"""Run the bot in polling mode (no webhook, no domain, no TLS needed).

For hosts like an Oracle Cloud free VM where the bot only needs OUTBOUND
internet: Telegram delivers updates to getUpdates long polls, and the bot
replies via sendMessage. Nothing listens on any inbound port, so there is
no nginx/certbot/domain setup for the bot itself.

Usage:
  cp .env.example .env   # then edit .env, or export the vars
  python3 poll_bot.py    # systemd: see mlbb-smile-bot.service

Cutover from webhook hosting (e.g. PythonAnywhere):
  1. Start this poller on the new host. It calls deleteWebhook first, so
     Telegram stops delivering to the old webhook URL; queued updates are
     NOT dropped (drop_pending_updates=False) — the poller drains them.
  2. Stop the old webhook app only after the poller is answering.
"""
import json
import os
import signal
import sys
import time

import store
from order_bot import OrderBot, OrderMerchantApp
from app import load_env


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


def build_bot():
    """Same wiring as webhook_app.py, minus the HTTP server."""
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
    if not token:
        print("[poll-bot] TELEGRAM_BOT_TOKEN not set — refusing to start.")
        return None, None
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
    merchant.on_delivered = bot.notify_delivered
    return bot, merchant


def main():
    bot, _merchant = build_bot()
    if bot is None:
        sys.exit(1)
    # Switch Telegram from webhook delivery to polling. Queued updates
    # are kept, so messages sent during the move are not lost.
    try:
        bot.api("deleteWebhook", {"drop_pending_updates": False})
        print("[poll-bot] webhook deleted, polling mode active")
    except Exception as e:
        print(f"[poll-bot] deleteWebhook failed (continuing anyway): {e}")
    try:
        bot.set_commands()
    except Exception as e:
        print(f"[poll-bot] set_commands failed (continuing anyway): {e}")
    stop = {"flag": False}

    def _handle(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    print("[poll-bot] polling started")
    while not stop["flag"]:
        try:
            bot.poll_once()
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[poll-bot] poll error: {e} — retrying in 5s")
            time.sleep(5)
    print("[poll-bot] stopped")


if __name__ == "__main__":
    main()
