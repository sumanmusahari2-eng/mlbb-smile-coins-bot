# Hosting notes

## What I can host here (this Muse VM) for testing
- Telegram polling only: the bot answering /start, packs, MLBB ID/Zone,
  and pending orders. That needs only outbound calls to api.telegram.org,
  so it can run here.
- I can start it with `run_hosted.sh` and restart it if it crashes.

## Limits you should know before sharing a token
- A Telegram bot token gives full control of that bot. Anyone with it
  can send messages as the bot. For a test, use a separate test bot
  from @BotFather if you can, and revoke/rotate the token in BotFather
  (`/revoke`) when you are done testing.
- Do NOT paste the token in chat unless you explicitly choose that
  transient path. I will use it only to run this bot, I will not save
  it in memory, and I will not put it in a file you can download.
  The safest local file for it is a `.env` with `chmod 600`.
- This VM can be replaced/restarted. A bot running here can go offline
  until it is restarted. That is fine for testing, not ideal for live sales.
- Live Smile.one payment notifications need a public HTTPS URL for
  your merchant API. This VM does not give you a public domain for that,
  so live Smile Coins delivery still needs a VPS with HTTPS (see
  `mlbb-bot.service` for the systemd template) or a tunnel you control.

## If you want live hosting later
Use a small VPS, HTTPS reverse proxy, the systemd service in this
folder, and register the public merchant URLs with Smile.one.
