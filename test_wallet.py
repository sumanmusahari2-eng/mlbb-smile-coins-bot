"""Test wallet + bot handler logic without Telegram network.

Run: python3 test_wallet.py
Bot.api is stubbed to capture sendMessage calls instead of calling Telegram.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store
from merchant_server import MerchantApp
from telegram_bot import Bot

base = os.path.dirname(os.path.abspath(__file__))
conn = store.init_db(":memory:")
config = {"merchant_key": "testkey", "products_path": os.path.join(base, "products.json"),
          "roles_path": os.path.join(base, "roles.json"),
          "admin_chat_ids": "999", "fund_auto_credit_test": "true"}
merchant = MerchantApp(conn, config)
bot = Bot("dummy", conn, config, merchant)
sent = []
bot.api = lambda method, payload: sent.append((method, payload)) or {"ok": True}

# /balance starts at 0
bot.handle_message(111, "/balance")
assert "0.00" in sent[-1][1]["text"], sent[-1]
print("balance 0 OK")

# /addfund auto-credit (test mode)
bot.handle_message(111, "/addfund 100")
assert store.get_balance(conn, 111)["balance"] == 100, store.get_balance(conn, 111)
print("addfund auto-credit OK:", store.get_balance(conn, 111))

# Order flow: Brazil pack 5 = 78+8 diamonds, R$ 6.25
bot.handle_callback(111, "pack:5")
bot.handle_message(111, "123456789")
bot.handle_message(111, "1234")
bot.handle_callback(111, "confirm:yes")
bal = store.get_balance(conn, 111)
assert bal["balance"] == 93.75 and bal["total_spent"] == 6.25, bal
orders = store.list_orders(conn, 111)
assert orders[0]["status"] == "DELIVERED", orders
print("wallet checkout OK, remaining:", bal, "order:", orders[0]["game_order"])

# Insufficient balance: pack 12 = 7740+1548, R$ 625, balance 93.75
bot.handle_callback(111, "pack:12")
bot.handle_message(111, "123456789")
bot.handle_message(111, "1234")
bot.handle_callback(111, "confirm:yes")
assert "Not enough balance" in sent[-1][1]["text"], sent[-1]
print("insufficient balance OK")

# Live mode: fund request + admin approval
bot.auto_credit = False
bot.handle_message(222, "/addfund 50")
reqs = store.list_pending_funds(conn)
assert reqs and reqs[0]["amount"] == 50, reqs
bot.handle_message(222, "/approvefund 999")  # non-admin, and bad id - should deny
assert store.get_balance(conn, 222)["balance"] == 0
bot.handle_message(999, f"/approvefund {reqs[0]['id']}")
assert store.get_balance(conn, 222)["balance"] == 50, store.get_balance(conn, 222)
print("admin approval OK")
print("ALL WALLET TESTS PASSED")
