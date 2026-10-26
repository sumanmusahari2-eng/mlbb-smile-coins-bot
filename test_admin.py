"""Test admin panel. Run: python3 test_admin.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store
from merchant_server import MerchantApp
from telegram_bot import Bot

base = os.path.dirname(os.path.abspath(__file__))
conn = store.init_db(":memory:")
config = {"merchant_key": "testkey", "products_path": os.path.join(base, "products.json"),
          "roles_path": os.path.join(base, "roles.json"),
          "admin_chat_ids": "", "admin_password": "testadmin123", "fund_auto_credit_test": "false"}
merchant = MerchantApp(conn, config)
bot = Bot("dummy", conn, config, merchant)
sent = []
bot.api = lambda method, payload: sent.append((method, payload)) or {"ok": True}

# Seed a user, a fund request, an order
store.credit_balance(conn, 111, 100)
req_id = store.create_fund_request(conn, 111, 50)
oid = store.create_order(conn, 111, "123456789", "1234", "5", "6.25", "BRL")

# Wrong password denied
bot.handle_message(999, "/admin wrong")
assert "Wrong admin password" in sent[-1][1]["text"]
print("admin wrong password OK")

# Correct password opens panel
bot.handle_message(999, "/admin testadmin123")
assert "Admin Panel" in sent[-1][1]["text"], sent[-1]
print("admin login OK")

# Users
bot.handle_callback(999, "admin:users")
assert "111" in sent[-1][1]["text"] or "User 111" in str(sent[-1])
print("admin users OK")

# User detail + adjust
bot.handle_callback(999, "admin:user:111")
assert "100.00" in sent[-1][1]["text"]
bot.handle_callback(999, "admin:adjust:111")
bot.handle_message(999, "25")
assert store.get_balance(conn, 111)["balance"] == 125
print("admin adjust OK")

# Funds approve
bot.handle_callback(999, "admin:funds")
bot.handle_callback(999, f"admin:fund:{req_id}")
bot.handle_callback(999, f"admin:approve:{req_id}")
assert store.get_balance(conn, 111)["balance"] == 175
print("admin approve fund OK")

# Funds reject
req2 = store.create_fund_request(conn, 111, 999)
bot.handle_callback(999, f"admin:reject:{req2}")
assert store.get_fund_request(conn, req2)["status"] == "REJECTED"
print("admin reject fund OK")

# Orders + stats + packs
bot.handle_callback(999, "admin:orders")
assert oid in str(sent[-1])
bot.handle_callback(999, f"admin:order:{oid}")
assert oid in sent[-1][1]["text"]
bot.handle_callback(999, "admin:stats")
assert "Stats" in sent[-2][1]["text"], sent[-2]
print("admin orders/stats OK")
print("ALL ADMIN TESTS PASSED")
