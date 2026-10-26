# Host on PythonAnywhere - simple version

Why this one? PythonAnywhere is made for Python. Free plan gives
one web app with HTTPS, which is exactly what a Telegram webhook
bot needs. No card needed for the free plan. You must renew it
about every 3 months on free, or it pauses.

This bot already has a WSGI version: passenger_wsgi.py
It works on PythonAnywhere too, no code change needed.

## Simple steps

1. Go to pythonanywhere.com, sign up for a free Beginner account.
   Pick a username - your bot site will be:
   https://YOURNAME.pythonanywhere.com
2. On the Files tab, upload the bot zip and unzip it, or upload
   the files one by one into a folder, e.g. /home/YOURNAME/bot/
   Files needed: passenger_wsgi.py, telegram_bot.py,
   merchant_server.py, smile_sign.py, store.py, products.json,
   roles.json, set_webhook.py
3. On the Web tab, click "Add a new web app", choose Manual
   configuration, Python 3.10 or newer.
4. In the WSGI configuration file (the link on the Web tab),
   replace its content with:
       import sys
       sys.path.insert(0, '/home/YOURNAME/bot')
       from passenger_wsgi import application
   Replace YOURNAME with your real username, and /bot with
   your real folder.
5. In the same folder, make a .env file with:
       TELEGRAM_BOT_TOKEN=<your token, keep private>
       SMILEONE_MERCHANT_KEY=1234567890
       ADMIN_PASSWORD=<choose a strong password>
       WEBHOOK_SECRET=<choose a random secret>
       FUND_AUTO_CREDIT_TEST=true
   Note: the .env file is read by passenger_wsgi.py at start.
6. On the Web tab, click Reload. Then open:
   https://YOURNAME.pythonanywhere.com/
   It should show: {"ok": true, "service": "mlbb-smile-bot-webhook"}
7. ONLY when step 6 works, tell Muse to stop the polling bot
   hosted here, then run:
   python3 set_webhook.py https://YOURNAME.pythonanywhere.com/telegram-webhook/<WEBHOOK_SECRET>
   Then send /start to the bot on Telegram to test.

## If you want to stay on EricHost
EricHost cPanel is still blocked for Muse, so that route needs
you to open cPanel yourself, or EricHost support to fix it.
PythonAnywhere does not have that block, and Muse can try to
help you there in the browser once you have an account.
