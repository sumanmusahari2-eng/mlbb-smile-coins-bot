# Deploying on SmarterASP.NET trial (60 days) - webhook mode

Your SmarterASP trial is 60 days, no credit card, with a temporary
domain. This guide uses the **webhook** version of the bot, because
shared web hosting is made to answer HTTP requests, not to run a
polling loop forever.

Files you need from this package:
  webhook_app.py, webhook_server.py, telegram_bot.py, merchant_server.py,
  smile_sign.py, store.py, products.json, roles.json, set_webhook.py

## Step 1 - Get your temporary domain
In your SmarterASP panel (member10.smarterasp.net), open your hosting
account and copy the temporary domain / test URL it gives you, e.g.
something like `yourname.site4now.net` (your exact domain is shown in
your panel - use that, do not guess it).

## Step 2 - Upload the files
Upload the files above to your site's root folder via the panel's File
Manager or FTP (details are in your SmarterASP panel).

## Step 3 - Set your settings
Create a `.env` file in the same folder (or set the same values in the
panel's application settings, if it offers environment variables):
```
TELEGRAM_BOT_TOKEN=<your BotFather token - keep this private>
SMILEONE_MERCHANT_KEY=1234567890
ADMIN_PASSWORD=<choose a strong admin password>
FUND_AUTO_CREDIT_TEST=true
WEBHOOK_SECRET=<choose a random secret, e.g. abc123xyz>
WEBHOOK_PORT=8000
DB_PATH=orders.db
```
Never put your bot token in a public file, a chat, or a screenshot.

## Step 4 - Start the webhook app
Start the app the way your SmarterASP plan allows Python to run.
This is the step that varies by plan - if your panel has a "Python
app", "Start command", or "Application" setting, set the start command to:
```
python3 webhook_app.py
```
If your trial panel has NO way to start a Python process, shared
hosting will not run this bot - that is a limit of shared hosting,
not of the bot. In that case use Render or PythonAnywhere instead
(see README), where a start command is a normal setting.

## Step 5 - Point Telegram at your site
Once the site is live, on your own computer run:
```
python3 set_webhook.py https://YOUR-TEMP-DOMAIN/telegram-webhook/<WEBHOOK_SECRET>
```
Replace YOUR-TEMP-DOMAIN with your real temporary domain and
<WEBHOOK_SECRET> with the secret from Step 3. If you did not set a
secret, use `https://YOUR-TEMP-DOMAIN/telegram-webhook`.

Then send /start to @mlbbsmileone_bot (or your bot) on Telegram.
If it replies, webhook mode works.

To go back to polling (the version hosted here), run:
```
python3 set_webhook.py delete
```

## Step 6 - Before the 60 days end
Back up `orders.db` (users, balances, orders) before the trial ends.
After 60 days the site stops unless you upgrade in the panel:
Renewal Notices -> Upgrade Now. Shared plans are paid quarterly or
annually, not monthly.

## What this does NOT change
- Delivery is still a stub, role lookup is still demo data.
- Smile.one merchant APIs are at the same site:
  https://YOUR-TEMP-DOMAIN/api/payment-notification etc.
  Register those public URLs with Smile.one technical staff for live use.
- The bot hosted here must be stopped before you set a webhook,
  because Telegram allows only one delivery mode at a time:
  polling here + webhook there will conflict (error 409).
