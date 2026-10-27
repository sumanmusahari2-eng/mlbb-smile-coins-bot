"""Tiny SQLite store for orders. No external dependencies."""
import json
import sqlite3
import threading
import time
import uuid

_lock = threading.Lock()


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orders (
            game_order TEXT PRIMARY KEY,
            telegram_chat_id INTEGER,
            uid TEXT, sid TEXT, productid TEXT,
            amount TEXT, currency TEXT,
            status TEXT, trade_no TEXT,
            created_at INTEGER, updated_at INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            telegram_chat_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0,
            total_funded REAL DEFAULT 0,
            total_spent REAL DEFAULT 0
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fund_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_chat_id INTEGER,
            amount REAL,
            status TEXT,
            created_at INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS region_balances (
            telegram_chat_id INTEGER,
            region TEXT,
            balance REAL DEFAULT 0,
            total_funded REAL DEFAULT 0,
            total_spent REAL DEFAULT 0,
            PRIMARY KEY (telegram_chat_id, region)
        )"""
    )
    conn.commit()
    return conn


def _ensure_user(conn, chat_id):
    conn.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,?)",
                 (chat_id, 0, 0, 0))
    conn.commit()


def get_balance(conn, chat_id):
    _ensure_user(conn, chat_id)
    row = conn.execute(
        "SELECT balance, total_funded, total_spent FROM users WHERE telegram_chat_id=?",
        (chat_id,)).fetchone()
    return {"balance": row[0], "total_funded": row[1], "total_spent": row[2]}


def credit_balance(conn, chat_id, amount):
    _ensure_user(conn, chat_id)
    with _lock:
        conn.execute(
            "UPDATE users SET balance=balance+?, total_funded=total_funded+? "
            "WHERE telegram_chat_id=?", (amount, amount, chat_id))
        conn.commit()
    return get_balance(conn, chat_id)


def deduct_balance(conn, chat_id, amount):
    bal = get_balance(conn, chat_id)
    if bal["balance"] < amount - 1e-9:
        return None
    with _lock:
        conn.execute(
            "UPDATE users SET balance=balance-?, total_spent=total_spent+? "
            "WHERE telegram_chat_id=?", (amount, amount, chat_id))
        conn.commit()
    return get_balance(conn, chat_id)


def create_fund_request(conn, chat_id, amount):
    now = int(time.time())
    with _lock:
        cur = conn.execute(
            "INSERT INTO fund_requests (telegram_chat_id, amount, status, created_at) "
            "VALUES (?,?,?,?)", (chat_id, amount, "PENDING", now))
        conn.commit()
        return cur.lastrowid


def get_fund_request(conn, request_id):
    row = conn.execute(
        "SELECT * FROM fund_requests WHERE id=?", (request_id,)).fetchone()
    if not row:
        return None
    return dict(zip(["id", "telegram_chat_id", "amount", "status", "created_at"], row))


def list_pending_funds(conn, limit=20):
    cur = conn.execute(
        "SELECT * FROM fund_requests WHERE status='PENDING' "
        "ORDER BY created_at DESC LIMIT ?", (limit,))
    keys = ["id", "telegram_chat_id", "amount", "status", "created_at"]
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def approve_fund_request(conn, request_id):
    req = get_fund_request(conn, request_id)
    if not req or req["status"] != "PENDING":
        return None
    credit_balance(conn, req["telegram_chat_id"], req["amount"])
    with _lock:
        conn.execute("UPDATE fund_requests SET status='APPROVED' WHERE id=?",
                     (request_id,))
        conn.commit()
    return get_fund_request(conn, request_id)


def reject_fund_request(conn, request_id):
    req = get_fund_request(conn, request_id)
    if not req or req["status"] != "PENDING":
        return None
    with _lock:
        conn.execute("UPDATE fund_requests SET status='REJECTED' WHERE id=?",
                     (request_id,))
        conn.commit()
    return get_fund_request(conn, request_id)


def list_users(conn, limit=50):
    cur = conn.execute(
        "SELECT * FROM users ORDER BY total_spent DESC LIMIT ?", (limit,))
    keys = ["telegram_chat_id", "balance", "total_funded", "total_spent"]
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def list_all_orders(conn, limit=50):
    cur = conn.execute(
        "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,))
    keys = ["game_order", "telegram_chat_id", "uid", "sid", "productid",
            "amount", "currency", "status", "trade_no", "created_at", "updated_at"]
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def get_stats(conn):
    users = conn.execute("SELECT COUNT(*), COALESCE(SUM(balance),0), "
                         "COALESCE(SUM(total_funded),0), COALESCE(SUM(total_spent),0) "
                         "FROM users").fetchone()
    orders = conn.execute("SELECT COUNT(*), SUM(CASE WHEN status='DELIVERED' THEN 1 ELSE 0 END) "
                          "FROM orders").fetchone()
    pending = conn.execute("SELECT COUNT(*) FROM fund_requests WHERE status='PENDING'").fetchone()
    return {"users": users[0], "total_balance": users[1], "total_funded": users[2],
            "total_spent": users[3], "orders": orders[0], "delivered": orders[1] or 0,
            "pending_funds": pending[0]}


def adjust_balance(conn, chat_id, delta):
    """Add delta (can be negative) to a user's balance. Returns new balance or None."""
    if delta >= 0:
        return credit_balance(conn, chat_id, delta)
    return deduct_balance(conn, chat_id, -delta)


def create_order(conn, chat_id, uid, sid, productid, amount, currency):
    game_order = time.strftime("%Y%m%d%H%M%S") + str(uuid.uuid4().int)[:8]
    now = int(time.time())
    with _lock:
        conn.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (game_order, chat_id, uid, sid, productid, amount, currency,
             "PENDING", "", now, now),
        )
        conn.commit()
    return game_order


def get_order(conn, game_order):
    cur = conn.execute("SELECT * FROM orders WHERE game_order=?", (game_order,))
    row = cur.fetchone()
    if not row:
        return None
    keys = ["game_order", "telegram_chat_id", "uid", "sid", "productid",
            "amount", "currency", "status", "trade_no", "created_at", "updated_at"]
    return dict(zip(keys, row))


def update_order(conn, game_order, status, trade_no=""):
    with _lock:
        conn.execute(
            "UPDATE orders SET status=?, trade_no=?, updated_at=? WHERE game_order=?",
            (status, trade_no, int(time.time()), game_order),
        )
        conn.commit()


def list_orders(conn, chat_id, limit=10):
    cur = conn.execute(
        "SELECT * FROM orders WHERE telegram_chat_id=? ORDER BY created_at DESC LIMIT ?",
        (chat_id, limit),
    )
    keys = ["game_order", "telegram_chat_id", "uid", "sid", "productid",
            "amount", "currency", "status", "trade_no", "created_at", "updated_at"]
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# -- per-region wallets --------------------------------------------------
# One wallet per region (BR, PH, ...). The old single users.balance is
# carried into BR exactly once, on first access, so existing test
# credits are not lost when regions land.

def _ensure_region_row(conn, chat_id, region):
    row = conn.execute(
        "SELECT balance FROM region_balances "
        "WHERE telegram_chat_id=? AND region=?", (chat_id, region)).fetchone()
    if row:
        return
    seed_bal, seed_funded, seed_spent = 0, 0, 0
    if region == "BR":
        legacy = get_balance(conn, chat_id)
        seed_bal, seed_funded, seed_spent = (
            legacy["balance"], legacy["total_funded"], legacy["total_spent"])
    with _lock:
        conn.execute(
            "INSERT OR IGNORE INTO region_balances VALUES (?,?,?,?,?)",
            (chat_id, region, seed_bal, seed_funded, seed_spent))
        conn.commit()


def get_region_balance(conn, chat_id, region):
    _ensure_region_row(conn, chat_id, region)
    row = conn.execute(
        "SELECT balance, total_funded, total_spent FROM region_balances "
        "WHERE telegram_chat_id=? AND region=?", (chat_id, region)).fetchone()
    return {"balance": row[0], "total_funded": row[1], "total_spent": row[2]}


def credit_region_balance(conn, chat_id, region, amount):
    _ensure_region_row(conn, chat_id, region)
    with _lock:
        conn.execute(
            "UPDATE region_balances SET balance=balance+?, "
            "total_funded=total_funded+? "
            "WHERE telegram_chat_id=? AND region=?",
            (amount, amount, chat_id, region))
        conn.commit()
    return get_region_balance(conn, chat_id, region)


def deduct_region_balance(conn, chat_id, region, amount):
    bal = get_region_balance(conn, chat_id, region)
    if bal["balance"] < amount - 1e-9:
        return None
    with _lock:
        conn.execute(
            "UPDATE region_balances SET balance=balance-?, "
            "total_spent=total_spent+? "
            "WHERE telegram_chat_id=? AND region=?",
            (amount, amount, chat_id, region))
        conn.commit()
    return get_region_balance(conn, chat_id, region)


def list_region_balances(conn, chat_id):
    rows = conn.execute(
        "SELECT region, balance FROM region_balances "
        "WHERE telegram_chat_id=? ORDER BY region", (chat_id,)).fetchall()
    return [{"region": r[0], "balance": r[1]} for r in rows]


def get_region_totals(conn):
    rows = conn.execute(
        "SELECT region, SUM(balance) FROM region_balances "
        "GROUP BY region").fetchall()
    return {r[0]: r[1] for r in rows}
