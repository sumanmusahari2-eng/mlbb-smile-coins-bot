"""Run the bot in webhook mode (no polling).

Usage:
  cp .env.example .env   # then edit .env, or export the vars
  python3 webhook_app.py
  # then, once your site is live:
  python3 set_webhook.py https://YOUR-DOMAIN/telegram-webhook
"""
import json
import os

import store
from order_bot import OrderBot, OrderMerchantApp
from webhook_server import serve_webhook
from app import load_env


def _load_ph_products(base):
    try:
        with open(os.path.join(base, "products_ph.json"),
                  encoding="utf-8") as f:
            return json.load(f).get("products", [])
    except Exception:
        return []


def _save_ph_products(base, products):
    import os as _os
    path = _os.path.join(base, "products_ph.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        doc = {}
    doc["products"] = products
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    _os.replace(tmp, path)
    return True


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
    merchant = OrderMerchantApp(conn, config, deliver=None, on_delivered=None)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    bot = None
    if token:
        ph_products = _load_ph_products(base)
        bot = OrderBot(token, conn, merchant.products, deliver=None,
                       auto_credit_test=config["fund_auto_credit_test"] == "true",
                       admin_chat_ids=config["admin_chat_ids"],
                       merchant=merchant,
                       regions_products={"BR": merchant.products,
                                         "PH": ph_products},
                       save_region=lambda region: (
                           _save_ph_products(base, ph_products)
                           if region == "PH" else False))
    if bot:
        merchant.on_delivered = bot.notify_delivered
        bot.set_commands()
    else:
        print("[warn] TELEGRAM_BOT_TOKEN not set - merchant API only.")
    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBHOOK_PORT", os.environ.get("PORT", "8000")))
    secret = os.environ.get("WEBHOOK_SECRET", "")
    serve_webhook(bot, merchant, host, port, secret)


if __name__ == "__main__":
    main()
