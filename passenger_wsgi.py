"""cPanel Python App (Passenger) entry point for pay.mlbbstore.in.

cPanel shared hosting does NOT run a long-running `python3 webhook_app.py`
process listening on a port. Instead, cPanel's "Setup Python App" runs a
WSGI application through Passenger/Apache.

This file exposes that WSGI application. It reuses the same Bot and
MerchantApp code as webhook_app.py, so Telegram updates and Smile.one
merchant APIs work the same way, just over WSGI instead of a raw socket.

Routes (same as webhook_server.py):
  GET  /                          -> health check
  POST /telegram-webhook          -> Telegram update (or /telegram-webhook/<WEBHOOK_SECRET>)
  POST /api/role-check, /api/game-order, /api/payment-notification,
       /api/server-list, /api/product-list   -> Smile.one merchant APIs

cPanel setup (simple version, see CPANEL.md):
  1. In cPanel, create a Python App with Application root = the folder
     you upload these files to, and Application startup file =
     passenger_wsgi.py, Application Entry point = application.
  2. Upload all bot files to that folder, then clear the old demo
     files from public_html first if you want pay.mlbbstore.in itself
     to serve the bot (or point the Python App's domain to
     pay.mlbbstore.in).
  3. In the Python App's environment variables (or a .env file in the
     app folder), set TELEGRAM_BOT_TOKEN, SMILEONE_MERCHANT_KEY,
     ADMIN_PASSWORD, WEBHOOK_SECRET, FUND_AUTO_CREDIT_TEST.
  4. Restart the Python App in cPanel.
  5. Only then, stop the polling bot hosted elsewhere and run:
       python3 set_webhook.py https://pay.mlbbstore.in/telegram-webhook/<WEBHOOK_SECRET>
     Telegram allows only one delivery mode at a time, so polling +
     webhook together will conflict (error 409).

Env vars are read from the process environment and from a .env file
in this folder, if present. No pip packages are needed - stdlib only.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store
from order_bot import OrderBot, OrderMerchantApp


def _load_env(path):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

def _load_ph_products(base):
    try:
        with open(os.path.join(base, "products_ph.json"),
                  encoding="utf-8") as f:
            return json.load(f).get("products", [])
    except Exception:
        return []


def _save_ph_products(base, products):
    path = os.path.join(base, "products_ph.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        doc = {}
    doc["products"] = products
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return True


_base = os.path.dirname(os.path.abspath(__file__))
_config = {
    "merchant_key": os.environ.get("SMILEONE_MERCHANT_KEY", "1234567890"),
    "products_path": os.path.join(_base, "products.json"),
    "roles_path": os.path.join(_base, "roles.json"),
    "checkout_url_template": os.environ.get("SMILEONE_CHECKOUT_URL_TEMPLATE", ""),
    "admin_chat_ids": os.environ.get("ADMIN_CHAT_IDS", ""),
    "admin_password": os.environ.get("ADMIN_PASSWORD", ""),
    "fund_auto_credit_test": os.environ.get("FUND_AUTO_CREDIT_TEST", "true"),
}
_conn = store.init_db(os.environ.get("DB_PATH", os.path.join(_base, "orders.db")))
_merchant = OrderMerchantApp(_conn, _config, deliver=None, on_delivered=None)
_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_bot = None
if _token:
    _ph_products = _load_ph_products(_base)
    _bot = OrderBot(_token, _conn, _merchant.products, deliver=None,
                    auto_credit_test=_config["fund_auto_credit_test"] == "true",
                    admin_chat_ids=_config["admin_chat_ids"],
                    merchant=_merchant,
                    regions_products={"BR": _merchant.products,
                                      "PH": _ph_products},
                    save_region=lambda region: (
                        _save_ph_products(_base, _ph_products)
                        if region == "PH" else False))
if _bot:
    _merchant.on_delivered = _bot.notify_delivered
_secret = os.environ.get("WEBHOOK_SECRET", "")

_routes = {
    "/api/role-check": _merchant.role_check,
    "/api/game-order": _merchant.game_order,
    "/api/payment-notification": _merchant.payment_notification,
    "/api/server-list": _merchant.server_list,
    "/api/product-list": _merchant.product_list,
}


def _respond(start_response, obj, code=200):
    data = json.dumps(obj).encode()
    start_response(f"{code} OK" if code == 200 else f"{code} Error",
                   [("Content-Type", "application/json"),
                    ("Content-Length", str(len(data)))])
    return [data]


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/") or "/"
    method = environ.get("REQUEST_METHOD", "GET")
    if method == "GET" and path in ("/", "/health"):
        return _respond(start_response, {"ok": True, "service": "mlbb-smile-bot-webhook"})
    if method != "POST":
        return _respond(start_response, {"ok": False, "error": "Not found"}, 404)
    try:
        length = int(environ.get("CONTENT_LENGTH", "0") or 0)
    except Exception:
        length = 0
    raw = environ["wsgi.input"].read(length) if length else b"{}"
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        return _respond(start_response, {"ok": False, "error": "Invalid JSON"}, 400)
    if path == "/telegram-webhook" or (_secret and path == f"/telegram-webhook/{_secret}"):
        if not _bot:
            return _respond(start_response, {"ok": False, "error": "No bot configured"}, 500)
        try:
            _bot.handle_update(body)
        except Exception as e:
            print("[passenger] update error:", e)
        return _respond(start_response, {"ok": True})
    if path.startswith("/telegram-webhook"):
        return _respond(start_response, {"ok": False, "error": "Forbidden"}, 403)
    fn = _routes.get(path)
    if not fn:
        return _respond(start_response, {"status": 500, "error": "Unknown endpoint"})
    try:
        return _respond(start_response, fn(body))
    except Exception as e:
        print("[passenger] merchant error:", e)
        return _respond(start_response, {"status": 500, "error": "System error"})
