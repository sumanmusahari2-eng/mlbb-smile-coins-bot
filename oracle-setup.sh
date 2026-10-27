#!/bin/bash
# Oracle Cloud Always Free VM setup for the Smile Coins bot (polling).
# Run on a fresh Ubuntu VM. The bot polls Telegram (outbound only), so
# NO inbound ports, nginx, domain or TLS setup is needed for the bot.
# The merchant API (/api/*) is NOT exposed by this setup — Smile.one
# callbacks need a public HTTPS URL, which is a separate follow-up step.
set -e
sudo apt-get update -qq
sudo apt-get install -y -qq git python3
cd ~
if [ ! -d mlbb-smile-coins-bot ]; then
  git clone https://github.com/sumanmusahari2-eng/mlbb-smile-coins-bot
fi
cd mlbb-smile-coins-bot
sudo cp mlbb-smile-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mlbb-smile-bot
echo "--- next steps ---"
echo "1. Create .env in this folder (TELEGRAM_BOT_TOKEN, SMILEONE_MERCHANT_KEY,"
echo "   ADMIN_CHAT_IDS, FUND_AUTO_CREDIT_TEST). Secrets are never committed."
echo "2. If migrating: copy orders.db + products.json from the old host here."
echo "3. Start: sudo systemctl start mlbb-smile-bot"
echo "4. Watch: sudo journalctl -u mlbb-smile-bot -f"
