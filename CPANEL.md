# Deploying on pay.mlbbstore.in (EricHost cPanel) - simple version

pay.mlbbstore.in is a Shared Starter cPanel account, Active, due
16/11/2026. Its old site is a small demo store, and you said you no
longer need it, so clearing its public_html is OK.

IMPORTANT: cPanel could NOT be opened from Muse's network, so Muse
cannot upload the files for you from here. You (or someone on a
network that can open cPanel) must do the upload steps below. Muse
can still help after upload, by setting the webhook and testing.

## Simple steps

1. Log in to EricHost portal, open cPanel for pay.mlbbstore.in.
2. In cPanel File Manager, open public_html.
   - If you want to keep the old demo site, zip it and download it first.
   - Then delete the files inside public_html. Do NOT delete the
     main mlbbstore.in site - there is none in this EricHost account,
     and do NOT touch paysafe.mlbbstore.in.
3. Upload all files from the bot zip to the Python App folder
   (or to public_html if your Python App uses that folder).
   Files: passenger_wsgi.py, telegram_bot.py, merchant_server.py,
   smile_sign.py, store.py, products.json, roles.json,
   webhook_app.py, webhook_server.py, set_webhook.py
4. In cPanel, open "Setup Python App" and create an app:
   - Python version: 3.9 or newer, any version shown
   - Application root: the folder you uploaded to
   - Application URL: pay.mlbbstore.in
   - Application startup file: passenger_wsgi.py
   - Application Entry point: application
5. In that Python App, add these environment variables
   (or make a .env file in the app folder with the same lines):
   TELEGRAM_BOT_TOKEN=<your BotFather token - keep private>
   SMILEONE_MERCHANT_KEY=1234567890
   ADMIN_PASSWORD=<choose a strong admin password>
   WEBHOOK_SECRET=<choose a random secret>
   FUND_AUTO_CREDIT_TEST=true
6. Restart the Python App in cPanel, then open in a browser:
   https://pay.mlbbstore.in/
   It should show: {"ok": true, "service": "mlbb-smile-bot-webhook"}
   If it does not, the Python App did not start - check its log in cPanel.
7. ONLY when step 6 works, stop the polling bot hosted elsewhere
   (tell Muse to stop it), then on your own computer run:
   python3 set_webhook.py https://pay.mlbbstore.in/telegram-webhook/<WEBHOOK_SECRET>
   Then send /start to the bot on Telegram to test.

## If cPanel has no Python App option
Then shared cPanel cannot run this bot, and the fallback is a VPS
or Render / PythonAnywhere (see README). Do not try to run
`python3 webhook_app.py` in a browser - cPanel websites do not
run long processes that way.

## What does NOT change
- Delivery is still a stub, role lookup is still demo data.
- Smile.one merchant APIs will be at:
  https://pay.mlbbstore.in/api/payment-notification etc.
  Register those URLs with Smile.one technical staff for live use.
