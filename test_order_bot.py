"""Tests for the fresh order bot — no token, no network, no real money.

Covers the docs order lifecycle: pick pack -> ID + Zone -> confirm ->
pay from wallet -> delivery attempt -> status, plus the Smile.one
inbound payment-notification path (sign-verified).
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store
from order_bot import OrderBot, OrderMerchantApp
from smile_sign import make_sign

PACKS = [
    {"pid": "5", "name": "MLBB 78 + 8 Diamonds", "price": "6.25",
     "original_price": "6.25", "currency": "BRL", "diamonds": 86},
    {"pid": "1", "name": "MLBB 50 + 5 Diamonds", "price": "4.00",
     "original_price": "4.00", "currency": "BRL", "diamonds": 55},
]


class FakeSender:
    def __init__(self):
        self.messages = []  # (chat_id, text)

    def __call__(self, chat_id, text, reply_markup=None):
        self.messages.append((chat_id, text))
        return {"ok": True}

    def texts(self):
        return [t for _, t in self.messages]


class OrderBotTest(unittest.TestCase):
    def setUp(self):
        self.conn = store.init_db(":memory:")
        self.sender = FakeSender()

    def make_bot(self, deliver=None, auto_credit_test=True):
        return OrderBot("TESTTOKEN", self.conn, PACKS,
                        sender=self.sender, deliver=deliver,
                        auto_credit_test=auto_credit_test)

    def order_flow(self, bot, chat=111, pack="5", uid="603803877",
                   sid="5674"):
        bot.handle_message(chat, "/start")
        bot.handle_callback(chat, "menu:topup")
        bot.handle_callback(chat, f"pack:{pack}")
        bot.handle_message(chat, uid)
        bot.handle_message(chat, sid)
        return bot

    def test_full_wallet_order_delivered_via_hook(self):
        bot = self.make_bot(deliver=lambda o: True)
        bot.handle_message(111, "/addfund 100")
        self.order_flow(bot)
        bot.handle_callback(111, "confirm:yes")
        orders = store.list_orders(self.conn, 111)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["status"], "DELIVERED")
        self.assertAlmostEqual(store.get_region_balance(self.conn, 111, "BR")["balance"],
                               93.75)
        joined = "\n".join(self.sender.texts())
        self.assertIn("Payment received", joined)
        self.assertIn("All orders delivered!", joined)
        self.assertIn("Delivered!", joined)
        self.assertIn(orders[0]["game_order"], joined)

    def test_wallet_order_stays_paid_when_delivery_not_configured(self):
        bot = self.make_bot(deliver=None)  # default: honest, no fake delivery
        bot.handle_message(111, "/addfund 100")
        self.order_flow(bot)
        bot.handle_callback(111, "confirm:yes")
        orders = store.list_orders(self.conn, 111)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["status"], "PAID")
        self.assertAlmostEqual(store.get_region_balance(self.conn, 111, "BR")["balance"],
                               93.75)
        joined = "\n".join(self.sender.texts())
        self.assertIn("Payment received", joined)
        self.assertIn("completed by the merchant", joined)
        self.assertNotIn("Delivered!", joined)

    def test_insufficient_balance_creates_no_order(self):
        bot = self.make_bot(deliver=lambda o: True)
        self.order_flow(bot)
        bot.handle_callback(111, "confirm:yes")
        self.assertEqual(store.list_orders(self.conn, 111), [])
        joined = "\n".join(self.sender.texts())
        self.assertIn("Not enough balance", joined)
        self.assertIn("You need 6.25 more", joined)
        self.assertIn("No order was created", joined)

    def test_cancel_creates_no_order(self):
        bot = self.make_bot()
        self.order_flow(bot)
        bot.handle_callback(111, "confirm:no")
        self.assertEqual(store.list_orders(self.conn, 111), [])

    def test_invalid_ids_reprompt_and_no_nickname_faked(self):
        bot = self.make_bot()
        bot.handle_message(111, "/start")
        bot.handle_callback(111, "menu:topup")
        bot.handle_callback(111, "pack:5")
        bot.handle_message(111, "abc")
        self.assertIn("numbers only", self.sender.texts()[-1])
        bot.handle_message(111, "603803877")
        bot.handle_message(111, "xyz")
        self.assertIn("numbers only", self.sender.texts()[-1])
        bot.handle_message(111, "5674")
        confirm = self.sender.texts()[-1]
        self.assertIn("603803877", confirm)
        self.assertIn("5674", confirm)
        self.assertIn("6.25", confirm)
        self.assertNotIn("Nickname", confirm)  # never faked

    def test_unknown_pack(self):
        bot = self.make_bot()
        bot.handle_message(111, "/start")
        bot.handle_callback(111, "pack:999")
        self.assertIn("Pack not found", self.sender.texts()[-1])

    def test_orders_and_balance_commands(self):
        bot = self.make_bot(deliver=lambda o: True)
        bot.handle_message(111, "/addfund 100")
        self.order_flow(bot)
        bot.handle_callback(111, "confirm:yes")
        bot.handle_message(111, "/orders")
        self.assertIn("DELIVERED", self.sender.texts()[-1])
        bot.handle_message(111, "/balance")
        self.assertIn("93.75", self.sender.texts()[-1])

    def test_real_product_list_renders(self):
        doc = store.load_json(
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "products.json"), {"products": []})
        self.assertEqual(len(doc["products"]), 16)
        bot = OrderBot("TESTTOKEN", self.conn, doc["products"],
                       sender=self.sender)
        bot.handle_message(111, "/packs")
        joined = "\n".join(self.sender.texts())
        self.assertIn("78 + 8 Diamonds", joined)
        self.assertIn("Twilight Pass", joined)

    # -- Smile.one inbound payment notification -------------------------
    def make_merchant(self, bot):
        tmp = tempfile.mkdtemp()
        ppath = os.path.join(tmp, "products.json")
        rpath = os.path.join(tmp, "roles.json")
        with open(ppath, "w") as f:
            json.dump({"products": PACKS}, f)
        with open(rpath, "w") as f:
            json.dump({"roles": {}}, f)
        m = OrderMerchantApp(
            self.conn,
            {"products_path": ppath, "roles_path": rpath,
             "merchant_key": "testkey"},
            deliver=lambda o: True,
            on_delivered=bot.notify_delivered)
        return m

    def signed_body(self, **fields):
        body = dict(fields)
        body["sign"] = make_sign(body, "testkey")
        return body

    def test_payment_notification_success_delivers(self):
        bot = self.make_bot(deliver=lambda o: True)
        merchant = self.make_merchant(bot)
        order_id = store.create_order(self.conn, 222, "603803877", "5674",
                                      "5", "6.25", "BRL")
        body = self.signed_body(game_order=order_id, uid="603803877",
                                sid="5674", productid="5",
                                trade_no="T1", trade_status="SUCCESS")
        result = bot.handle_payment_notification(body, merchant)
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["game_status"], "SUCCESS")
        self.assertEqual(store.get_order(self.conn, order_id)["status"],
                         "DELIVERED")
        joined = "\n".join(self.sender.texts())
        self.assertIn("Delivered!", joined)
        self.assertIn(order_id, joined)

    def test_payment_notification_bad_sign_rejected(self):
        bot = self.make_bot(deliver=lambda o: True)
        merchant = self.make_merchant(bot)
        order_id = store.create_order(self.conn, 222, "603803877", "5674",
                                      "5", "6.25", "BRL")
        body = self.signed_body(game_order=order_id, uid="603803877",
                                sid="5674", productid="5",
                                trade_no="T1", trade_status="SUCCESS")
        body["sign"] = "0" * 32  # tampered
        result = bot.handle_payment_notification(body, merchant)
        self.assertEqual(result["status"], 205)
        self.assertEqual(store.get_order(self.conn, order_id)["status"],
                         "PENDING")
        self.assertEqual(self.sender.messages, [])

    def test_payment_notification_unknown_order(self):
        bot = self.make_bot(deliver=lambda o: True)
        merchant = self.make_merchant(bot)
        body = self.signed_body(game_order="nope", uid="1",
                                trade_no="T1", trade_status="SUCCESS")
        result = bot.handle_payment_notification(body, merchant)
        self.assertEqual(result["status"], 201)

    # -- admin (stats + price setting, the preserved admin features) ----
    def make_admin_bot(self, admin_ids="111"):
        merchant = self.make_merchant(self.make_bot())
        bot = OrderBot("TESTTOKEN", self.conn, merchant.products,
                       sender=self.sender, deliver=None,
                       auto_credit_test=True, admin_chat_ids=admin_ids,
                       merchant=merchant)
        return bot, merchant

    def test_admin_denied_for_non_admin(self):
        bot, _ = self.make_admin_bot(admin_ids="999")
        bot.handle_message(111, "/admin")
        self.assertIn("Admin access only", self.sender.texts()[-1])
        bot.handle_callback(111, "admin:menu")
        self.assertIn("Admin access only", self.sender.texts()[-1])

    def test_admin_set_price(self):
        bot, merchant = self.make_admin_bot(admin_ids="111")
        bot.handle_message(111, "/admin")
        self.assertIn("Admin Panel", self.sender.texts()[-1])
        bot.handle_callback(111, "admin:packs")
        self.assertIn("pick a region", self.sender.texts()[-1])
        bot.handle_callback(111, "admin:packlist:BR")
        self.assertIn("Set pack prices", self.sender.texts()[-1])
        bot.handle_callback(111, "admin:pack:BR:5")
        self.assertIn("Current price: 6.25", self.sender.texts()[-1])
        bot.handle_callback(111, "admin:setprice:BR:5")
        bot.handle_message(111, "abc")
        self.assertIn("valid price", self.sender.texts()[-1])
        bot.handle_message(111, "7.00")
        self.assertIn("price updated: 6.25 → 7.00", self.sender.texts()[-1])
        self.assertEqual(merchant.find_product("5")["price"], "7.00")
        # And it persisted to the products file.
        doc = store.load_json(merchant.config["products_path"], {})
        saved = [p for p in doc["products"] if p["pid"] == "5"][0]
        self.assertEqual(saved["price"], "7.00")

    # -- multi-region: /start, /getid, /br, /ph, bulk --------------------
    def test_start_shows_balances_and_examples(self):
        bot = self.make_bot()
        bot.handle_message(111, "/start")
        joined = "\n".join(self.sender.texts())
        self.assertIn("Your Current Balance", joined)
        self.assertIn("[BR]", joined)
        self.assertIn("[PH]", joined)
        self.assertIn("/br", joined)
        self.assertIn("/ph", joined)
        self.assertIn("Bulk (10 max)", joined)

    def test_getid(self):
        bot = self.make_bot()
        bot.handle_message(111, "/getid")
        self.assertIn("111", self.sender.texts()[-1])
        self.assertIn("Account ID", self.sender.texts()[-1])

    def test_quick_order_br_single(self):
        bot = self.make_bot(deliver=lambda o: True)
        bot.handle_message(111, "/addfund 100")
        bot.handle_message(111, "/br 603803877 5674 86")
        confirm = self.sender.texts()[-1]
        self.assertIn("603803877", confirm)
        self.assertIn("Total: 6.25 BRL", confirm)
        bot.handle_callback(111, "batch:yes")
        orders = store.list_orders(self.conn, 111)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["status"], "DELIVERED")
        self.assertAlmostEqual(
            store.get_region_balance(self.conn, 111, "BR")["balance"], 93.75)

    def test_quick_order_bulk_two_and_max(self):
        bot = self.make_bot(deliver=lambda o: True)
        bot.handle_message(111, "/addfund 100")
        bot.handle_message(111, "/br 111 1 86 222 2 55")
        bot.handle_callback(111, "batch:yes")
        orders = store.list_orders(self.conn, 111)
        self.assertEqual(len(orders), 2)
        self.assertAlmostEqual(
            store.get_region_balance(self.conn, 111, "BR")["balance"],
            100 - 6.25 - 4.00)
        # 11 triplets exceeds the bulk max.
        bot2 = self.make_bot()
        args = " ".join(["111 1 86"] * 11)
        bot2.handle_message(222, f"/br {args}")
        self.assertIn("Bulk max is 10", self.sender.texts()[-1])
        self.assertEqual(store.list_orders(self.conn, 222), [])

    def test_quick_order_invalid_and_unknown_pack(self):
        bot = self.make_bot()
        bot.handle_message(111, "/br 111 1")
        self.assertIn("Usage:", self.sender.texts()[-1])
        bot.handle_message(111, "/br abc 1 86")
        self.assertIn("must all be numbers", self.sender.texts()[-1])
        bot.handle_message(111, "/br 111 1 999")
        self.assertIn("No pack for 999 diamonds", self.sender.texts()[-1])
        self.assertEqual(store.list_orders(self.conn, 111), [])

    def test_ph_gated_without_verified_packs(self):
        bot = self.make_bot()
        bot.handle_message(111, "/ph 111 1 11")
        self.assertIn("aren't available yet", self.sender.texts()[-1])
        bot.handle_callback(111, "region:PH")
        self.assertIn("aren't available yet", self.sender.texts()[-1])
        self.assertEqual(store.list_orders(self.conn, 111), [])

    def test_region_balance_carryover_from_legacy_wallet(self):
        store.credit_balance(self.conn, 111, 50.0)  # old single wallet
        bot = self.make_bot()
        bal = store.get_region_balance(self.conn, 111, "BR")
        self.assertAlmostEqual(bal["balance"], 50.0)
        ph = store.get_region_balance(self.conn, 111, "PH")
        self.assertAlmostEqual(ph["balance"], 0.0)
        bot.handle_message(111, "/balance")
        joined = "\n".join(self.sender.texts())
        self.assertIn("50.00 BRL", joined)
        self.assertIn("0.00 PHP", joined)

    def test_menu_region_pick(self):
        bot = self.make_bot()
        bot.handle_message(111, "/start")
        bot.handle_callback(111, "menu:topup")
        self.assertIn("Pick a region", self.sender.texts()[-1])
        bot.handle_callback(111, "region:BR")
        self.assertIn("Pick a pack", self.sender.texts()[-1])
        bot.handle_callback(111, "pack:BR:5")
        self.assertIn("Send your MLBB ID", self.sender.texts()[-1])

    # -- menu commands: /admin must not appear for everyone -------------
    def test_menu_commands_admin_scoped(self):
        bot = OrderBot("TESTTOKEN", self.conn, PACKS, sender=None,
                       admin_chat_ids="111")
        calls = []
        bot.api = lambda m, p: calls.append((m, p)) or {"ok": True}
        bot.set_commands()
        self.assertEqual(len(calls), 2)
        default_cmds = [c["command"] for c in json.loads(calls[0][1]["commands"])]
        self.assertNotIn("admin", default_cmds)
        self.assertIn("getid", default_cmds)
        self.assertIn("br", default_cmds)
        self.assertEqual(calls[0][1].get("scope"), None)
        scoped = calls[1][1]
        scoped_cmds = [c["command"] for c in json.loads(scoped["commands"])]
        self.assertIn("admin", scoped_cmds)
        self.assertEqual(json.loads(scoped["scope"]),
                         {"type": "chat", "chat_id": 111})

    def test_menu_commands_no_admin_without_admins(self):
        bot = OrderBot("TESTTOKEN", self.conn, PACKS, sender=None,
                       admin_chat_ids="")
        calls = []
        bot.api = lambda m, p: calls.append((m, p)) or {"ok": True}
        bot.set_commands()
        self.assertEqual(len(calls), 1)
        cmds = [c["command"] for c in json.loads(calls[0][1]["commands"])]
        self.assertNotIn("admin", cmds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
