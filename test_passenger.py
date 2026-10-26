"""Test passenger_wsgi.py (cPanel) locally via wsgiref. Run: python3 test_passenger.py"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["DB_PATH"] = ":memory:"
os.environ["WEBHOOK_SECRET"] = "testsecret"
os.environ["TELEGRAM_BOT_TOKEN"] = "dummy"

import passenger_wsgi

sent = []
passenger_wsgi._bot.api = lambda method, payload: sent.append((method, payload)) or {"ok": True}


def call(path, obj=None, method="POST"):
    body = json.dumps(obj or {}).encode() if obj is not None else b""
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
    status = {}
    def start_response(s, headers):
        status["status"] = s
    out = b"".join(passenger_wsgi.application(environ, start_response))
    return status["status"], json.loads(out)


code, res = call("/", method="GET")
assert res["ok"] is True, res
print("passenger health OK")

code, res = call("/telegram-webhook/wrong", {"update_id": 1})
assert res["ok"] is False
print("passenger secret check OK")

code, res = call("/telegram-webhook/testsecret",
                 {"update_id": 2, "message": {"chat": {"id": 555}, "text": "/start"}})
assert res["ok"] is True and "MLBB Top-Up" in sent[0][1]["text"], sent
print("passenger /start OK")
print("ALL PASSENGER TESTS PASSED")
