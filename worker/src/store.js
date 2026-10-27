// D1 data layer — a faithful port of store.py's functions.
// `db` is the D1 binding: db.prepare(sql).bind(...).first()/.all()/.run().
// run() resolves to { meta: { changes } }; first() to a row or null.

export async function ensureUser(db, chatId) {
  await db.prepare(
    "INSERT OR IGNORE INTO users (telegram_chat_id, balance, total_funded, total_spent) VALUES (?, 0, 0, 0)"
  ).bind(chatId).run();
}

export async function getBalance(db, chatId) {
  await ensureUser(db, chatId);
  const row = await db.prepare(
    "SELECT balance, total_funded, total_spent FROM users WHERE telegram_chat_id = ?"
  ).bind(chatId).first();
  return { balance: row.balance, total_funded: row.total_funded, total_spent: row.total_spent };
}

// -- per-region wallets (same semantics as store.py) -------------------
// The old single users.balance carries into BR exactly once, on first
// access, so existing credits are not lost when regions land.
export async function ensureRegionRow(db, chatId, region) {
  const row = await db.prepare(
    "SELECT balance FROM region_balances WHERE telegram_chat_id = ? AND region = ?"
  ).bind(chatId, region).first();
  if (row) return;
  let seedBal = 0, seedFunded = 0, seedSpent = 0;
  if (region === "BR") {
    const legacy = await getBalance(db, chatId);
    seedBal = legacy.balance; seedFunded = legacy.total_funded; seedSpent = legacy.total_spent;
  }
  await db.prepare(
    "INSERT OR IGNORE INTO region_balances (telegram_chat_id, region, balance, total_funded, total_spent) VALUES (?, ?, ?, ?, ?)"
  ).bind(chatId, region, seedBal, seedFunded, seedSpent).run();
}

export async function getRegionBalance(db, chatId, region) {
  await ensureRegionRow(db, chatId, region);
  const row = await db.prepare(
    "SELECT balance, total_funded, total_spent FROM region_balances WHERE telegram_chat_id = ? AND region = ?"
  ).bind(chatId, region).first();
  return { balance: row.balance, total_funded: row.total_funded, total_spent: row.total_spent };
}

export async function creditRegionBalance(db, chatId, region, amount) {
  await ensureRegionRow(db, chatId, region);
  await db.prepare(
    "UPDATE region_balances SET balance = balance + ?, total_funded = total_funded + ? WHERE telegram_chat_id = ? AND region = ?"
  ).bind(amount, amount, chatId, region).run();
  return getRegionBalance(db, chatId, region);
}

// Atomic deduct: the balance>= guard makes the check-and-deduct one
// statement, so concurrent updates cannot overspend (no locks on D1).
// Returns the new balance, or null when funds are insufficient.
export async function deductRegionBalance(db, chatId, region, amount) {
  await ensureRegionRow(db, chatId, region);
  const res = await db.prepare(
    "UPDATE region_balances SET balance = balance - ?, total_spent = total_spent + ? WHERE telegram_chat_id = ? AND region = ? AND balance >= ?"
  ).bind(amount, amount, chatId, region, amount - 1e-9).run();
  if (!res.meta || res.meta.changes === 0) return null;
  return getRegionBalance(db, chatId, region);
}

export async function listRegionBalances(db, chatId) {
  const res = await db.prepare(
    "SELECT region, balance FROM region_balances WHERE telegram_chat_id = ? ORDER BY region"
  ).bind(chatId).all();
  return res.results || [];
}

export async function getRegionTotals(db) {
  const res = await db.prepare(
    "SELECT region, SUM(balance) AS total FROM region_balances GROUP BY region"
  ).all();
  const out = {};
  for (const r of (res.results || [])) out[r.region] = r.total;
  return out;
}

// -- orders -------------------------------------------------------------
function gameOrderId() {
  const d = new Date();
  const p = (n, w) => String(n).padStart(w, "0");
  const stamp = `${d.getUTCFullYear()}${p(d.getUTCMonth() + 1, 2)}${p(d.getUTCDate(), 2)}${p(d.getUTCHours(), 2)}${p(d.getUTCMinutes(), 2)}${p(d.getUTCSeconds(), 2)}`;
  return stamp + String(Math.floor(Math.random() * 100000000)).padStart(8, "0");
}

export async function createOrder(db, chatId, uid, sid, productid, amount, currency) {
  const gameOrder = gameOrderId();
  const now = Math.floor(Date.now() / 1000);
  await db.prepare(
    "INSERT INTO orders (game_order, telegram_chat_id, uid, sid, productid, amount, currency, status, trade_no, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', '', ?, ?)"
  ).bind(gameOrder, chatId, uid, sid, productid, amount, currency, now, now).run();
  return gameOrder;
}

export async function getOrder(db, gameOrder) {
  return db.prepare("SELECT * FROM orders WHERE game_order = ?").bind(gameOrder).first();
}

export async function updateOrder(db, gameOrder, status, tradeNo = "") {
  const now = Math.floor(Date.now() / 1000);
  await db.prepare(
    "UPDATE orders SET status = ?, trade_no = ?, updated_at = ? WHERE game_order = ?"
  ).bind(status, tradeNo, now, gameOrder).run();
}

export async function listOrders(db, chatId, limit = 10) {
  const res = await db.prepare(
    "SELECT * FROM orders WHERE telegram_chat_id = ? ORDER BY created_at DESC LIMIT ?"
  ).bind(chatId, limit).all();
  return res.results || [];
}

export async function getStats(db) {
  const users = await db.prepare(
    "SELECT COUNT(*) AS c, COALESCE(SUM(balance), 0) AS bal, COALESCE(SUM(total_funded), 0) AS funded, COALESCE(SUM(total_spent), 0) AS spent FROM users"
  ).first();
  const orders = await db.prepare(
    "SELECT COUNT(*) AS c, SUM(CASE WHEN status = 'DELIVERED' THEN 1 ELSE 0 END) AS d FROM orders"
  ).first();
  const pending = await db.prepare(
    "SELECT COUNT(*) AS c FROM fund_requests WHERE status = 'PENDING'"
  ).first();
  return {
    users: users.c, total_balance: users.bal, total_funded: users.funded,
    total_spent: users.spent, orders: orders.c, delivered: orders.d || 0,
    pending_funds: pending.c,
  };
}

// -- sessions (Workers have no in-memory state between requests) --------
export async function getSession(db, chatId) {
  const row = await db.prepare(
    "SELECT state FROM sessions WHERE telegram_chat_id = ?"
  ).bind(chatId).first();
  if (!row) return null;
  try { return JSON.parse(row.state); } catch { return null; }
}

export async function saveSession(db, chatId, state) {
  await db.prepare(
    "INSERT OR REPLACE INTO sessions (telegram_chat_id, state) VALUES (?, ?)"
  ).bind(chatId, JSON.stringify(state)).run();
}

export async function deleteSession(db, chatId) {
  await db.prepare("DELETE FROM sessions WHERE telegram_chat_id = ?").bind(chatId).run();
}

// -- products / roles / servers ------------------------------------------
export async function listProducts(db, region) {
  const res = await db.prepare(
    "SELECT * FROM products WHERE region = ? ORDER BY CAST(pid AS INTEGER)"
  ).bind(region).all();
  return res.results || [];
}

export async function findProduct(db, pid, region) {
  return db.prepare(
    "SELECT * FROM products WHERE pid = ? AND region = ?"
  ).bind(String(pid), region).first();
}

export async function findPackByDiamonds(db, diamonds, region) {
  const products = await listProducts(db, region);
  return products.find((p) => {
    try { return parseInt(p.diamonds, 10) === parseInt(diamonds, 10); } catch { return false; }
  }) || null;
}

export async function updateProductPrice(db, region, pid, price) {
  const res = await db.prepare(
    "UPDATE products SET price = ? WHERE pid = ? AND region = ?"
  ).bind(price, String(pid), region).run();
  return (res.meta && res.meta.changes > 0);
}

export async function getRole(db, uid) {
  return db.prepare("SELECT * FROM roles WHERE uid = ?").bind(String(uid)).first();
}

export async function listServers(db) {
  const res = await db.prepare("SELECT * FROM servers").all();
  return res.results || [];
}
