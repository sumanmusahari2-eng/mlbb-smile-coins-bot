"""Fresh Telegram ordering bot, built from the Smile.one Docs order lifecycle.

Docs model (docs.smile.one: "APIs and parameters that merchants need to
provide to Smile One"):
  1. Product list  -> the merchant's packs; the customer picks one.
  2. Role check    -> uid (+ sid/zone) identifies the player.
  3. Game order    -> an order is created, identified by game_order.
  4. Payment       -> a payment notification with trade_status SUCCESS
                      confirms the order is paid.
  5. Delivery      -> the merchant delivers the items, order = DELIVERED.

This bot is the customer-facing side of that lifecycle, written clean
from the beginning (the old telegram_bot.py mixed admin panels, fund
requests, region shortcuts and a wallet flow into one file):

  /start  -> menu -> Top Up -> pick a pack -> send MLBB ID
          -> send Zone ID -> confirm -> pay from wallet
  /packs   -> list all packs and prices
  /orders  -> list your orders and their status
  /balance -> check your wallet balance
  /addfund <amount> -> test-mode credit only (see below)
  /help    -> how ordering works

Order states: PENDING -> PAID -> DELIVERED.
  * Wallet payment deducts the balance and moves the order to PAID.
  * A Smile.one payment notification (handle_payment_notification, sign
    verified with the merchant key) also moves an order to PAID.
  * Delivery is a pluggable hook (deliver=...). The shipped default does
    NOT pretend to deliver: it logs the delivery request and returns
    False, so a paid order stays PAID and the customer is told the
    merchant will complete delivery. Wire a real supplier/fulfilment
    call here and paid orders become DELIVERED automatically, with a
    confirmation message to the customer.

Honest limits, stated in the code and in the messages, not hidden:
  * Nickname lookup: the docs do not publish a merchant-to-Smile.one
    role-query API, so this bot does NOT fake a nickname. The confirm
    screen shows the exact MLBB ID + Zone ID back and the customer
    confirms them. If Smile.one staff confirm a real lookup, add it
    in the confirm step.
  * /addfund is TEST ONLY while auto_credit_test is on: it credits
    instantly so the order flow can be tested with no payment rail.
    Turn it off before any real use; real funding needs a payment
    method, which this bot does not invent.
  * No checkout URL is invented: the docs publish no direct
    Smile Coins debit URL, so this bot never shows a fake pay link.

Stdlib only. No token or network is needed to test: pass a fake
sender= callable and the whole flow runs against an in-memory DB
(see test_order_bot.py).
"""
import json
import time
import urllib.parse
import urllib.request

import store
from merchant_server import MerchantApp

API = "https://api.telegram.org/bot"
PER_PAGE = 8


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
                 auto_credit_test=False, admin_chat_ids="", merchant=None):
        self.token = (token or "").strip()
        self.conn = conn
        self.products = products
        self.sender = sender  # fake sender for tests: fn(chat_id, text, kb)
        self.deliver = deliver
        self.auto_credit_test = auto_credit_test
        self.merchant = merchant  # OrderMerchantApp, for admin price saves
        raw_admins = admin_chat_ids or ""
        self.admin_ids = {int(x) for x in str(raw_admins).split(",")
                          if x.strip().isdigit()}
        self.sessions = {}  # chat_id -> dict
        self.offset = 0

    def is_admin(self, chat_id):
        return chat_id in self.admin_ids

    # -- products ------------------------------------------------------
    def find_product(self, pid):
        for p in self.products:
            if str(p["pid"]) == str(pid):
                return p
        return None

    def packs_text(self):
        lines = ["💎 <b>MLBB Packs</b>:"]
        for p in self.products:
            lines.append(f"{p['name']} - {p['price']} {p.get('currency', 'BRL')}")
        return "\n".join(lines)

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

    # -- menus -----------------------------------------------------------
    def main_menu(self):
        return _kb([
            [{"text": "🎮 Top Up MLBB", "callback_data": "menu:topup"},
             {"text": "💎 Packs", "callback_data": "menu:packs"}],
            [{"text": "💰 Balance", "callback_data": "menu:balance"},
             {"text": "📦 My Orders", "callback_data": "menu:orders"}],
            [{"text": "❓ Help", "callback_data": "menu:help"}],
        ])

    def start(self, chat_id):
        self.sessions[chat_id] = {"step": "menu"}
        return self.send(
            chat_id,
            "🎮 <b>MLBB Top-Up</b>\n\n"
            "Pick a pack, send your MLBB ID + Zone ID, confirm, and pay "
            "from your wallet.\n\n"
            "Use the menu below, or /packs, /orders, /balance, /help.",
            reply_markup=self.main_menu())

    def show_packs(self, chat_id, page=0):
        total_pages = max(1, (len(self.products) + PER_PAGE - 1) // PER_PAGE)
        page = max(0, min(page, total_pages - 1))
        self.sessions[chat_id] = {"step": "pack", "pack_page": page}
        items = self.products[page * PER_PAGE:(page + 1) * PER_PAGE]
        rows = []
        for i in range(0, len(items), 2):
            rows.append([{"text": f"{p['name']} - {p['price']}",
                          "callback_data": f"pack:{p['pid']}"}
                         for p in items[i:i + 2]])
        nav = []
        if page > 0:
            nav.append({"text": "◀️ Prev", "callback_data": f"packpage:{page - 1}"})
        if page < total_pages - 1:
            nav.append({"text": "Next ▶️", "callback_data": f"packpage:{page + 1}"})
        if nav:
            rows.append(nav)
        rows.append([{"text": "⬅️ Back to menu", "callback_data": "menu:main"}])
        return self.send(chat_id, f"Pick a pack: (page {page + 1}/{total_pages})",
                         reply_markup=_kb(rows))

    # -- order flow ------------------------------------------------------
    def send_confirm(self, chat_id, sess):
        product = self.find_product(sess["pack"])
        sess["step"] = "confirm"
        kb = _kb([
            [{"text": "✅ Confirm & pay", "callback_data": "confirm:yes"},
             {"text": "❌ Cancel", "callback_data": "confirm:no"}]])
        return self.send(
            chat_id,
            "Please check carefully, then confirm:\n"
            f"<b>{product['name']}</b>\n"
            f"MLBB ID: {sess['uid']}\nZone ID: {sess['sid']}\n"
            f"Price: {product['price']} {product.get('currency', 'BRL')}\n\n"
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

    def pay_from_wallet(self, chat_id, sess):
        """Charge the wallet, create the order, attempt delivery."""
        product = self.find_product(sess["pack"])
        price = float(product["price"])
        currency = product.get("currency", "BRL")
        bal = store.get_balance(self.conn, chat_id)
        if bal["balance"] < price - 1e-9:
            shortfall = price - bal["balance"]
            sess["step"] = "menu"
            return self.send(
                chat_id,
                f"⚠️ Not enough balance.\n"
                f"Price: {price:.2f} {currency}\n"
                f"Your balance: {bal['balance']:.2f}\n"
                f"You need {shortfall:.2f} more.\n\n"
                f"Add funds with: /addfund {shortfall:.2f}\n"
                "Then use /start again to place the order.\n"
                "(No order was created.)")
        store.deduct_balance(self.conn, chat_id, price)
        order_id = store.create_order(self.conn, chat_id, sess["uid"],
                                      sess["sid"], sess["pack"],
                                      product["price"], currency)
        store.update_order(self.conn, order_id, "PAID", "WALLET")
        order = store.get_order(self.conn, order_id)
        new_bal = store.get_balance(self.conn, chat_id)
        sess["step"] = "done"
        delivered = self._attempt_delivery(order)
        if delivered:
            return self.send(
                chat_id,
                f"✅ <b>Paid and delivered!</b>\n"
                f"Order {order_id}\n{product['name']} for MLBB ID "
                f"{sess['uid']} (Zone {sess['sid']})\n"
                f"Paid: {price:.2f} {currency}\n"
                f"Remaining balance: {new_bal['balance']:.2f}")
        return self.send(
            chat_id,
            f"✅ <b>Payment received.</b>\n"
            f"Order {order_id}\n{product['name']} for MLBB ID "
            f"{sess['uid']} (Zone {sess['sid']})\n"
            f"Paid: {price:.2f} {currency}\n"
            f"Remaining balance: {new_bal['balance']:.2f}\n\n"
            "Delivery will be completed by the merchant shortly — "
            "you'll get a confirmation message here once it's delivered.")

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
        result = merchant_app.payment_notification(body)
        if result.get("status") == 200 and result.get("game_status") == "SUCCESS":
            order = store.get_order(self.conn, body.get("game_order", ""))
            if order and order["status"] == "DELIVERED":
                # MerchantApp already delivered + notified via on_delivered.
                pass
        return result

    # -- admin (kept minimal: stats + pack prices, the two things the
    # merchant actually uses; admin = ADMIN_CHAT_IDS only, no password) --
    def admin_menu(self):
        return _kb([
            [{"text": "📊 Stats", "callback_data": "admin:stats"},
             {"text": "💎 Packs", "callback_data": "admin:packs"}],
            [{"text": "⬅️ Back to main menu", "callback_data": "menu:main"}],
        ])

    def show_admin_menu(self, chat_id):
        stats = store.get_stats(self.conn)
        return self.send(chat_id,
                         "🛠 <b>Admin Panel</b>\n\n"
                         f"Users: {stats['users']}\n"
                         f"Orders: {stats['orders']} ({stats['delivered']} delivered)\n"
                         f"Pending funds: {stats['pending_funds']}\n"
                         f"Total wallet balance: {stats['total_balance']:.2f}\n"
                         f"Total funded: {stats['total_funded']:.2f}\n"
                         f"Total spent: {stats['total_spent']:.2f}",
                         reply_markup=self.admin_menu())

    def show_admin_packs(self, chat_id):
        rows = []
        for i in range(0, len(self.products), 2):
            rows.append([{"text": f"{p['name']} - {p['price']}",
                          "callback_data": f"admin:pack:{p['pid']}"}
                         for p in self.products[i:i + 2]])
        rows.append([{"text": "⬅️ Admin menu", "callback_data": "admin:menu"}])
        return self.send(chat_id, "💎 Set pack prices — tap a pack:",
                         reply_markup=_kb(rows))

    def handle_admin_callback(self, chat_id, data):
        if data == "admin:menu":
            return self.show_admin_menu(chat_id)
        if data == "admin:stats":
            stats = store.get_stats(self.conn)
            self.send(chat_id,
                      f"📊 <b>Stats</b>\nUsers: {stats['users']}\n"
                      f"Orders: {stats['orders']} "
                      f"({stats['delivered']} delivered)\n"
                      f"Total balance: {stats['total_balance']:.2f}\n"
                      f"Total funded: {stats['total_funded']:.2f}\n"
                      f"Total spent: {stats['total_spent']:.2f}")
            return self.send(chat_id, "Back to admin:",
                             reply_markup=self.admin_menu())
        if data == "admin:packs":
            return self.show_admin_packs(chat_id)
        if data.startswith("admin:pack:"):
            product = self.find_product(data.split(":", 2)[2])
            if not product:
                return self.send(chat_id, "Pack not found.")
            kb = _kb([
                [{"text": "✏️ Set new price",
                  "callback_data": f"admin:setprice:{product['pid']}"}],
                [{"text": "⬅️ Packs", "callback_data": "admin:packs"},
                 {"text": "⬅️ Admin menu", "callback_data": "admin:menu"}],
            ])
            return self.send(chat_id,
                             f"💎 {product['name']}\n"
                             f"Current price: {product['price']} "
                             f"{product.get('currency', 'BRL')}",
                             reply_markup=kb)
        if data.startswith("admin:setprice:"):
            product = self.find_product(data.split(":", 2)[2])
            if not product:
                return self.send(chat_id, "Pack not found.")
            self.sessions[chat_id] = {"step": "admin_set_price",
                                      "admin_price_pid": product["pid"]}
            return self.send(chat_id,
                             f"Send the new price for {product['name']} "
                             "(e.g. 6.50).")
        return None

    # -- commands / messages ----------------------------------------------
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
            pid = sess.get("admin_price_pid")
            product = self.find_product(pid) if pid else None
            try:
                value = float(text.replace(",", "."))
            except ValueError:
                value = -1
            if not product or value <= 0:
                return self.send(chat_id,
                                 "Send a valid price above 0, e.g. 6.50.")
            old = product["price"]
            product["price"] = f"{value:.2f}"
            saved = self.merchant.save_products() if self.merchant else False
            if saved:
                self.sessions.pop(chat_id, None)
                return self.send(chat_id,
                                 f"✅ {product['name']} price updated: "
                                 f"{old} → {product['price']}.")
            product["price"] = old
            return self.send(chat_id,
                             "Could not save the new price — nothing changed.")
        if text.startswith("/start"):
            return self.start(chat_id)
        if text.startswith("/packs"):
            self.send(chat_id, self.packs_text())
            return self.send(chat_id, "Want to buy one? Tap Top Up below:",
                             reply_markup=self.main_menu())
        if text.startswith("/orders"):
            orders = store.list_orders(self.conn, chat_id)
            if not orders:
                return self.send(chat_id, "No orders yet. Use /start to top up.")
            lines = [f"{o['game_order']} - {o['productid']} - {o['status']}"
                     for o in orders]
            return self.send(chat_id, "Your orders:\n" + "\n".join(lines))
        if text.startswith("/balance"):
            bal = store.get_balance(self.conn, chat_id)
            return self.send(chat_id,
                             f"💰 <b>Your balance: {bal['balance']:.2f}</b>\n"
                             f"Total added: {bal['total_funded']:.2f}\n"
                             f"Total spent: {bal['total_spent']:.2f}")
        if text.startswith("/addfund"):
            parts = text.split()
            if len(parts) < 2:
                return self.send(chat_id,
                                 "Usage: /addfund <amount>\nExample: /addfund 100")
            try:
                amount = float(parts[1])
                if amount <= 0:
                    raise ValueError
            except ValueError:
                return self.send(chat_id,
                                 "Send a positive amount, e.g. /addfund 100")
            if not self.auto_credit_test:
                return self.send(chat_id,
                                 "Test auto-credit is off. Ask the merchant "
                                 "to add funds to your wallet.")
            bal = store.credit_balance(self.conn, chat_id, amount)
            return self.send(chat_id,
                             f"✅ <b>Test mode:</b> {amount:.2f} added instantly.\n"
                             f"New balance: {bal['balance']:.2f}")
        if text.startswith("/help"):
            return self.send(chat_id,
                             "How ordering works:\n"
                             "1. /start, tap Top Up, pick a pack\n"
                             "2. Send your MLBB ID, then your Zone ID\n"
                             "3. Check the confirm screen and pay from "
                             "your wallet\n"
                             "4. Delivery follows — you get a message here "
                             "when it's delivered\n\n"
                             "/packs - list packs\n/orders - your orders\n"
                             "/balance - your wallet\n/addfund - test credit")
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
            return self.show_packs(chat_id, 0)
        if data == "menu:packs":
            self.send(chat_id, self.packs_text())
            return self.send(chat_id, "Back to menu:",
                             reply_markup=self.main_menu())
        if data.startswith("packpage:"):
            try:
                page = int(data.split(":", 1)[1])
            except ValueError:
                page = 0
            return self.show_packs(chat_id, page)
        if data == "menu:balance":
            bal = store.get_balance(self.conn, chat_id)
            self.send(chat_id,
                      f"💰 <b>Your balance: {bal['balance']:.2f}</b>")
            return self.send(chat_id, "Back to menu:",
                             reply_markup=self.main_menu())
        if data == "menu:orders":
            orders = store.list_orders(self.conn, chat_id)
            if not orders:
                self.send(chat_id, "No orders yet. Use Top Up from the menu.")
            else:
                lines = [f"{o['game_order']} - {o['productid']} - {o['status']}"
                         for o in orders]
                self.send(chat_id, "Your orders:\n" + "\n".join(lines))
            return self.send(chat_id, "Back to menu:",
                             reply_markup=self.main_menu())
        if data == "menu:help":
            return self.handle_message(chat_id, "/help")
        if data.startswith("pack:"):
            product = self.find_product(data.split(":", 1)[1])
            if not product:
                return self.send(chat_id, "Pack not found. Pick another:")
            sess["pack"] = product["pid"]
            sess["step"] = "uid"
            return self.send(chat_id,
                             f"Pack: {product['name']} - {product['price']} "
                             f"{product.get('currency', 'BRL')}.\n"
                             "Send your MLBB ID (numbers only).")
        if data == "confirm:yes":
            if not sess.get("pack") or not sess.get("uid") or not sess.get("sid"):
                self.sessions.pop(chat_id, None)
                return self.start(chat_id)
            return self.pay_from_wallet(chat_id, sess)
        if data == "confirm:no":
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

    def set_commands(self):
        """Telegram Menu button (bottom-left) - shows these commands."""
        if self.sender is not None:
            return
        commands = [
            {"command": "start", "description": "Open the main menu"},
            {"command": "packs", "description": "View all MLBB packs"},
            {"command": "orders", "description": "List your orders"},
            {"command": "balance", "description": "Check your balance"},
            {"command": "admin", "description": "Admin panel"},
            {"command": "help", "description": "How ordering works"},
        ]
        try:
            self.api("setMyCommands", {"commands": json.dumps(commands)})
            print("[order-bot] menu commands set")
        except Exception as e:
            print("[order-bot] setMyCommands failed:", e)

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
