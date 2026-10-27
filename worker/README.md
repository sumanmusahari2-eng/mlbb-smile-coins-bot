# MLBB Smile Coins bot — Cloudflare Workers version

A faithful port of the Python bot (`order_bot.py` + `merchant_server.py`)
to a Cloudflare Worker: webhook mode, D1 database, no server to keep
alive. Same commands, messages, bulk rules, admin gating and honest
limits. Differences forced by Workers are listed at the bottom.

## Deploy (needs a working Cloudflare login — see note below)

```bash
npm install -g wrangler
wrangler login            # browser OAuth; needs your Cloudflare session
cd worker
wrangler d1 create mlbb-smile-bot-db
# paste the returned database_id into wrangler.toml
wrangler d1 execute mlbb-smile-bot-db --file=schema.sql
wrangler secret put TELEGRAM_BOT_TOKEN
wrangler secret put SMILEONE_MERCHANT_KEY
wrangler secret put ADMIN_CHAT_IDS        # e.g. 123456789
wrangler secret put WEBHOOK_SECRET        # any random string
wrangler secret put SETUP_SECRET          # any random string
wrangler deploy
```

Then visit (once):

```
https://<your-worker>.workers.dev/setup?key=<SETUP_SECRET>
```

That sets the Telegram webhook to this Worker and installs the menu
commands (default list without /admin; your account gets /admin too).
Check `/health` returns ok, then test /start on @mlbbsmileone_bot.

## Cutover from PythonAnywhere

1. Deploy the Worker and run /setup (Telegram now delivers to the
   Worker; the old webhook stops receiving anything).
2. Migrate balances (below) BEFORE users place orders on the Worker,
   or their wallets start at 0.
3. Leave the PythonAnywhere app up until the Worker is verified, then
   stop it. The old webhook URL receives nothing once /setup runs,
   so there is no double-delivery window.

## Balances migration (wallets live in the old orders.db)

On the PythonAnywhere console, export wallets to JSON:

```bash
python3 -c "
import sqlite3, json
c = sqlite3.connect('/home/meowjiofficial/orders.db')
def rows(t):
    cur = c.execute(f'SELECT * FROM {t}')
    return [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
print(json.dumps({'users': rows('users'), 'region_balances': rows('region_balances')}))
" > wallets.json
```

Then generate D1 statements and apply them:

```bash
python3 migrate-balances.py wallets.json > wallets.sql
wrangler d1 execute mlbb-smile-bot-db --file=wallets.sql
```

Migrated region_balances rows win: the Worker's "legacy balance carries
into BR once" seeding only fires when no region row exists yet.

## Tests (no Cloudflare account needed)

```bash
node --test test/worker.test.js
```

Runs the real bot/merchant/sign code against a fake D1 and a recording
sender: /start, quick orders, bulk rules, menu flow, admin gating,
PH gating, merchant endpoints, webhook secret check.

## Known differences from the Python bot

- Webhook only — no polling mode (Workers cannot hold a long poll).
- Sessions live in D1, products live in D1 (no files on Workers).
- Wallet deduct is one atomic UPDATE (no locks needed on D1).
- Delivery stays unwired, exactly like live: a Smile.one SUCCESS
  notification for an undelivered order answers 201 and the order
  stays PENDING — never fake DELIVERED.
- Role lookup is the same DEMO table as roles.json. Replace it with a
  real game-account lookup before production use.
- Order IDs use UTC timestamps (Python used local time) — cosmetic.
- The Python bot's fund-request approval flow is not wired in the new
  bot either; the fund_requests table exists for parity.
- FUND_AUTO_CREDIT_TEST=true keeps /addfund instant test credit on.
  Turn it off before any real use (wrangler secret put is for secrets;
  this one is a var in wrangler.toml).
