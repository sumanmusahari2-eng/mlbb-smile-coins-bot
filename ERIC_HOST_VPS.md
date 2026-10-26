# EricHost move — simple checklist (test bot first)

## Do not buy shared hosting just for this bot
This bot runs as a process that must stay on all day (Telegram polling,
see `app.py`). Shared cPanel hosting can run a website, but it does not
give you root or a systemd service to keep a bot process alive — the
process can be stopped when you log out. That is why the current test bot
goes offline when this machine restarts.

## The right EricHost tier for the bot: a VPS
A VPS gives root access and lets you install the included
`mlbb-bot.service` so the bot restarts itself.

Prices below are from a third-party review of EricHost, NOT confirmed
inside an EricHost account — check the exact price inside your account
before paying, and note taxes:
- General VPS: from ₹399/month
- India VPS (Mumbai, KVM): from ₹449/month
- Managed VPS: from ₹599/month
- That review also says VPS is excluded from the usual 30-day
  money-back guarantee for shared hosting — confirm this before paying.

Shared Business plans (from ₹99/month, mentioned earlier) are cheaper,
but they are the wrong tier for an always-on bot process.

## What the VPS needs (simple setup)
1. A Linux VPS with Python 3 — no extra pip packages are needed.
   The bot uses only Python's standard library.
2. Upload the bot zip, unzip it on the VPS.
3. Create a `.env` file in the bot folder (chmod 600) with the bot token
   and admin settings. Never put the token inside the zip or in chat
   unless you choose that transient path.
4. Copy `mlbb-bot.service` to `/etc/systemd/system/`, edit its
   WorkingDirectory to the VPS bot folder, then:
   `sudo systemctl daemon-reload`
   `sudo systemctl enable --now mlbb-bot`
   `sudo systemctl status mlbb-bot`
5. Test in Telegram: `/start`, then `/balance` on @mlbbsmileone_bot.

## What a VPS alone does NOT finish
The merchant API in `app.py` currently listens only on 127.0.0.1:8000.
A VPS lets the test bot stay online, but live Smile.one payment
notifications still need a public HTTPS address later. That is a
separate step — do not treat "bot is on EricHost" as "live sales work".

## Still open — one question before buying or checking a panel
- Do you already have an EricHost login/account? If yes, is it a
  shared plan or a VPS? If it is shared, tell Muse before paying for
  anything else, so the plan is not bought twice.
- SmarterASP is still parked and NOT activated — leave it alone for now.
