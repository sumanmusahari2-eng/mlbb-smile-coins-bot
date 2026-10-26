"""Test admin set-price flow. Run: python3 test_admin_price.py
Uses a temp copy of products.json so real prices are never touched."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store
from merchant_server import MerchantApp
from telegram_bot import Bot

base = os.path.dirname(os.path.abspath(__file__))
tmpdir = tempfile.mkdtemp()
tmp_products = os.path.join(tmpdir, "products.json")
shutil.copy(os.path.join(base, "products.json"), tmp_products)

conn = store.init_db(":memory:")
config = {"merchant_key": "testkey", "products_path": tmp_products,
          "roles_path": os.path.join(base, "roles.json"),
          "admin_chat_ids": "999", "admin_password": "", "fund_auto_credit_test": "false"}
merchant = MerchantApp(conn, config)
bot = Bot("dummy", conn, config, merchant)
sent = []
bot.api = lambda method, payload: sent.append((method, payload)) or {"ok": True}

# Non-admin blocked from admin packs
bot.handle_callback(111, "admin:packs")
assert "Admin access only" in sent[-1][1]["text"]
print("non-admin blocked OK")

# Admin packs list has a button per pack
bot.handle_callback(999, "admin:packs")
kb = json.loads(sent[-1][1]["reply_markup"])["inline_keyboard"]
callbacks = [b["callback_data"] for row in kb for b in row]
assert "admin:pack:5" in callbacks, callbacks
assert all(len(b["text"].encode()) <= 64 for row in kb for b in row), "button label over 64 bytes"
print("admin packs list OK")

# Pack detail + set price prompt
bot.handle_callback(999, "admin:pack:5")
assert "78 + 8" in sent[-1][1]["text"] and "6.25" in sent[-1][1]["text"]
bot.handle_callback(999, "admin:setprice:5")
assert "new price" in sent[-1][1]["text"]
print("pack detail + prompt OK")

# Invalid price rejected, price unchanged
bot.handle_message(999, "abc")
assert "valid price" in sent[-1][1]["text"]
assert merchant.find_product("5")["price"] == "6.25"
bot.handle_message(999, "0")
assert "valid price" in sent[-1][1]["text"]
assert merchant.find_product("5")["price"] == "6.25"
print("invalid price rejected OK")

# Valid price: in-memory + file both updated, note preserved
bot.handle_message(999, "7.25")
assert "7.25" in sent[-1][1]["text"], sent[-1]
assert merchant.find_product("5")["price"] == "7.25"
with open(tmp_products, encoding="utf-8") as f:
    doc = json.load(f)
saved = [p for p in doc["products"] if p["pid"] == "5"][0]
assert saved["price"] == "7.25", saved
assert "_note" in doc and "servers" in doc, "document lost fields on save"
print("price saved to file OK")

# New price shows in the customer packs list too
assert "7.25" in bot.packs_text()
print("packs list reflects new price OK")

shutil.rmtree(tmpdir)
print("ALL ADMIN PRICE TESTS PASSED")
