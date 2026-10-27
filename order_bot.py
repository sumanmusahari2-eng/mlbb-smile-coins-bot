"""Telegram ordering bot, built from the Smile.one Docs order lifecycle,
now multi-region like the reference reseller bot (BR + PH).

Docs model (docs.smile.one: "APIs and parameters that merchants need to
provide to Smile One"):
  1. Product list  -> the merchant's packs; the customer picks one.
  2. Role check    -> uid (+ sid/zone) identifies the player.
  3. Game order    -> an order is created, identified by game_order.
  4. Payment       -> a payment notification with trade_status SUCCESS
                      confirms the order is paid.
  5. Delivery      -> the merchant delivers the items, order = DELIVERED.

Customer commands:
  /start  -> welcome + per-region balances + order examples + menu
  /getid  -> your Telegram account ID
  /help   -> all commands list
  /packs  -> list all packs and prices
  /orders -> list your orders and their status
  /balance -> per-region wallet balances
  /br <uid> <sid> <diamonds> [more triplets...] -> quick order, BR
  /ph <uid> <sid> <diamonds> [more triplets...] -> quick order, PH
  /addfund [region] <amount> -> test-mode credit only (see below)
  /admin  -> admin panel (stats + pack prices, ADMIN_CHAT_IDS only)

Bulk: one /br or /ph command can carry up to 10 orders (triplets of
uid + sid + diamonds). The bot validates every triplet first, shows
one summary with a total, and charges the region wallet once on
confirm. Anything invalid cancels the whole batch — no partial orders.

Menu flow: /start -> Top Up -> pick region -> pick pack -> send MLBB
ID -> send Zone ID -> confirm -> pay from that region's wallet.

Order states: PENDING -> PAID -> DELIVERED.
  * Wallet payment deducts the region balance and moves orders to PAID.
  * A Smile.one payment notification (sign-verified) also moves an
    order to PAID.
  * Delivery is a pluggable hook (deliver=...). The shipped default
    does NOT pretend to deliver: it logs the request and returns
    False, so a paid order stays PAID and the customer is told the
    merchant will complete delivery. Wire a real supplier call here
    and paid orders become DELIVERED automatically, with a
    confirmation message to the customer.

Honest limits, stated in the code and in the messages, not hidden:
  * Nickname lookup: the docs do not publish a merchant-to-Smile.one
    role-query API, so this bot does NOT fake a nickname. The confirm
    screen shows the exact MLBB ID + Zone ID back and the customer
    confirms them.
  * /addfund is TEST ONLY while auto_credit_test is on: it credits
    instantly so the order flow can be tested with no payment rail.
    Turn it off before any real use.
  * No checkout URL is invented: the docs publish no direct
    Smile Coins debit URL, so this bot never shows a fake pay link.
  * PH packs are only offered once their official list is verified.
    Until then /ph and the PH menu answer that PH is not available
    yet — no invented PH prices, ever.

Wallets: one wallet per region (store.region_balances). The old
single wallet balance carries into BR exactly once, on first access.

Stdlib only. No token or network is needed to test: pass a fake
sender= callable and the whole flow runs against an in-memory DB
(see test_order_bot.py).
"""
import json
import re
import time
import urllib.parse
import urllib.request

import store
from merchant_server import MerchantApp

API = "https://api.telegram.org/bot"
PER_PAGE = 8
BULK_MAX = 10

REGIONS = {
    "BR": {"name": "Brazil", "flag": "🇧🇷", "currency": "BRL"},
    "PH": {"name": "Philippines", "flag": "🇵🇭", "currency": "PHP"},
}
CURRENCY_REGION = {"BRL": "BR", "PHP": "PH"}


def _kb(rows):
    return {"inline_keyboard": rows}


class OrderMerchantApp(MerchantApp):
    """MerchantApp whose delivery hook is injectable.

    The base class deliver_items() only logs and returns True, which
    would mark orders DELIVERED without delivering anything. This
    subclass routes delivery through the bot's deliver hook instead,
    so an unwired hook keeps paid orders PAID (honest) instead.
    """

    def __init__(self, conn, config, deliver=None, on_delivered=None):
        super().__init__(conn, config, on_delivered=on_delivered)
        self._deliver = deliver

    def deliver_items(self, order):
        if self._deliver is not None:
            try:
                return bool(self._deliver(order))
            except Exception as e:
                print(f"[delivery] hook failed: {e}")
                return False
        print(f"[DELIVERY-NOT-CONFIGURED] order={order['game_order']} "
              f"product={order['productid']} uid={order['uid']} "
              f"sid={order['sid']} — merchant must deliver manually")
        return False


class OrderBot:
    def __init__(self, token, conn, products, sender=None, deliver=None,
                 auto_credit_test=False, admin_chat_ids="", merchant=None,
                 regions_products=None, save_region=None):
        self.token = (token or "").strip()
        self.conn = conn
        # products = BR list (backward compatible). regions_products
        # overrides per region when given; BR must be the same list
        # object as merchant.products so admin price saves stick.
        self.products = products
        self.regions_products = regions_products or {"BR": products}
        self.save_region = save_region
        self.sender = sender  # fake sender for tests: fn(chat_id, text, kb)
        self.deliver = deliver
        self.auto_credit_test = auto_credit_test
        self.merchant = merchant  # OrderMerchantApp, for BR price saves
        raw_admins = admin_chat_ids or ""
        self.admin_ids = {int(x) for x in str(raw_admins).split(",")
                          if x.strip().isdigit()}
        self.sessions = {}  # chat_id -> dict
        self.offset = 0

    def is_admin(self, chat_id):
        return chat_id in self.admin_ids

    # -- products ------------------------------------------------------
    def region_products(self, region):
        return self.regions_products.get(region, [])

    def find_product(self, pid, region="BR"):
        for p in self.region_products(region):
            if str(p["pid"]) == str(pid):
                return p
        return None

    def find_pack_by_diamonds(self, diamonds, region="BR"):
        for p in self.region_products(region):
            try:
                if int(p.get("diamonds", 0)) == int(diamonds):
                    return p
            except Exception:
                continue
        return None

    def packs_text(self, region="BR"):
        info = REGIONS[region]
        lines = [f"💎 <b>MLBB Packs — {info['flag']} {info['name']}</b>:"]
        for p in self.region_products(region):
            lines.append(f"{p['name']} - {p['price']} {info['currency']}")
        return "\n".join(lines)

    def _save_region(self, region):
        if region == "BR" and self.merchant is not None:
            return self.merchant.save_products()
        if self.save_region is not None:
            return self.save_region(region)
        return False

    # -- balances --------------------------------------------------------
    def balance_lines(self, chat_id):
        lines = []
        for code in ("BR", "PH"):
            info = REGIONS[code]
            bal = store.get_region_balance(self.conn, chat_id, code)
            lines.append(f"Balance {info['flag']} [{code}]: "
                         f"{bal['balance']:.2f} {info['currency']}")
        return lines

    # -- Telegram transport --------------------------------------------
    def api(self, method, payload):
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(API + self.token + "/" + method, data=data)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def send(self, chat_id, text, reply_markup=None):
        if self.sender is not None:
            return self.sender(chat_id, text, reply_markup)
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        # Retry with backoff: PythonAnywhere's free outbound proxy
        # intermittently answers 503, which used to drop replies silently.
        for attempt in (1, 2, 3, 4, 5):
            try:
                return self.api("sendMessage", payload)
            except Exception as e:
                if attempt < 5:
                    print(f"[telegram] send attempt {attempt} failed: {e}")
                    time.sleep(2 ** (attempt - 1))  # 1s, 2s, 4s, 8s
                else:
                    print("[telegram] send failed:", e)

    def set_commands(self):
        """Telegram Menu button (bottom-left) - shows these commands.

        Telegram shows ONE command list to everyone by default, so the
        admin command must NOT be in the default list — otherwise every
        user sees "Admin panel" in their menu (the panel itself stays
        gated: non-admins get "Admin access only"). Admins get their
        own per-chat menu (BotCommandScopeChat) that includes /admin.
        """
        if self.sender is not None:
            return
        default = [
            {"command": "start", "description": "Welcome + balances"},
            {"command": "getid", "description": "Your account ID"},
            {"command": "packs", "description": "View all MLBB packs"},
            {"command": "orders", "description": "List your orders"},
            {"command": "balance", "description": "Per-region balances"},
            {"command": "br", "description": "Quick order BR: /br uid zone diamonds"},
            {"command": "ph", "description": "Quick order PH: /ph uid zone diamonds"},
            {"command": "help", "description": "All commands"},
        ]
        try:
            self.api("setMyCommands", {"commands": json.dumps(default)})
            print("[order-bot] menu commands set")
        except Exception as e:
            print("[order-bot] setMyCommands failed:", e)
        scoped = default + [{"command": "admin",
                             "description": "Admin panel"}]
        for admin_id in sorted(self.admin_ids):
            try:
                self.api("setMyCommands", {
                    "commands": json.dumps(scoped),
                    "scope": json.dumps({"type": "chat",
                                         "chat_id": admin_id})})
                print(f"[order-bot] admin menu set for {admin_id}")
            except Exception as e:
                print(f"[order-bot] admin menu failed for {admin_id}: {e}")

    # -- menus -----------------------------------------------------------
    def main_menu(self):
        return _kb([
            [{"text": "🎮 Top Up MLBB", "callback_data": "menu:topup"},
             {"text": "💎 Packs", "callback_data": "menu:packs"}],
            [{"text": "💰 Balance", "callback_data": "menu:balance"},
             {"text": "📦 My Orders", "callback_data": "menu:orders"}],
            [{"text": "🆔 My ID", "callback_data": "menu:getid"},
             {"text": "❓ Help", "callback_data": "menu:help"}],
        ])

    def region_menu(self):
        return _kb([
            [{"text": "🇧🇷 Brazil (BR)", "callback_data": "region:BR"},
             {"text": "🇵🇭 Philippines (PH)", "callback_data": "region:PH"}],
            [{"text": "⬅️ Back to menu", "callback_data": "menu:main"}],
        ])

    def start(self, chat_id):
        self.sessions[chat_id] = {"step": "menu"}
        lines = ["👋 <b>Welcome!</b>\n",
                 "<b>Your Current Balance</b>"]
        lines.extend(self.balance_lines(chat_id))
        lines.append(
            "\n💎 <b>Diamond Topup Example</b>\n"
            "For BR\n/br &lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt;\n"
            "For PH\n/ph &lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt;\n"
            "For Bulk (10 max)\n"
            "/br &lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt; "
            "&lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt; ...\n\n"
            "Or tap Top Up below and pick a pack.")
        return self.send(chat_id, "\n".join(lines),
                         reply_markup=self.main_menu())

    def show_packs(self, chat_id, region, page=0):
        products = self.region_products(region)
        total_pages = max(1, (len(products) + PER_PAGE - 1) // PER_PAGE)
        page = max(0, min(page, total_pages - 1))
        self.sessions[chat_id] = {"step": "pack", "region": region,
                                  "pack_page": page}
        items = products[page * PER_PAGE:(page + 1) * PER_PAGE]
        rows = []
        for i in range(0, len(items), 2):
            rows.append([{"text": f"{p['name']} - {p['price']}",
                          "callback_data": f"pack:{region}:{p['pid']}"}
                         for p in items[i:i + 2]])
        nav = []
        if page > 0:
            nav.append({"text": "◀️ Prev",
                        "callback_data": f"packpage:{region}:{page - 1}"})
        if page < total_pages - 1:
            nav.append({"text": "Next ▶️",
                        "callback_data": f"packpage:{region}:{page + 1}"})
        if nav:
            rows.append(nav)
        rows.append([{"text": "⬅️ Regions", "callback_data": "menu:topup"},
                     {"text": "⬅️ Menu", "callback_data": "menu:main"}])
        return self.send(chat_id,
                         f"Pick a pack ({REGIONS[region]['flag']} "
                         f"{REGIONS[region]['name']}, "
                         f"page {page + 1}/{total_pages}):",
                         reply_markup=_kb(rows))

    # -- order flow ------------------------------------------------------
    def send_confirm(self, chat_id, sess):
        product = self.find_product(sess["pack"], sess["region"])
        sess["step"] = "confirm"
        kb = _kb([
            [{"text": "✅ Confirm & pay", "callback_data": "confirm:yes"},
             {"text": "❌ Cancel", "callback_data": "confirm:no"}]])
        info = REGIONS[sess["region"]]
        return self.send(
            chat_id,
            "Please check carefully, then confirm:\n"
            f"<b>{product['name']}</b>\n"
            f"Region: {info['flag']} {info['name']}\n"
            f"MLBB ID: {sess['uid']}\nZone ID: {sess['sid']}\n"
            f"Price: {product['price']} {info['currency']}\n\n"
            "Make sure the ID and Zone are correct — diamonds go to "
            "exactly this account.",
            reply_markup=kb)

    def _attempt_delivery(self, order):
        """Run the delivery hook; on success mark DELIVERED + notify."""
        ok = False
        if self.deliver is not None:
            try:
                ok = bool(self.deliver(order))
            except Exception as e:
                print(f"[delivery] hook failed: {e}")
        if not ok:
            print(f"[DELIVERY-NOT-CONFIGURED] order={order['game_order']} "
                  f"product={order['productid']} uid={order['uid']} "
                  f"sid={order['sid']} — merchant must deliver manually")
            return False
        store.update_order(self.conn, order["game_order"], "DELIVERED", "")
        self.notify_delivered(store.get_order(self.conn, order["game_order"]))
        return True

    def pay_batch(self, chat_id, sess):
        """Charge the region wallet once for a batch (1..10 orders)."""
        region = sess["region"]
        info = REGIONS[region]
        currency = info["currency"]
        items = sess["batch"]  # [{product, uid, sid, price}]
        total = sum(it["price"] for it in items)
        bal = store.get_region_balance(self.conn, chat_id, region)
        if bal["balance"] < total - 1e-9:
            shortfall = total - bal["balance"]
            sess["step"] = "menu"
            return self.send(
                chat_id,
                f"⚠️ Not enough balance.\n"
                f"Total: {total:.2f} {currency}\n"
                f"Your balance: {bal['balance']:.2f} {currency}\n"
                f"You need {shortfall:.2f} more.\n\n"
                f"Add funds with: /addfund {region.lower()} {shortfall:.2f}\n"
                "Then place the order again.\n"
                "(No order was created.)")
        store.deduct_region_balance(self.conn, chat_id, region, total)
        order_ids = []
        delivered = 0
        for it in items:
            order_id = store.create_order(self.conn, chat_id, it["uid"],
                                          it["sid"], it["product"]["pid"],
                                          it["product"]["price"], currency)
            store.update_order(self.conn, order_id, "PAID", "WALLET")
            order = store.get_order(self.conn, order_id)
            if self._attempt_delivery(order):
                delivered += 1
            order_ids.append(order_id)
        new_bal = store.get_region_balance(self.conn, chat_id, region)
        sess["step"] = "done"
        lines = [f"✅ <b>Payment received.</b>",
                 f"Orders: {', '.join(order_ids)}",
                 f"Paid: {total:.2f} {currency}",
                 f"Remaining balance: {new_bal['balance']:.2f} {currency}"]
        if delivered == len(items):
            lines.append("All orders delivered! 🎉")
        elif delivered:
            lines.append(f"{delivered}/{len(items)} delivered; the rest "
                         "will be completed by the merchant shortly.")
        else:
            lines.append("Delivery will be completed by the merchant "
                         "shortly — you'll get a confirmation message "
                         "here once it's delivered.")
        return self.send(chat_id, "\n".join(lines))

    def pay_from_wallet(self, chat_id, sess):
        """Single-order checkout, via the batch path (batch of 1)."""
        product = self.find_product(sess["pack"], sess["region"])
        sess["batch"] = [{"product": product, "uid": sess["uid"],
                          "sid": sess["sid"],
                          "price": float(product["price"])}]
        return self.pay_batch(chat_id, sess)

    def notify_delivered(self, order):
        chat_id = order.get("telegram_chat_id")
        if chat_id:
            self.send(chat_id,
                      f"✅ <b>Delivered!</b>\nOrder {order['game_order']}\n"
                      f"Product {order['productid']} for MLBB ID {order['uid']} "
                      f"(Zone {order['sid']}) has been delivered.")

    # -- Smile.one inbound payment notification --------------------------
    def handle_payment_notification(self, body, merchant_app):
        """Smile.one -> merchant, per the docs. Sign-verified inside
        merchant_app. On SUCCESS: PAID -> delivery attempt -> notify.
        Returns the docs status dict (200/201/204/205)."""
        return merchant_app.payment_notification(body)

    # -- quick orders (/br, /ph, bulk) ------------------------------------
    def parse_quick_order(self, chat_id, region, args):
        """Validate a quick-order command into a batch, or explain why
        not. args = tokens after /br or /ph."""
        info = REGIONS[region]
        if not self.region_products(region):
            return self.send(
                chat_id,
                f"{info['flag']} {info['name']} packs aren't available yet.\n"
                "The official pack list for this region is still being "
                "verified — no prices have been set, so no order can be "
                "placed for this region right now.")
        if not args or len(args) % 3 != 0:
            return self.send(
                chat_id,
                f"Usage: /{region.lower()} &lt;uid&gt; &lt;zone&gt; "
                "&lt;diamonds&gt; ...\n"
                f"Example: /{region.lower()} 986305 2375 86\n"
                f"Bulk: up to {BULK_MAX} orders in one command.")
        triplets = [args[i:i + 3] for i in range(0, len(args), 3)]
        if len(triplets) > BULK_MAX:
            return self.send(
                chat_id,
                f"Bulk max is {BULK_MAX} orders per command — you sent "
                f"{len(triplets)}. Split it into smaller commands.")
        items = []
        for uid, sid, diamonds in triplets:
            if not (uid.isdigit() and sid.isdigit() and diamonds.isdigit()):
                return self.send(
                    chat_id,
                    "UID, Zone and diamonds must all be numbers.\n"
                    f"Bad entry: {uid} {sid} {diamonds}")
            product = self.find_pack_by_diamonds(int(diamonds), region)
            if not product:
                return self.send(
                    chat_id,
                    f"No pack for {diamonds} diamonds in {region}. "
                    "Use /packs to see the available packs.")
            items.append({"product": product, "uid": uid, "sid": sid,
                          "price": float(product["price"])})
        total = sum(it["price"] for it in items)
        sess = {"step": "batch_confirm", "region": region, "batch": items}
        self.sessions[chat_id] = sess
        lines = ["Please check carefully, then confirm:"]
        for it in items:
            lines.append(f"• {it['product']['name']} — ID {it['uid']} "
                         f"(Zone {it['sid']}) — {it['price']:.2f} "
                         f"{info['currency']}")
        lines.append(f"\nTotal: {total:.2f} {info['currency']} "
                     f"({len(items)} order{'s' if len(items) > 1 else ''})")
        kb = _kb([
            [{"text": "✅ Confirm & pay", "callback_data": "batch:yes"},
             {"text": "❌ Cancel", "callback_data": "batch:no"}]])
        return self.send(chat_id, "\n".join(lines), reply_markup=kb)

    # -- admin (stats + pack prices; ADMIN_CHAT_IDS only, no password) --
    def admin_menu(self):
        return _kb([
            [{"text": "📊 Stats", "callback_data": "admin:stats"},
             {"text": "💎 Packs", "callback_data": "admin:packs"}],
            [{"text": "⬅️ Back to main menu", "callback_data": "menu:main"}],
        ])

    def show_admin_menu(self, chat_id):
        stats = store.get_stats(self.conn)
        totals = store.get_region_totals(self.conn)
        wallet_lines = "\n".join(
            f"Wallet {REGIONS[c]['flag']} [{c}]: {v:.2f} "
            f"{REGIONS[c]['currency']}" for c, v in sorted(totals.items()))
        return self.send(chat_id,
                         "🛠 <b>Admin Panel</b>\n\n"
                         f"Users: {stats['users']}\n"
                         f"Orders: {stats['orders']} ({stats['delivered']} delivered)\n"
                         + (wallet_lines + "\n" if wallet_lines else "") +
                         f"Total funded: {stats['total_funded']:.2f}\n"
                         f"Total spent: {stats['total_spent']:.2f}",
                         reply_markup=self.admin_menu())

    def show_admin_regions(self, chat_id):
        rows = [[{"text": f"{REGIONS[c]['flag']} {REGIONS[c]['name']}",
                  "callback_data": f"admin:packlist:{c}"}
                 for c in ("BR", "PH")]]
        rows.append([{"text": "⬅️ Admin menu", "callback_data": "admin:menu"}])
        return self.send(chat_id, "💎 Set pack prices — pick a region:",
                         reply_markup=_kb(rows))

    def show_admin_packs(self, chat_id, region):
        products = self.region_products(region)
        if not products:
            return self.send(chat_id,
                             f"No packs configured for {region} yet.")
        rows = []
        for i in range(0, len(products), 2):
            rows.append([{"text": f"{p['name']} - {p['price']}",
                          "callback_data": f"admin:pack:{region}:{p['pid']}"}
                         for p in products[i:i + 2]])
        rows.append([{"text": "⬅️ Regions", "callback_data": "admin:packs"},
                     {"text": "⬅️ Admin menu", "callback_data": "admin:menu"}])
        return self.send(chat_id,
                         f"💎 Set pack prices ({region}) — tap a pack:",
                         reply_markup=_kb(rows))

    def handle_admin_callback(self, chat_id, data):
        if data == "admin:menu":
            return self.show_admin_menu(chat_id)
        if data == "admin:stats":
            stats = store.get_stats(self.conn)
            totals = store.get_region_totals(self.conn)
            wallet_lines = "\n".join(
                f"Wallet {REGIONS[c]['flag']} [{c}]: {v:.2f} "
                f"{REGIONS[c]['currency']}" for c, v in sorted(totals.items()))
            self.send(chat_id,
                      f"📊 <b>Stats</b>\nUsers: {stats['users']}\n"
                      f"Orders: {stats['orders']} "
                      f"({stats['delivered']} delivered)\n" + wallet_lines)
            return self.send(chat_id, "Back to admin:",
                             reply_markup=self.admin_menu())
        if data == "admin:packs":
            return self.show_admin_regions(chat_id)
        if data.startswith("admin:packlist:"):
            return self.show_admin_packs(chat_id, data.split(":", 2)[2])
        if data.startswith("admin:pack:"):
            _, _, region, pid = data.split(":", 3)
            product = self.find_product(pid, region)
            if not product:
                return self.send(chat_id, "Pack not found.")
            kb = _kb([
                [{"text": "✏️ Set new price",
                  "callback_data": f"admin:setprice:{region}:{pid}"}],
                [{"text": "⬅️ Packs",
                  "callback_data": f"admin:packlist:{region}"},
                 {"text": "⬅️ Admin menu", "callback_data": "admin:menu"}],
            ])
            return self.send(chat_id,
                             f"💎 {product['name']}\n"
                             f"Current price: {product['price']} "
                             f"{REGIONS[region]['currency']}",
                             reply_markup=kb)
        if data.startswith("admin:setprice:"):
            _, _, region, pid = data.split(":", 3)
            product = self.find_product(pid, region)
            if not product:
                return self.send(chat_id, "Pack not found.")
            self.sessions[chat_id] = {"step": "admin_set_price",
                                      "admin_price_region": region,
                                      "admin_price_pid": pid}
            return self.send(chat_id,
                             f"Send the new price for {product['name']} "
                             f"in {REGIONS[region]['currency']} (e.g. 6.50).")
        return None

    # -- commands / messages ----------------------------------------------
    def help_text(self):
        return (
            "🤖 <b>All Commands</b>\n\n"
            "/start — Welcome + your balances + order examples\n"
            "/getid — Your account ID\n"
            "/packs — List all packs and prices\n"
            "/orders — List your orders\n"
            "/balance — Your per-region balances\n"
            "/br &lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt; — Quick order, Brazil\n"
            "/ph &lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt; — Quick order, Philippines\n"
            "Bulk: repeat uid + zone + diamonds, up to 10 orders in one "
            "command, e.g. /br 986305 2375 86 986305 2375 86\n"
            "/addfund [region] &lt;amount&gt; — Add funds (test mode)\n"
            "/admin — Admin panel\n"
            "/help — This list")

    def handle_message(self, chat_id, text):
        sess = self.sessions.get(chat_id)
        text = (text or "").strip()
        if text.startswith("/admin"):
            if not self.is_admin(chat_id):
                return self.send(chat_id, "Admin access only.")
            return self.show_admin_menu(chat_id)
        if sess and sess.get("step") == "admin_set_price":
            if not self.is_admin(chat_id):
                self.sessions.pop(chat_id, None)
                return self.send(chat_id, "Admin access only.")
            region = sess.get("admin_price_region", "BR")
            pid = sess.get("admin_price_pid")
            product = self.find_product(pid, region) if pid else None
            try:
                value = float(text.replace(",", "."))
            except ValueError:
                value = -1
            if not product or value <= 0:
                return self.send(chat_id,
                                 "Send a valid price above 0, e.g. 6.50.")
            old = product["price"]
            product["price"] = f"{value:.2f}"
            if self._save_region(region):
                self.sessions.pop(chat_id, None)
                return self.send(chat_id,
                                 f"✅ {product['name']} price updated: "
                                 f"{old} → {product['price']}.")
            product["price"] = old
            return self.send(chat_id,
                             "Could not save the new price — nothing changed.")
        if text.startswith("/start"):
            return self.start(chat_id)
        if text.startswith("/getid"):
            return self.send(chat_id,
                             f"🆔 <b>Your Account ID:</b> {chat_id}\n"
                             "This is your Telegram ID for this bot.")
        if text.startswith("/help"):
            return self.send(chat_id, self.help_text())
        if text.startswith("/packs"):
            self.send(chat_id, self.packs_text("BR"))
            if self.region_products("PH"):
                self.send(chat_id, self.packs_text("PH"))
            return self.send(chat_id, "Want to buy one? Tap Top Up below:",
                             reply_markup=self.main_menu())
        if text.startswith("/orders"):
            orders = store.list_orders(self.conn, chat_id)
            if not orders:
                return self.send(chat_id, "No orders yet. Use /start to top up.")
            lines = []
            for o in orders:
                region = CURRENCY_REGION.get(o["currency"], o["currency"])
                lines.append(f"{o['game_order']} - {o['productid']} "
                             f"[{region}] - {o['status']}")
            return self.send(chat_id, "Your orders:\n" + "\n".join(lines))
        if text.startswith("/balance"):
            lines = ["💰 <b>Your Current Balance</b>"]
            lines.extend(self.balance_lines(chat_id))
            return self.send(chat_id, "\n".join(lines))
        if text.startswith("/addfund"):
            parts = text.split()
            region, amount_s = "BR", None
            if len(parts) == 2:
                amount_s = parts[1]
            elif len(parts) == 3 and parts[1].upper() in REGIONS:
                region, amount_s = parts[1].upper(), parts[2]
            if amount_s is None:
                return self.send(chat_id,
                                 "Usage: /addfund [region] <amount>\n"
                                 "Examples: /addfund 100, /addfund br 100")
            try:
                amount = float(amount_s)
                if amount <= 0:
                    raise ValueError
            except ValueError:
                return self.send(chat_id,
                                 "Send a positive amount, e.g. /addfund 100")
            if not self.auto_credit_test:
                return self.send(chat_id,
                                 "Test auto-credit is off. Ask the merchant "
                                 "to add funds to your wallet.")
            bal = store.credit_region_balance(self.conn, chat_id, region,
                                              amount)
            return self.send(chat_id,
                             f"✅ <b>Test mode:</b> {amount:.2f} "
                             f"{REGIONS[region]['currency']} added instantly "
                             f"to your {region} wallet.\n"
                             f"New {region} balance: {bal['balance']:.2f}")
        m = re.match(r"^/(br|ph)(?:\s+(.*))?$", text.lower())
        if m:
            region = m.group(1).upper()
            args = m.group(2).split() if m.group(2) else []
            return self.parse_quick_order(chat_id, region, args)
        if not sess:
            return self.start(chat_id)
        if sess["step"] == "uid":
            if not text.isdigit():
                return self.send(chat_id,
                                 "MLBB ID must be numbers only. Send it again.")
            sess["uid"] = text
            sess["step"] = "sid"
            return self.send(chat_id,
                             "Now send your Zone ID (the number in brackets "
                             "after your MLBB ID, e.g. 1234).")
        if sess["step"] == "sid":
            if not text.isdigit():
                return self.send(chat_id,
                                 "Zone ID must be numbers only. Send it again.")
            sess["sid"] = text
            return self.send_confirm(chat_id, sess)
        return self.send(chat_id, "Use /start to begin a new top-up.")

    def handle_callback(self, chat_id, data):
        sess = self.sessions.setdefault(chat_id, {})
        if data.startswith("admin:"):
            if not self.is_admin(chat_id):
                return self.send(chat_id, "Admin access only.")
            return self.handle_admin_callback(chat_id, data)
        if data == "menu:main":
            return self.start(chat_id)
        if data == "menu:topup":
            self.sessions[chat_id] = {"step": "region"}
            return self.send(chat_id, "Pick a region:",
                             reply_markup=self.region_menu())
        if data.startswith("region:"):
            region = data.split(":", 1)[1]
            if region not in REGIONS:
                return self.send(chat_id, "Unknown region.")
            if not self.region_products(region):
                return self.send(chat_id,
                                 f"{REGIONS[region]['flag']} "
                                 f"{REGIONS[region]['name']} packs aren't "
                                 "available yet.")
            return self.show_packs(chat_id, region, 0)
        if data == "menu:packs":
            self.send(chat_id, self.packs_text("BR"))
            if self.region_products("PH"):
                self.send(chat_id, self.packs_text("PH"))
            return self.send(chat_id, "Back to menu:",
                             reply_markup=self.main_menu())
        if data.startswith("packpage:"):
            try:
                _, region, page = data.split(":", 2)
                page = int(page)
            except ValueError:
                region, page = "BR", 0
            return self.show_packs(chat_id, region, page)
        if data == "menu:balance":
            lines = ["💰 <b>Your Current Balance</b>"]
            lines.extend(self.balance_lines(chat_id))
            self.send(chat_id, "\n".join(lines))
            return self.send(chat_id, "Back to menu:",
                             reply_markup=self.main_menu())
        if data == "menu:orders":
            orders = store.list_orders(self.conn, chat_id)
            if not orders:
                self.send(chat_id, "No orders yet. Use Top Up from the menu.")
            else:
                lines = []
                for o in orders:
                    region = CURRENCY_REGION.get(o["currency"], o["currency"])
                    lines.append(f"{o['game_order']} - {o['productid']} "
                                 f"[{region}] - {o['status']}")
                self.send(chat_id, "Your orders:\n" + "\n".join(lines))
            return self.send(chat_id, "Back to menu:",
                             reply_markup=self.main_menu())
        if data == "menu:getid":
            return self.send(chat_id,
                             f"🆔 <b>Your Account ID:</b> {chat_id}")
        if data == "menu:help":
            return self.handle_message(chat_id, "/help")
        if data.startswith("pack:"):
            parts = data.split(":")
            if len(parts) == 3:
                _, region, pid = parts
            else:
                region, pid = "BR", parts[1]
            product = self.find_product(pid, region)
            if not product:
                return self.send(chat_id, "Pack not found. Pick another:")
            sess["pack"] = product["pid"]
            sess["region"] = region
            sess["step"] = "uid"
            return self.send(chat_id,
                             f"Pack: {product['name']} - {product['price']} "
                             f"{REGIONS[region]['currency']}.\n"
                             "Send your MLBB ID (numbers only).")
        if data == "confirm:yes":
            if not sess.get("pack") or not sess.get("uid") or not sess.get("sid"):
                self.sessions.pop(chat_id, None)
                return self.start(chat_id)
            return self.pay_from_wallet(chat_id, sess)
        if data == "confirm:no":
            self.sessions.pop(chat_id, None)
            return self.start(chat_id)
        if data == "batch:yes":
            if not sess.get("batch") or not sess.get("region"):
                self.sessions.pop(chat_id, None)
                return self.start(chat_id)
            return self.pay_batch(chat_id, sess)
        if data == "batch:no":
            self.sessions.pop(chat_id, None)
            return self.start(chat_id)
        return None

    def handle_update(self, upd):
        """Handle one Telegram update dict — webhook and polling alike."""
        msg = upd.get("message") or {}
        cb = upd.get("callback_query") or {}
        if msg and "text" in msg:
            self.handle_message(msg["chat"]["id"], msg["text"])
        elif cb:
            chat_id = cb["message"]["chat"]["id"]
            self.handle_callback(chat_id, cb["data"])
            if self.sender is None:
                try:
                    self.api("answerCallbackQuery",
                             {"callback_query_id": cb["id"]})
                except Exception:
                    pass

    def poll_once(self):
        res = self.api("getUpdates", {"offset": self.offset, "timeout": 20})
        for upd in res.get("result", []):
            self.offset = upd["update_id"] + 1
            self.handle_update(upd)

    def run(self):
        print("[order-bot] polling started")
        self.set_commands()
        while True:
            try:
                self.poll_once()
            except Exception as e:
                print("[order-bot] poll error:", e)
                time.sleep(3)
