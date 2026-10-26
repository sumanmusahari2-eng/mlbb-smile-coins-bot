"""Test menu callbacks. Run: python3 test_menu.py"""
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

bot.handle_message(111, "/start")
assert "Top Up MLBB" in sent[-1][1]["reply_markup"], sent[-1]
print("main menu OK")

bot.handle_callback(111, "menu:balance")
assert "balance" in sent[-2][1]["text"].lower(), sent
print("menu balance OK")

bot.handle_callback(111, "menu:fund")
assert "How much" in sent[-1][1]["text"], sent[-1]
bot.handle_callback(111, "fundamount:100")
assert store.get_balance(conn, 111)["balance"] == 100
print("menu fund amount button OK")

bot.handle_callback(111, "menu:fund")
bot.handle_message(111, "50")
assert store.get_balance(conn, 111)["balance"] == 150
print("menu fund custom amount OK")

bot.handle_callback(111, "menu:topup")
assert "Pick a pack" in sent[-1][1]["text"]
bot.handle_callback(111, "pack:5")
bot.handle_message(111, "123456789")
bot.handle_message(111, "1234")
bot.handle_callback(111, "confirm:yes")
assert store.get_balance(conn, 111)["balance"] == 143.75
print("menu topup flow OK, balance 143.75")

bot.handle_callback(111, "menu:packs")
assert "7740 + 1548" in sent[-2][1]["text"], sent[-2]
print("menu packs list OK, total packs:", len(bot.merchant.products))
assert len(bot.merchant.products) == 16

bot.handle_callback(111, "menu:topup")
assert "page 1/2" in sent[-1][1]["text"], sent[-1]
bot.handle_callback(111, "packpage:1")
assert "page 2/2" in sent[-1][1]["text"], sent[-1]
print("pack pagination OK")

bot.set_commands()
assert sent[-1][0] == "setMyCommands", sent[-1]
print("setMyCommands OK")
print("ALL MENU TESTS PASSED")
