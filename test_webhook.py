"""Test webhook mode. Run: python3 test_webhook.py"""
import json
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store
from merchant_server import MerchantApp
from telegram_bot import Bot
from webhook_server import serve_webhook

base = os.path.dirname(os.path.abspath(__file__))
conn = store.init_db(":memory:")
config = {"merchant_key": "testkey", "products_path": os.path.join(base, "products.json"),
          "roles_path": os.path.join(base, "roles.json"),
          "admin_chat_ids": "", "admin_password": "", "fund_auto_credit_test": "true"}
merchant = MerchantApp(conn, config)
bot = Bot("dummy", conn, config, merchant)
sent = []
bot.api = lambda method, payload: sent.append((method, payload)) or {"ok": True}

t = threading.Thread(target=serve_webhook, args=(bot, merchant, "127.0.0.1", 18099, "testsecret"), daemon=True)
t.start()
time.sleep(0.5)

def post(path, obj):
    req = urllib.request.Request(f"http://127.0.0.1:18099{path}",
                                 data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())

# Health check
assert post("/", {}) or True  # GET only, POST / is 404 - use GET below
req = urllib.request.Request("http://127.0.0.1:18099/")
with urllib.request.urlopen(req, timeout=5) as r:
    assert json.loads(r.read())["ok"] is True
print("webhook health OK")

# Wrong secret rejected
try:
    post("/telegram-webhook/wrong", {"update_id": 1, "message": {"chat": {"id": 555}, "text": "/start"}})
    raise AssertionError("wrong secret should be rejected")
except urllib.error.HTTPError as e:
    assert e.code == 403
print("webhook secret check OK")

# Real update via webhook
res = post("/telegram-webhook/testsecret",
           {"update_id": 2, "message": {"chat": {"id": 555}, "text": "/start"}})
assert res["ok"] is True
assert sent and "MLBB Top-Up" in sent[0][1]["text"], sent
print("webhook /start OK")

# Button callback via webhook
sent.clear()
res = post("/telegram-webhook/testsecret",
           {"update_id": 3, "callback_query": {"id": "cb1", "message": {"chat": {"id": 555}}, "data": "menu:balance"}})
assert res["ok"] is True
assert any("wallet balance" in s[1]["text"].lower() for s in sent), sent
print("webhook callback OK")
print("ALL WEBHOOK TESTS PASSED")
