"""Webhook server for shared / web hosts (e.g. SmarterASP trial, Render, PythonAnywhere).

Unlike app.py (polling), this server does NOT poll Telegram. Telegram
POSTs updates to it, so the host only needs to serve HTTP - which is
what shared web hosting is made for.

Routes:
  GET  /                          -> health check
  POST /telegram-webhook          -> Telegram update (or /telegram-webhook/<WEBHOOK_SECRET>)
  POST /api/role-check, /api/game-order, /api/payment-notification,
       /api/server-list, /api/product-list   -> Smile.one merchant APIs

Env: same as app.py, plus WEBHOOK_SECRET (optional, recommended),
WEBHOOK_HOST (default 0.0.0.0), WEBHOOK_PORT (default $PORT or 8000).

Set the webhook once your site is live:
  python3 set_webhook.py https://YOUR-DOMAIN/telegram-webhook
  (with a secret: python3 set_webhook.py https://YOUR-DOMAIN/telegram-webhook/<secret>)
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from merchant_server import MerchantApp


def make_webhook_handler(bot, merchant: MerchantApp, secret=""):
    routes = {
        "/api/role-check": merchant.role_check,
        "/api/game-order": merchant.game_order,
        "/api/payment-notification": merchant.payment_notification,
        "/api/server-list": merchant.server_list,
        "/api/product-list": merchant.product_list,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            print("[webhook]", *args)

        def _send_json(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/health"):
                return self._send_json({"ok": True, "service": "mlbb-smile-bot-webhook"})
            return self._send_json({"ok": False, "error": "Not found"}, 404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                return self._send_json({"ok": False, "error": "Invalid JSON"}, 400)
            # Telegram webhook
            if self.path == "/telegram-webhook" or (
                    secret and self.path == f"/telegram-webhook/{secret}"):
                if not bot:
                    return self._send_json({"ok": False, "error": "No bot configured"}, 500)
                try:
                    bot.handle_update(body)
                except Exception as e:
                    print("[webhook] update error:", e)
                return self._send_json({"ok": True})
            if self.path.startswith("/telegram-webhook"):
                return self._send_json({"ok": False, "error": "Forbidden"}, 403)
            # Smile.one merchant APIs
            fn = routes.get(self.path)
            if not fn:
                return self._send_json({"status": 500, "error": "Unknown endpoint"})
            try:
                return self._send_json(fn(body))
            except Exception as e:
                print("[webhook] merchant error:", e)
                return self._send_json({"status": 500, "error": "System error"})

    return Handler


def serve_webhook(bot, merchant, host, port, secret=""):
    server = ThreadingHTTPServer((host, port), make_webhook_handler(bot, merchant, secret))
    print(f"[webhook] listening on http://{host}:{port}")
    server.serve_forever()
