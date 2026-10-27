#!/usr/bin/env python3
"""Convert a wallets.json export (users + region_balances from the old
orders.db) into D1 INSERT statements for the Worker cutover.

Usage: python3 migrate-balances.py wallets.json > wallets.sql
Then:  wrangler d1 execute mlbb-smile-bot-db --file=wallets.sql

INSERT OR REPLACE: re-running the migration is safe, and migrated
region rows stop the Worker's legacy-BR seeding from double-crediting.
"""
import json
import sys


def num(v):
    return float(v or 0)


def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        doc = json.load(f)
    for u in doc.get("users", []):
        print(
            "INSERT OR REPLACE INTO users "
            "(telegram_chat_id, balance, total_funded, total_spent) VALUES "
            f"({int(u['telegram_chat_id'])}, {num(u['balance'])}, "
            f"{num(u['total_funded'])}, {num(u['total_spent'])});"
        )
    for r in doc.get("region_balances", []):
        print(
            "INSERT OR REPLACE INTO region_balances "
            "(telegram_chat_id, region, balance, total_funded, total_spent) VALUES "
            f"({int(r['telegram_chat_id'])}, '{r['region']}', {num(r['balance'])}, "
            f"{num(r['total_funded'])}, {num(r['total_spent'])});"
        )


if __name__ == "__main__":
    main()
