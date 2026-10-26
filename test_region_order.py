"""Test region quick order. Run: python3 test_region_order.py"""
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

bot.handle_message(111, "/addfund 200")
bot.handle_message(111, "/brl 603803877 5674 86")
assert "Please confirm" in sent[-1][1]["text"], sent[-1]
assert "Brazil" in sent[-1][1]["text"] and "BRL" in sent[-1][1]["text"]
assert "603803877" in sent[-1][1]["text"] and "5674" in sent[-1][1]["text"]
print("region summary OK")
bot.handle_callback(111, "confirm:yes")
bal = store.get_balance(conn, 111)
assert bal["balance"] == 193.75, bal
orders = store.list_orders(conn, 111)
assert orders[0]["status"] == "DELIVERED" and orders[0]["currency"] == "BRL" and orders[0]["amount"] == "6.25", orders
assert orders[0]["uid"] == "603803877" and orders[0]["sid"] == "5674"
print("region order OK:", orders[0])

# Unknown region should fall through to normal handling (menu), not crash
bot.handle_message(222, "/xyz 123 456 86")
print("unknown region fallthrough OK, last:", sent[-1][1]["text"][:60])

# Partial region: /php <id> <zone>, then diamonds
bot.handle_message(333, "/addfund 500")
bot.handle_message(333, "/brl 111222333 9999")
assert "diamonds amount" in sent[-1][1]["text"]
bot.handle_message(333, "172")
assert "Please confirm" in sent[-1][1]["text"] and "Brazil" in sent[-1][1]["text"]
bot.handle_callback(333, "confirm:yes")
assert store.get_balance(conn, 333)["balance"] == 487.5
print("partial region order OK")

# /brl alone
bot.handle_message(444, "/brl")
assert "MLBB ID" in sent[-1][1]["text"]
print("bare /brl OK")
print("ALL REGION TESTS PASSED")
