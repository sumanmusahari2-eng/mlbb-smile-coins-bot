"""Set / delete the Telegram webhook for this bot.

Usage:
  python3 set_webhook.py https://YOUR-DOMAIN/telegram-webhook
  python3 set_webhook.py https://YOUR-DOMAIN/telegram-webhook/<secret>
  python3 set_webhook.py delete

Token comes from TELEGRAM_BOT_TOKEN env, or a .env file in this folder.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import load_env  # noqa: E402

load_env()
token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not token:
    print("TELEGRAM_BOT_TOKEN not set. Put it in .env or export it.")
    sys.exit(1)
if len(sys.argv) < 2:
    print("Usage: python3 set_webhook.py <webhook-url>  OR  python3 set_webhook.py delete")
    sys.exit(1)

if sys.argv[1] == "delete":
    method, payload = "deleteWebhook", {}
    print("Deleting webhook (bot will go back to polling mode)...")
else:
    method = "setWebhook"
    payload = {"url": sys.argv[1]}
    print(f"Setting webhook to {sys.argv[1]} ...")

data = urllib.parse.urlencode(payload).encode()
req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
with urllib.request.urlopen(req, timeout=30) as r:
    print(json.loads(r.read()))
print("Done. Send /start to the bot to test it.")
