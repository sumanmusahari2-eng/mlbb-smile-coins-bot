"""Telegram bot, using only the Bot API + stdlib (no pip packages).

Flow for 'users pay with Smile Coins they already have':
  /start -> pick an MLBB pack -> enter MLBB ID -> enter Zone ID
  -> confirm nickname (demo role lookup; see README)
  -> order is created as PENDING, price shown in Smile Coins
  -> 'Pay with Smile Coins' step:
       * If SMILEONE_CHECKOUT_URL_TEMPLATE is set, the bot gives a
         Smile.one checkout link built from that template.
       * Otherwise it tells the user the order is pending and that
         Smile.one must confirm payment via the payment-notification
         endpoint before delivery. This is honest: the Smile.one Docs
         do NOT publish a direct Smile Coins debit URL, so a checkout
         link / endpoint registration must be confirmed with
         Smile.one technical staff (see README).
  -> When payment-notification with SUCCESS arrives, the user gets a
     Telegram delivery message via notify_delivered().

Wallet commands (internal Smile Coins balance, see README):
  /balance              show your Smile Coins balance
  /addfund <amount>     add funds - test mode credits instantly,
                        live mode creates a request for admin approval
  /fund <amount>        same as /addfund
  /approvefund <id>     admin only, approve a pending fund request
  /pendingfunds         admin only, list pending fund requests

If a user has enough wallet balance at checkout, the pack price is
deducted and the order is marked delivered (delivery hook is still a
stub - see README). If not, the bot tells them how much more to add.

State is kept per chat in memory + in SQLite orders / users / funds.
"""
import json
import re
import time
import urllib.parse
import urllib.request

import store
from merchant_server import MerchantApp

API = "https://api.telegram.org/bot"

# Region / currency codes for region-separated ordering.
# e.g. /brl 603803877 5674 86  -> Brazil, MLBB ID 603803877, Zone 5674, 86 diamonds
# BRAZIL ONLY, for now (2026-10-26). Other regions were removed from the
# bot on the user's instruction - re-add them here only when their
# official packs/prices have been checked.
REGIONS = {
    "brl": ("Brazil", "BRL"), "br": ("Brazil", "BRL"),
}


class Bot:
    def __init__(self, token, conn, config, merchant_app: MerchantApp):
        self.token = token
        self.conn = conn
        self.config = config
        self.merchant = merchant_app
        self.sessions = {}  # chat_id -> dict
        self.offset = 0
        raw_admins = config.get("admin_chat_ids", "") or ""
        self.admin_ids = {int(x) for x in str(raw_admins).split(",") if x.strip().isdigit()}
        self.auto_credit = str(config.get("fund_auto_credit_test", "true")).lower() == "true"
        self.admin_password = config.get("admin_password", "") or ""
        self.admin_sessions = set()  # chat_ids authenticated with /admin in this run

    def is_admin(self, chat_id):
        return chat_id in self.admin_ids or chat_id in self.admin_sessions

    def admin_menu_keyboard(self):
        return {"inline_keyboard": [
            [{"text": "📊 Stats", "callback_data": "admin:stats"},
             {"text": "👥 Users", "callback_data": "admin:users"}],
            [{"text": "💰 Funds", "callback_data": "admin:funds"},
             {"text": "📦 Orders", "callback_data": "admin:orders"}],
            [{"text": "💎 Packs", "callback_data": "admin:packs"}],
            [{"text": "⬅️ Back to main menu", "callback_data": "menu:main"}],
        ]}

    def show_admin_menu(self, chat_id):
        stats = store.get_stats(self.conn)
        return self.send(chat_id,
                         f"🛠 <b>Admin Panel</b>\n\n"
                         f"Users: {stats['users']}\n"
                         f"Orders: {stats['orders']} ({stats['delivered']} delivered)\n"
                         f"Pending funds: {stats['pending_funds']}\n"
                         f"Total wallet balance: {stats['total_balance']:.2f} BRL\n"
                         f"Total funded: {stats['total_funded']:.2f} BRL\n"
                         f"Total spent: {stats['total_spent']:.2f} BRL",
                         reply_markup=self.admin_menu_keyboard())

    def _balance_text(self, chat_id):
        bal = store.get_balance(self.conn, chat_id)
        return (f"💰 <b>Your wallet balance: {bal['balance']:.2f} BRL</b>\n"
                f"Total added: {bal['total_funded']:.2f} BRL\n"
                f"Total spent: {bal['total_spent']:.2f} BRL\n"
                "Note: test wallet only - see README for the real Smile Coin payment on Smile.one.")

    @staticmethod
    def _parse_amount(text):
        try:
            amount = float(text.strip())
            return amount if amount > 0 else None
        except Exception:
            return None

    def find_pack_by_diamonds(self, diamonds):
        for p in self.merchant.products:
            try:
                if int(p.get("diamonds", 0)) == int(diamonds):
                    return p
            except Exception:
                continue
        return None

    def send_confirm(self, chat_id, sess):
        """Shared confirm step used by menu flow and quick-order flow."""
        product = self.merchant.find_product(sess["pack"])
        role = self.merchant.verify_role(sess["uid"], sess["sid"])
        nick = role["nickname"] if role else "(not verified in demo mode - please double-check your ID/Zone)"
        kb = {"inline_keyboard": [
            [{"text": "✅ Confirm order", "callback_data": "confirm:yes"},
             {"text": "❌ Cancel", "callback_data": "confirm:no"}]]}
        sess["step"] = "confirm"
        currency = product.get("currency") or sess.get("currency", "BRL")
        region_line = ""
        if sess.get("region"):
            region_line = f"Region: {sess['region']} ({currency})\n"
        elif currency == "BRL":
            region_line = "Region: Brazil (BRL)\n"
        return self.send(
            chat_id,
            f"Please confirm:\n<b>{product['name']}</b>\n"
            f"{region_line}"
            f"MLBB ID: {sess['uid']}\nZone ID: {sess['sid']}\n"
            f"Nickname: {nick}\nPrice: {product['price']} {currency}",
            reply_markup=kb)

    # -- Telegram API helpers -------------------------------------------
    def api(self, method, payload):
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(API + self.token + "/" + method, data=data)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def send(self, chat_id, text, reply_markup=None):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        try:
            return self.api("sendMessage", payload)
        except Exception as e:
            print("[telegram] send failed:", e)

    def notify_delivered(self, order):
        chat_id = order.get("telegram_chat_id")
        if chat_id:
            self.send(chat_id,
                      f"✅ <b>Delivered!</b>\nOrder {order['game_order']}\n"
                      f"Product {order['productid']} for MLBB ID {order['uid']} "
                      f"(Zone {order['sid']}) has been delivered.")

    def set_commands(self):
        """Telegram Menu button (bottom-left) - shows these commands."""
        commands = [
            {"command": "start", "description": "Open the main menu"},
            {"command": "packs", "description": "View all MLBB packs and prices"},
            {"command": "balance", "description": "Check your BRL balance"},
            {"command": "addfund", "description": "Add funds - /addfund <amount>"},
            {"command": "orders", "description": "List your orders"},
            {"command": "admin", "description": "Admin panel - /admin <password>"},
            {"command": "help", "description": "How this bot works"},
        ]
        try:
            self.api("setMyCommands", {"commands": json.dumps(commands)})
            print("[telegram] menu commands set")
        except Exception as e:
            print("[telegram] setMyCommands failed:", e)

    def main_menu_keyboard(self):
        return {"inline_keyboard": [
            [{"text": "🎮 Top Up MLBB", "callback_data": "menu:topup"},
             {"text": "💎 Packs", "callback_data": "menu:packs"}],
            [{"text": "💰 Balance", "callback_data": "menu:balance"},
             {"text": "➕ Add Fund", "callback_data": "menu:fund"}],
            [{"text": "📦 My Orders", "callback_data": "menu:orders"},
             {"text": "❓ Help", "callback_data": "menu:help"}],
        ]}

    def packs_text(self):
        lines = ["💎 <b>MLBB Packs</b> (prices in BRL - official Brazil packs, see README):"]
        for p in self.merchant.products:
            lines.append(f"{p['name']} - {p['price']} coins")
        return "\n".join(lines)

    def do_addfund(self, chat_id, amount):
        if self.auto_credit:
            bal = store.credit_balance(self.conn, chat_id, amount)
            return self.send(chat_id,
                             f"✅ <b>Test mode:</b> {amount:.2f} BRL added instantly.\n"
                             f"New balance: {bal['balance']:.2f}\n\n"
                             "In live mode, turn off test auto-credit and an admin "
                             "must approve fund requests instead.")
        req_id = store.create_fund_request(self.conn, chat_id, amount)
        return self.send(chat_id,
                         f"Fund request <b>#{req_id}</b> created for {amount:.2f} BRL.\n"
                         "Status: PENDING - an admin must approve it with "
                         f"/approvefund {req_id} before it appears in your balance.")

    # -- flow ------------------------------------------------------------
    def start(self, chat_id):
        self.sessions[chat_id] = {"step": "menu"}
        self.send(chat_id,
                  "🎮 <b>MLBB Top-Up - Brazil only</b>\nPrices in BRL.\n\n\n"
                  "Choose from the menu below, or use the Menu button:\n"
                  "/balance, /addfund, /orders, /help",
                  reply_markup=self.main_menu_keyboard())

    def show_packs(self, chat_id, page=0):
        self.sessions[chat_id] = {"step": "pack", "pack_page": page}
        per_page = 8
        total_pages = (len(self.merchant.products) + per_page - 1) // per_page
        page = max(0, min(page, total_pages - 1))
        items = self.merchant.products[page * per_page:(page + 1) * per_page]
        rows = []
        for i in range(0, len(items), 2):
            row = [{"text": f"{p['name']} - {p['price']}",
                    "callback_data": f"pack:{p['pid']}"} for p in items[i:i + 2]]
            rows.append(row)
        nav = []
        if page > 0:
            nav.append({"text": "◀️ Prev", "callback_data": f"packpage:{page - 1}"})
        if page < total_pages - 1:
            nav.append({"text": "Next ▶️", "callback_data": f"packpage:{page + 1}"})
        if nav:
            rows.append(nav)
        rows.append([{"text": "⬅️ Back to menu", "callback_data": "menu:main"}])
        kb = {"inline_keyboard": rows}
        self.send(chat_id, f"Pick a pack: (page {page + 1}/{total_pages})",
                  reply_markup=kb)

    def show_fund_menu(self, chat_id):
        self.sessions[chat_id] = {"step": "fund_amount"}
        kb = {"inline_keyboard": [
            [{"text": "50 coins", "callback_data": "fundamount:50"},
             {"text": "100 coins", "callback_data": "fundamount:100"}],
            [{"text": "200 coins", "callback_data": "fundamount:200"},
             {"text": "500 coins", "callback_data": "fundamount:500"}],
            [{"text": "⬅️ Back to menu", "callback_data": "menu:main"}],
        ]}
        self.send(chat_id,
                  "How much do you want to add?\nTap an amount, or just send a number here.",
                  reply_markup=kb)

    def handle_message(self, chat_id, text):
        sess = self.sessions.get(chat_id)
        # Admin panel: /admin or /admin <password>
        if text.startswith("/admin"):
            if self.is_admin(chat_id):
                return self.show_admin_menu(chat_id)
            parts = text.split()
            if len(parts) >= 2 and self.admin_password and parts[1] == self.admin_password:
                self.admin_sessions.add(chat_id)
                return self.show_admin_menu(chat_id)
            if not self.admin_password and not self.admin_ids:
                return self.send(chat_id, "Admin panel is not configured. Set ADMIN_PASSWORD or ADMIN_CHAT_IDS.")
            if len(parts) >= 2:
                return self.send(chat_id, "Wrong admin password. Try /admin <password> again.")
            self.sessions[chat_id] = {"step": "admin_password"}
            return self.send(chat_id, "Send your admin password now (or /admin <password>).")
        # Admin password typed as a plain message (after /admin with no password)
        if sess and sess.get("step") == "admin_password":
            if self.admin_password and text.strip() == self.admin_password:
                self.admin_sessions.add(chat_id)
                return self.show_admin_menu(chat_id)
            self.sessions.pop(chat_id, None)
            return self.send(chat_id, "Wrong admin password.")
        # Admin adjust balance: amount typed after tapping Adjust on a user
        if sess and sess.get("step") == "admin_adjust" and self.is_admin(chat_id):
            try:
                delta = float(text.strip())
            except Exception:
                return self.send(chat_id, "Send a number, e.g. 100 to add, or -50 to deduct.")
            target = sess.pop("admin_adjust_user", None)
            result = store.adjust_balance(self.conn, target, delta) if target else None
            if result is None:
                return self.send(chat_id, "Could not adjust - not enough balance to deduct that much.")
            return self.send(chat_id, f"User {target} balance is now {result['balance']:.2f} BRL.")
        # Region quick order: /<region> <MLBB ID> <Zone ID> <diamonds>
        # e.g. /brl 603803877 5674 86
        rm = re.match(r"^/([a-z]{2,3})(?:\s+(\S+))?(?:\s+(\S+))?(?:\s+(\S+))?\s*$", text.strip().lower())
        if rm and rm.group(1) not in REGIONS and rm.group(2):
            regions = ", ".join(sorted({v[1].lower() for v in REGIONS.values()}))
            return self.send(chat_id,
                             f"Unknown region '{rm.group(1)}'.\n"
                             f"Supported regions: {regions}\n"
                             "Example: /brl 603803877 5674 86")
        if rm and rm.group(1) in REGIONS:
            region_name, currency = REGIONS[rm.group(1)]
            uid, sid, diamonds_arg = rm.group(2), rm.group(3), rm.group(4)
            sess = {"region": region_name, "currency": currency, "step": "uid"}
            self.sessions[chat_id] = sess
            if diamonds_arg:
                if not diamonds_arg.isdigit():
                    return self.send(chat_id, "Diamonds must be a number, e.g. /brl 603803877 5674 86")
                product = self.find_pack_by_diamonds(int(diamonds_arg))
                if not product:
                    return self.send(chat_id, f"No pack for {diamonds_arg} diamonds. Use /packs to see the available packs.")
                sess["pack"] = product["pid"]
            if uid and sid and sess.get("pack"):
                sess["uid"], sess["sid"] = uid, sid
                return self.send_confirm(chat_id, sess)
            if uid and sid:
                sess["uid"], sess["sid"] = uid, sid
                sess["step"] = "region_pack"
                return self.send(chat_id, "Now send the diamonds amount, e.g. 86")
            if uid:
                sess["uid"] = uid
                sess["step"] = "sid"
                return self.send(chat_id, "Now send your Zone ID (server).")
            sess["step"] = "uid"
            return self.send(chat_id, f"Region: {region_name} ({currency}).\nSend your MLBB ID.")
        # Quick order: /<diamonds> <MLBB ID> <Zone ID>
        # e.g. /86 603900379 7654
        # Also works partially: /86 603900379  (then bot asks for Zone)
        # or /86 alone (then bot asks for ID, then Zone).
        # A Confirm tap is still required before any balance is deducted,
        # so a typo in the ID does not instantly spend coins.
        m = re.match(r"^/(\d+)(?:\s+(\S+))?(?:\s+(\S+))?\s*$", text.strip())
        if m:
            diamonds = int(m.group(1))
            product = self.find_pack_by_diamonds(diamonds)
            if not product:
                return self.send(chat_id,
                                 f"No pack for {diamonds} diamonds. "
                                 "Use /packs to see the available packs.")
            sess = {"pack": product["pid"], "step": "uid"}
            self.sessions[chat_id] = sess
            if m.group(2) and m.group(3):
                sess["uid"], sess["sid"] = m.group(2), m.group(3)
                return self.send_confirm(chat_id, sess)
            if m.group(2):
                sess["uid"] = m.group(2)
                sess["step"] = "sid"
                return self.send(chat_id, "Now send your Zone ID (server).")
            sess["step"] = "uid"
            return self.send(chat_id, f"Pack: {product['name']} - {product['price']} BRL.\nSend your MLBB ID.")
        if text.startswith("/start"):
            return self.start(chat_id)
        if text.startswith("/packs"):
            self.send(chat_id, self.packs_text())
            return self.send(chat_id, "Want to buy one? Tap Top Up below:",
                              reply_markup=self.main_menu_keyboard())
        if text.startswith("/balance"):
            return self.send(chat_id, self._balance_text(chat_id))
        if text.startswith("/addfund") or text.startswith("/fund"):
            parts = text.split()
            if len(parts) < 2:
                return self.send(chat_id, "Usage: /addfund <amount>\nExample: /addfund 100")
            amount = self._parse_amount(parts[1])
            if amount is None:
                return self.send(chat_id, "Please send a positive amount, e.g. /addfund 100")
            return self.do_addfund(chat_id, amount)
        if text.startswith("/approvefund"):
            if not self.admin_ids:
                return self.send(chat_id, "No admin is configured. Set ADMIN_CHAT_IDS to use approvals.")
            if chat_id not in self.admin_ids:
                return self.send(chat_id, "Only an admin can approve fund requests.")
            parts = text.split()
            if len(parts) < 2 or not parts[1].isdigit():
                return self.send(chat_id, "Usage: /approvefund <request_id>")
            req = store.approve_fund_request(self.conn, int(parts[1]))
            if not req:
                return self.send(chat_id, "Request not found or already handled.")
            self.send(req["telegram_chat_id"],
                      f"✅ Your fund request #{req['id']} for {req['amount']:.2f} BRL was approved.")
            return self.send(chat_id, f"Approved request #{req['id']} for {req['amount']:.2f} BRL.")
        if text.startswith("/pendingfunds"):
            if not self.admin_ids or chat_id not in self.admin_ids:
                return self.send(chat_id, "Only an admin can view pending fund requests.")
            pending = store.list_pending_funds(self.conn)
            if not pending:
                return self.send(chat_id, "No pending fund requests.")
            lines = [f"#{r['id']} - {r['amount']:.2f} coins - chat {r['telegram_chat_id']}"
                     for r in pending]
            return self.send(chat_id, "Pending fund requests:\n" + "\n".join(lines))
        if text.startswith("/orders"):
            orders = store.list_orders(self.conn, chat_id)
            if not orders:
                return self.send(chat_id, "No orders yet. Use /start to top up.")
            lines = [f"{o['game_order']} - {o['productid']} - {o['status']}"
                     for o in orders]
            return self.send(chat_id, "Your orders:\n" + "\n".join(lines))
        if text.startswith("/help"):
            return self.send(chat_id,
                             "Commands:\n"
                             "/start - open the menu\n"
                             "/packs - list all packs\n"
                             "/<diamonds> <MLBB ID> <Zone ID> - quick order, e.g. /86 603900379 7654\n"
                             "/<region> <MLBB ID> <Zone ID> <diamonds> - region order, e.g. /brl 603803877 5674 86\n"
                             "/balance - check your BRL balance\n"
                             "/addfund <amount> - add funds to your wallet\n"
                             "/orders - list your orders\n\n"
                             "Use /start to pick a pack, then send your MLBB ID "
                             "and Zone ID. If your wallet balance covers the "
                             "price, it is deducted at checkout. Otherwise, "
                             "add funds first, or pay via Smile.one if a "
                             "checkout link is configured.")
        if not sess:
            return self.start(chat_id)
        if sess["step"] == "fund_amount":
            amount = self._parse_amount(text)
            if amount is None:
                return self.send(chat_id, "Please send a positive amount, e.g. 100")
            sess["step"] = "menu"
            return self.do_addfund(chat_id, amount)
        if sess["step"] == "region_pack":
            if not text.strip().isdigit():
                return self.send(chat_id, "Please send the diamonds amount as a number, e.g. 86")
            product = self.find_pack_by_diamonds(int(text.strip()))
            if not product:
                return self.send(chat_id, "No pack for that amount. Use /packs to see the available packs.")
            sess["pack"] = product["pid"]
            return self.send_confirm(chat_id, sess)
        if sess["step"] == "uid":
            sess["uid"] = text.strip()
            sess["step"] = "sid"
            return self.send(chat_id, "Now send your Zone ID (the number in brackets after your MLBB ID, e.g. 1234).")
        if sess["step"] == "sid":
            sess["sid"] = text.strip()
            if not sess.get("pack"):
                sess["step"] = "region_pack"
                return self.send(chat_id, "Now send the diamonds amount, e.g. 86")
            return self.send_confirm(chat_id, sess)
        return self.send(chat_id, "Use /start to begin a new top-up.")

    def handle_callback(self, chat_id, data):
        sess = self.sessions.setdefault(chat_id, {})
        # --- Admin panel callbacks ---
        if data.startswith("admin:"):
            if not self.is_admin(chat_id):
                return self.send(chat_id, "Admin access only. Send /admin first.")
            if data == "admin:menu":
                return self.show_admin_menu(chat_id)
            if data == "admin:stats":
                stats = store.get_stats(self.conn)
                self.send(chat_id,
                          f"📊 <b>Stats</b>\nUsers: {stats['users']}\nOrders: {stats['orders']} "
                          f"({stats['delivered']} delivered)\nPending funds: {stats['pending_funds']}\n"
                          f"Total balance: {stats['total_balance']:.2f} BRL\n"
                          f"Total funded: {stats['total_funded']:.2f} BRL\n"
                          f"Total spent: {stats['total_spent']:.2f} BRL")
                return self.send(chat_id, "Back to admin:", reply_markup=self.admin_menu_keyboard())
            if data == "admin:users":
                users = store.list_users(self.conn, 20)
                if not users:
                    self.send(chat_id, "No users yet.")
                else:
                    rows = [[{"text": f"User {u['telegram_chat_id']} - {u['balance']:.2f} BRL",
                              "callback_data": f"admin:user:{u['telegram_chat_id']}"}] for u in users]
                    rows.append([{"text": "⬅️ Admin menu", "callback_data": "admin:menu"}])
                    self.send(chat_id, "👥 Users (tap one for details):",
                              reply_markup={"inline_keyboard": rows})
                return None
            if data.startswith("admin:user:"):
                target = int(data.split(":", 2)[2])
                bal = store.get_balance(self.conn, target)
                orders = store.list_orders(self.conn, target, 5)
                lines = [f"{o['game_order']} - {o['productid']} - {o['status']}" for o in orders]
                kb = {"inline_keyboard": [
                    [{"text": "➕/➖ Adjust balance", "callback_data": f"admin:adjust:{target}"}],
                    [{"text": "⬅️ Users", "callback_data": "admin:users"},
                     {"text": "⬅️ Admin menu", "callback_data": "admin:menu"}],
                ]}
                return self.send(chat_id,
                                 f"👤 User {target}\nBalance: {bal['balance']:.2f} BRL\n"
                                 f"Total funded: {bal['total_funded']:.2f} BRL\n"
                                 f"Total spent: {bal['total_spent']:.2f} BRL\n\n"
                                 "Recent orders:\n" + ("\n".join(lines) if lines else "None"),
                                 reply_markup=kb)
            if data.startswith("admin:adjust:"):
                target = int(data.split(":", 2)[2])
                self.sessions[chat_id] = {"step": "admin_adjust", "admin_adjust_user": target}
                return self.send(chat_id, f"Send the amount to adjust for user {target}:\n100 to add, -50 to deduct.")
            if data == "admin:funds":
                pending = store.list_pending_funds(self.conn)
                if not pending:
                    self.send(chat_id, "No pending fund requests.")
                else:
                    rows = []
                    for r in pending:
                        rows.append([{"text": f"#{r['id']} {r['amount']:.2f} BRL - user {r['telegram_chat_id']}",
                                      "callback_data": f"admin:fund:{r['id']}"}])
                    rows.append([{"text": "⬅️ Admin menu", "callback_data": "admin:menu"}])
                    self.send(chat_id, "💰 Pending funds (tap one):",
                              reply_markup={"inline_keyboard": rows})
                return None
            if data.startswith("admin:fund:"):
                req_id = int(data.split(":", 2)[2])
                req = store.get_fund_request(self.conn, req_id)
                if not req:
                    return self.send(chat_id, "Fund request not found.")
                kb = {"inline_keyboard": [
                    [{"text": "✅ Approve", "callback_data": f"admin:approve:{req_id}"},
                     {"text": "❌ Reject", "callback_data": f"admin:reject:{req_id}"}],
                    [{"text": "⬅️ Funds", "callback_data": "admin:funds"}],
                ]}
                return self.send(chat_id,
                                 f"Fund request #{req_id}\nUser: {req['telegram_chat_id']}\n"
                                 f"Amount: {req['amount']:.2f} BRL\nStatus: {req['status']}",
                                 reply_markup=kb)
            if data.startswith("admin:approve:"):
                req_id = int(data.split(":", 2)[2])
                req = store.approve_fund_request(self.conn, req_id)
                if not req:
                    return self.send(chat_id, "Request not found or already handled.")
                self.send(req["telegram_chat_id"],
                          f"✅ Your fund request #{req['id']} for {req['amount']:.2f} BRL was approved.")
                return self.send(chat_id, f"Approved request #{req['id']}.")
            if data.startswith("admin:reject:"):
                req_id = int(data.split(":", 2)[2])
                req = store.reject_fund_request(self.conn, req_id)
                if not req:
                    return self.send(chat_id, "Request not found or already handled.")
                self.send(req["telegram_chat_id"],
                          f"❌ Your fund request #{req['id']} for {req['amount']:.2f} BRL was rejected.")
                return self.send(chat_id, f"Rejected request #{req['id']}.")
            if data == "admin:orders":
                orders = store.list_all_orders(self.conn, 20)
                if not orders:
                    self.send(chat_id, "No orders yet.")
                else:
                    rows = [[{"text": f"{o['game_order']} - {o['productid']} - {o['status']}",
                              "callback_data": f"admin:order:{o['game_order']}"}] for o in orders]
                    rows.append([{"text": "⬅️ Admin menu", "callback_data": "admin:menu"}])
                    self.send(chat_id, "📦 Recent orders (tap one):",
                              reply_markup={"inline_keyboard": rows})
                return None
            if data.startswith("admin:order:"):
                order_id = data.split(":", 2)[2]
                order = store.get_order(self.conn, order_id)
                if not order:
                    return self.send(chat_id, "Order not found.")
                kb = {"inline_keyboard": [
                    [{"text": "⬅️ Orders", "callback_data": "admin:orders"},
                     {"text": "⬅️ Admin menu", "callback_data": "admin:menu"}],
                ]}
                return self.send(chat_id,
                                 f"📦 Order {order['game_order']}\nUser chat: {order['telegram_chat_id']}\n"
                                 f"MLBB ID: {order['uid']} / Zone: {order['sid']}\n"
                                 f"Product: {order['productid']} - {order['amount']} {order['currency']}\n"
                                 f"Status: {order['status']} / Trade: {order['trade_no']}",
                                 reply_markup=kb)
            if data == "admin:packs":
                self.send(chat_id, self.packs_text())
                return self.send(chat_id, "Back to admin:", reply_markup=self.admin_menu_keyboard())
            return None
        if data == "menu:main":
            return self.start(chat_id)
        if data == "menu:topup":
            return self.show_packs(chat_id, 0)
        if data == "menu:packs":
            self.send(chat_id, self.packs_text())
            return self.send(chat_id, "Back to menu:", reply_markup=self.main_menu_keyboard())
        if data.startswith("packpage:"):
            try:
                page = int(data.split(":", 1)[1])
            except Exception:
                page = 0
            return self.show_packs(chat_id, page)
        if data == "menu:balance":
            self.send(chat_id, self._balance_text(chat_id))
            return self.send(chat_id, "Back to menu:", reply_markup=self.main_menu_keyboard())
        if data == "menu:fund":
            return self.show_fund_menu(chat_id)
        if data.startswith("fundamount:"):
            amount = self._parse_amount(data.split(":", 1)[1])
            sess["step"] = "menu"
            return self.do_addfund(chat_id, amount)
        if data == "menu:orders":
            orders = store.list_orders(self.conn, chat_id)
            if not orders:
                self.send(chat_id, "No orders yet. Use Top Up from the menu.")
            else:
                lines = [f"{o['game_order']} - {o['productid']} - {o['status']}"
                         for o in orders]
                self.send(chat_id, "Your orders:\n" + "\n".join(lines))
            return self.send(chat_id, "Back to menu:", reply_markup=self.main_menu_keyboard())
        if data == "menu:help":
            self.send(chat_id,
                      "🎮 Top Up: pick a pack, enter MLBB ID + Zone ID, confirm.\n"
                      "💰 Balance: check your BRL wallet.\n"
                      "➕ Add Fund: add coins to your wallet, then top up.\n"
                      "📦 My Orders: see your past orders.")
            return self.send(chat_id, "Back to menu:", reply_markup=self.main_menu_keyboard())
        if data.startswith("pack:"):
            sess["pack"] = data.split(":", 1)[1]
            sess["step"] = "uid"
            return self.send(chat_id, "Send your MLBB ID (numbers only).")
        if data == "confirm:yes":
            product = self.merchant.find_product(sess.get("pack"))
            price = float(product["price"])
            currency = product.get("currency") or sess.get("currency", "BRL")
            bal = store.get_balance(self.conn, chat_id)
            # Wallet-first checkout: deduct if the user already has enough.
            # Test mode: numeric amount is deducted from the wallet balance
            # regardless of region currency - region prices are placeholders.
            if bal["balance"] >= price - 1e-9:
                store.deduct_balance(self.conn, chat_id, price)
                order_id = store.create_order(
                    self.conn, chat_id, sess["uid"], sess["sid"], sess["pack"],
                    product["price"], currency)
                order = store.get_order(self.conn, order_id)
                self.merchant.deliver_items(order)
                store.update_order(self.conn, order_id, "DELIVERED", "WALLET")
                new_bal = store.get_balance(self.conn, chat_id)
                sess["step"] = "done"
                region_line = f"Region: {sess['region']} ({currency})\n" if sess.get("region") else ""
                return self.send(chat_id,
                                 f"✅ <b>Paid from your wallet and delivered!</b>\n"
                                 f"Order {order_id}\n{region_line}{product['name']} for MLBB ID "
                                 f"{sess['uid']} (Zone {sess['sid']})\n"
                                 f"Paid: {price:.2f} {currency}\n"
                                 f"Remaining balance: {new_bal['balance']:.2f}\n\n"
                                 "Note: delivery is via the bot's delivery hook, "
                                 "which is still a stub in this test version.")
            order_id = store.create_order(
                self.conn, chat_id, sess["uid"], sess["sid"], sess["pack"],
                product["price"], currency)
            sess["step"] = "done"
            template = self.config.get("checkout_url_template", "")
            if not template:
                shortfall = price - bal["balance"]
                return self.send(chat_id,
                                 f"⚠️ Not enough balance.\n"
                                 f"Price: {price:.2f} {currency}\n"
                                 f"Your balance: {bal['balance']:.2f}\n"
                                 f"You need {shortfall:.2f} more.\n\n"
                                 f"Add funds with: /addfund {shortfall:.2f}\n"
                                 "Then use /start again to place the order.\n"
                                 f"(A pending order {order_id} was also created, "
                                 "but it will not be delivered until paid.)")
            if template:
                link = template.format(game_order=order_id, uid=sess["uid"],
                                       sid=sess["sid"], productid=sess["pack"],
                                       amount=product["price"])
                msg = (f"Order <b>{order_id}</b> created.\n"
                       f"Pay with your Smile Coins here:\n{link}\n\n"
                       "Delivery happens automatically once Smile.one "
                       "confirms SUCCESS.")
            else:
                msg = (f"Order <b>{order_id}</b> created and pending.\n\n"
                       "To pay with Smile Coins, complete payment for this "
                       "order on Smile.one (your merchant product page). "
                       "Once Smile.one sends a SUCCESS payment notification "
                       "to this bot's merchant API, your diamonds will be "
                       "delivered and you will get a message here.\n\n"
                       "Note: set SMILEONE_CHECKOUT_URL_TEMPLATE to get a "
                       "direct pay link in this message.")
            return self.send(chat_id, msg)
        if data == "confirm:no":
            self.sessions.pop(chat_id, None)
            return self.start(chat_id)
        return None

    def handle_update(self, upd):
        """Handle one Telegram update dict - used by both polling and webhook mode."""
        msg = upd.get("message") or {}
        cb = upd.get("callback_query") or {}
        if msg and "text" in msg:
            self.handle_message(msg["chat"]["id"], msg["text"])
        elif cb:
            chat_id = cb["message"]["chat"]["id"]
            self.handle_callback(chat_id, cb["data"])
            try:
                self.api("answerCallbackQuery", {"callback_query_id": cb["id"]})
            except Exception:
                pass

    def poll_once(self):
        res = self.api("getUpdates", {"offset": self.offset, "timeout": 20})
        for upd in res.get("result", []):
            self.offset = upd["update_id"] + 1
            self.handle_update(upd)

    def run(self):
        print("[telegram] polling started")
        self.set_commands()
        while True:
            try:
                self.poll_once()
            except Exception as e:
                print("[telegram] poll error:", e)
                time.sleep(3)
