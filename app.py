"""Run both parts together:
  - Merchant HTTP API for Smile.one (port MERCHANT_PORT, default 8000)
  - Telegram bot polling (needs TELEGRAM_BOT_TOKEN)

Env vars (see .env.example):
  TELEGRAM_BOT_TOKEN, SMILEONE_MERCHANT_KEY,
  SMILEONE_CHECKOUT_URL_TEMPLATE (optional),
  MERCHANT_HOST, MERCHANT_PORT, DB_PATH

Usage:
  cp .env.example .env   # then edit .env, or export the vars
  python3 app.py         # loads .env if present, no pip packages needed
"""
import os
import threading

import store
from merchant_server import MerchantApp, serve
from telegram_bot import Bot


def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    load_env()
    base = os.path.dirname(os.path.abspath(__file__))
    config = {
        "merchant_key": os.environ.get("SMILEONE_MERCHANT_KEY", "1234567890"),
        "products_path": os.path.join(base, "products.json"),
        "roles_path": os.path.join(base, "roles.json"),
        "checkout_url_template": os.environ.get("SMILEONE_CHECKOUT_URL_TEMPLATE", ""),
        "admin_chat_ids": os.environ.get("ADMIN_CHAT_IDS", ""),
        "admin_password": os.environ.get("ADMIN_PASSWORD", ""),
        "fund_auto_credit_test": os.environ.get("FUND_AUTO_CREDIT_TEST", "true"),
    }
    conn = store.init_db(os.environ.get("DB_PATH", os.path.join(base, "orders.db")))
    bot = None
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    merchant = MerchantApp(conn, config, on_delivered=None)
    if token:
        bot = Bot(token, conn, config, merchant)
        merchant.on_delivered = bot.notify_delivered
    else:
        print("[warn] TELEGRAM_BOT_TOKEN not set - merchant API only, no Telegram polling.")
    host = os.environ.get("MERCHANT_HOST", "127.0.0.1")
    port = int(os.environ.get("MERCHANT_PORT", "8000"))
    thread = threading.Thread(target=serve, args=(merchant, host, port), daemon=True)
    thread.start()
    if bot:
        bot.run()
    else:
        thread.join()


if __name__ == "__main__":
    main()
