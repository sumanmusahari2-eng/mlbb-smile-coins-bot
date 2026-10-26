# MLBB Telegram Top-Up Bot — pay with existing Smile Coins

A working prototype, built from the Smile.one Docs. No pip packages needed — Python 3 only.

## What this is, and what it is NOT

**This bot implements the merchant side exactly as the Smile.one Docs describe it:**
Smile.one calls *your* endpoints. You do not call Smile.one to debit coins.

- `POST /api/role-check` — verify MLBB ID + Zone ID, return nickname
- `POST /api/game-order` — create an order, return `game_order`
- `POST /api/payment-notification` — Smile.one tells you the payment result;
  only `trade_status = SUCCESS` triggers delivery, and a duplicate SUCCESS
  returns 204 (already delivered) instead of delivering twice
- `POST /api/server-list` and `POST /api/product-list` — servers and packs/prices
- Signing on every call: sort fields, join as `key=value`, append the
  merchant key, double-MD5. Verification also accepts the URL-encoded
  variant, because the docs' prose and PHP example differ on encoding.

## Quick order

Send a single command in this format, with the diamond amount first,
then MLBB ID, then Zone ID (server):

```
/86 603900379 7654
```

That shows a Confirm summary first — tap ✅ Confirm to deduct from
your wallet balance and place the order. This Confirm tap is deliberate:
it stops a typo in the ID from instantly spending coins.

### Region order

```
/brl 603803877 5674 86
```

Format: `/<region> <MLBB ID> <Zone ID> <diamonds>`. Supported regions
include brl (Brazil), php (Philippines), idr (Indonesia), myr (Malaysia),
sgd, thb, vnd, inr, usd, eur, try, mxn, ars, plus short aliases like
br, ph, id, my. The order is stored with that region's currency.
Prices are still placeholders (numeric price = diamond amount) — supply
region-specific prices before live use. Partial forms also work:
`/brl 603803877 5674` (bot asks for diamonds), `/brl 603803877`
(bot asks for Zone, then diamonds), `/brl` alone (bot asks for ID).
Partial forms also work: `/86 603900379` (bot then asks for Zone ID),
or `/86` alone (bot then asks for ID, then Zone ID). If the diamond
amount is not a pack, the bot tells you to check `/packs`.
Note: numeric commands like `/86` are typed manually — they do not
appear in Telegram's Menu button list, which only shows named commands.

## Admin panel - Brazil only, for now

In Telegram, send `/admin` then your admin password, or `/admin <password>`
in one message. The panel (also in the Menu button) lets you manage:
- 📊 Stats: users, orders, delivered, pending funds, total balance/funded/spent
- 👥 Users: list users, tap one for balance/funded/spent + recent orders, Adjust balance (send 100 to add, -50 to deduct)
- 💰 Funds: list pending fund requests, tap one to Approve or Reject (the user is notified)
- 📦 Orders: list recent orders, tap one for full details (user, MLBB ID/Zone, product, amount, currency, status)
- 💎 Packs: view the Brazil pack list

Access control: set `ADMIN_PASSWORD` in `.env` for password login, or
`ADMIN_CHAT_IDS` (comma-separated Telegram chat ids) for password-free
admin access for those chats only. Anyone with the password can manage
the bot, so change it for live use and do not share it publicly.

## Menu - Brazil only, for now

`/start` opens a main menu with buttons: Top Up MLBB, Packs, Balance,
Add Fund, My Orders, Help. The Telegram Menu button (bottom-left) also
lists the commands: `/start`, `/packs`, `/balance`, `/addfund`, `/orders`, `/help`.
`/packs` (or the Packs button) lists only the official Brazil packs
(16 packs, in BRL, checked 2026-10-26 on Smile.one Brazil).
Top Up shows packs 8 per page, with Prev / Next buttons.
Other regions were removed on the user's instruction - re-add them
only after their official packs/prices are checked.

### Brazil packs (BRL)
- 50 + 5: R$ 4.00, 150 + 15: R$ 11.99, 250 + 25: R$ 19.75, 500 + 65: R$ 40.50
- 78 + 8: R$ 6.25, 156 + 16: R$ 12.50, 234 + 23: R$ 18.67, 625 + 81: R$ 50.00
- 1860 + 335: R$ 150.00, 3099 + 589: R$ 250.00, 4649 + 883: R$ 375.00, 7740 + 1548: R$ 625.00
- Twilight Pass: R$ 41.25, Weekly Diamond Pass (~220 total): R$ 8.00
- Weekly Elite Pack (55 + bonuses, once a week): R$ 4.00
- Monthly Epic Pack (275 + bonuses, once a month): R$ 19.75

Note on Smile Coins: on Smile.one Brazil, prices are shown in BRL and
Smile Coin is a payment method (e.g. the R$ 18.67 pack was shown as
176.7 Smile Coins). The bot's test wallet currently deducts the BRL
amount numerically - do not use that as the real Smile Coin conversion
rate for live payments.
Add Fund from the menu offers 50 / 100 / 200 / 500 coins, or you can
just send a custom number.

**Telegram flow:**
`/start` → menu → Top Up → pick a pack → send MLBB ID → send Zone ID → confirm nickname
→ if your wallet balance covers the price, it is deducted and the order
is marked delivered; if not, the bot tells you the shortfall.
Delivery message is also sent when a SUCCESS payment notification arrives.

## Wallet commands

- `/balance` — your Smile Coins balance, total added, total spent
- `/addfund <amount>` (also `/fund`) — add funds
  - Test mode (`FUND_AUTO_CREDIT_TEST=true`, the default): credits instantly,
    and the bot says so. Use this only for testing.
  - Live mode (`FUND_AUTO_CREDIT_TEST=false`): creates a PENDING request.
    An admin whose Telegram chat id is in `ADMIN_CHAT_IDS` approves it
    with `/approvefund <request_id>`; `/pendingfunds` lists pending requests.
    In live mode, replace the approval step with real Smile.one payment
    confirmation before crediting — do not auto-credit real money.

## What the docs do NOT give you (read before going live)

1. **No direct Smile Coins debit / checkout URL.** The docs publish no
   Smile.one base URL to call. If `SMILEONE_CHECKOUT_URL_TEMPLATE` is set,
   the bot sends that link. If it is not set, the bot honestly tells the
   user the order is pending until Smile.one notifies it. Confirm the
   checkout link format and register your public merchant endpoints with
   Smile.one technical staff — they also issue the merchant encryption key.
2. **No real MLBB delivery.** `deliver_items()` in `merchant_server.py`
   is a stub that only logs. Replace it with your real game-delivery call.
   Do not claim live delivery until that is tested.
3. **Demo role lookup only.** `roles.json` contains two demo players.
   Replace `verify_role()` with your real game-account lookup.
4. **Placeholder prices.** `products.json` packs are editable placeholders,
   not official MLBB / Smile.one prices. Edit them to match your real
   Smile.one product table (pid, price, currency).

## Run it

1. Create a bot in Telegram: talk to @BotFather, `/newbot`, copy the token.
   That token is a secret — put it in `.env`, never in chat or in git.
2. Copy the config and edit it:
   ```
   cp .env.example .env
   # set TELEGRAM_BOT_TOKEN and SMILEONE_MERCHANT_KEY in .env
   # optionally set SMILEONE_CHECKOUT_URL_TEMPLATE, e.g.
   # https://your-smileone-product-page?order={game_order}&uid={uid}&productid={productid}
   ```
3. Edit `products.json` (real packs/prices) and `roles.json` / `verify_role()`.
4. Test the merchant API locally:
   ```
   python3 test_endpoints.py
   ```
5. Run:
   ```
   python3 app.py
   ```
   The merchant API listens on `MERCHANT_HOST:MERCHANT_PORT` (default
   127.0.0.1:8000) and the bot polls Telegram.

## Going live

Smile.one must be able to reach your merchant API over public HTTPS, so
on a real server / VPS put it behind HTTPS (reverse proxy) and give
Smile.one your public URLs, e.g. `https://your-domain/api/role-check`,
`/api/game-order`, `/api/payment-notification`, `/api/product-list`,
`/api/server-list`. A localhost URL will not work for Smile.one callbacks.

## Files

- `app.py` — runs merchant API + Telegram polling together
- `merchant_server.py` — Smile.one merchant endpoints + delivery stub
- `telegram_bot.py` — Telegram UI: pack → MLBB ID → Zone ID → confirm → pay
- `smile_sign.py` — double-MD5 signing / verification
- `store.py` — SQLite orders (PENDING → DELIVERED / REFUND / etc.)
- `products.json`, `roles.json` — editable packs and demo roles
- `test_endpoints.py` — local tests for all merchant endpoints
