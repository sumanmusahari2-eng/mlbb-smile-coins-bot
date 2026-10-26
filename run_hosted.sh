#!/bin/bash
# Hosted test run for the MLBB Smile Coins bot.
# Usage: TELEGRAM_BOT_TOKEN is read from .env in this folder (chmod 600 .env).
# This script restarts the bot if it crashes, but it does NOT survive a
# full VM replacement on its own - see HOSTING.md for the systemd step.
cd "$(dirname "$0")" || exit 1
while true; do
  echo "[hosted] starting bot at $(date)"
  python3 app.py
  echo "[hosted] bot stopped (exit $?), restarting in 5s..."
  sleep 5
done
