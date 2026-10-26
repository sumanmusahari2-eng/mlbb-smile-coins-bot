"""Test quick order command. Run: python3 test_quick_order.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store
from merchant_server import MerchantApp
from telegram_bot import Bot

base = os.path.dirname(os.path.abspath(__file__))
conn = store.init_db(":memory:")
config = {"merchant_key": "testkey", "products_path": os.path.join(base, "products.json"),
          "roles_path": os.path.join(base, "roles.json"),
          "admin_chat_ids": "", "fund_auto_credit_test": "true"}
merchant = MerchantApp(conn, config)
bot = Bot("dummy", conn, config, merchant)
sent = []
bot.api = lambda method, payload: sent.append((method, payload)) or {"ok": True}

# Unknown pack
bot.handle_message(111, "/999 123 456")
assert "No pack" in sent[-1][1]["text"], sent[-1]
print("unknown pack OK")

# Full quick order, with balance first
bot.handle_message(111, "/addfund 200")
bot.handle_message(111, "/86 603900379 7654")
assert "Please confirm" in sent[-1][1]["text"], sent[-1]
assert "603900379" in sent[-1][1]["text"] and "7654" in sent[-1][1]["text"]
assert "78 + 8 Diamonds" in sent[-1][1]["text"]
print("quick order summary OK")
bot.handle_callback(111, "confirm:yes")
bal = store.get_balance(conn, 111)
assert bal["balance"] == 193.75 and bal["total_spent"] == 6.25, bal
orders = store.list_orders(conn, 111)
assert orders[0]["status"] == "DELIVERED" and orders[0]["uid"] == "603900379" and orders[0]["sid"] == "7654" and orders[0]["amount"] == "6.25", orders
print("quick order deduct+deliver OK:", orders[0])

# Partial: /172 <id> then zone
bot.handle_message(222, "/addfund 500")
bot.handle_message(222, "/172 111222333")
assert "Zone ID" in sent[-1][1]["text"]
bot.handle_message(222, "9999")
assert "Please confirm" in sent[-1][1]["text"]
bot.handle_callback(222, "confirm:yes")
assert store.get_balance(conn, 222)["balance"] == 487.5
print("partial quick order OK")

# /86 alone, then ID, then zone
bot.handle_message(333, "/86")
assert "MLBB ID" in sent[-1][1]["text"]
bot.handle_message(333, "555666777")
bot.handle_message(333, "1234")
assert "Please confirm" in sent[-1][1]["text"]
print("bare /86 flow OK")
print("ALL QUICK ORDER TESTS PASSED")
