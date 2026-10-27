-- D1 schema for the Smile Coins bot (Worker port of store.py).
-- Apply with: wrangler d1 migrations apply mlbb-smile-bot-db
-- (or paste into the dashboard's D1 console for the first deploy).

CREATE TABLE IF NOT EXISTS users (
  telegram_chat_id INTEGER PRIMARY KEY,
  balance REAL DEFAULT 0,
  total_funded REAL DEFAULT 0,
  total_spent REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS region_balances (
  telegram_chat_id INTEGER,
  region TEXT,
  balance REAL DEFAULT 0,
  total_funded REAL DEFAULT 0,
  total_spent REAL DEFAULT 0,
  PRIMARY KEY (telegram_chat_id, region)
);

CREATE TABLE IF NOT EXISTS fund_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_chat_id INTEGER,
  amount REAL,
  status TEXT,
  created_at INTEGER
);

CREATE TABLE IF NOT EXISTS orders (
  game_order TEXT PRIMARY KEY,
  telegram_chat_id INTEGER,
  uid TEXT, sid TEXT, productid TEXT,
  amount TEXT, currency TEXT,
  status TEXT, trade_no TEXT,
  created_at INTEGER, updated_at INTEGER
);

-- Bot conversation state (replaces the Python in-memory sessions dict:
-- Workers forget everything between requests, so sessions live here).
CREATE TABLE IF NOT EXISTS sessions (
  telegram_chat_id INTEGER PRIMARY KEY,
  state TEXT
);

-- Packs per region. Admin price setting updates this table (it used to
-- rewrite products.json on disk, which Workers do not have).
CREATE TABLE IF NOT EXISTS products (
  pid TEXT,
  region TEXT,
  name TEXT,
  diamonds INTEGER,
  price TEXT,
  original_price TEXT,
  icon TEXT,
  currency TEXT,
  PRIMARY KEY (pid, region)
);

CREATE TABLE IF NOT EXISTS servers (
  server_id TEXT PRIMARY KEY,
  server_name TEXT
);

-- DEMO ONLY, same as roles.json: replace verify_role with a real
-- game-account lookup before production use.
CREATE TABLE IF NOT EXISTS roles (
  uid TEXT PRIMARY KEY,
  nickname TEXT,
  sid TEXT
);

-- Seed: BR packs, copied exactly from products.json (2026-10-26).
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('1', 'BR', 'MLBB 50 + 5 Diamonds', 55, '4.00', '4.00', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('2', 'BR', 'MLBB 150 + 15 Diamonds', 165, '11.99', '11.99', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('3', 'BR', 'MLBB 250 + 25 Diamonds', 275, '19.75', '19.75', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('4', 'BR', 'MLBB 500 + 65 Diamonds', 565, '40.50', '40.50', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('5', 'BR', 'MLBB 78 + 8 Diamonds', 86, '6.25', '6.25', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('6', 'BR', 'MLBB 156 + 16 Diamonds', 172, '12.50', '12.50', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('7', 'BR', 'MLBB 234 + 23 Diamonds', 257, '18.67', '18.67', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('8', 'BR', 'MLBB 625 + 81 Diamonds', 706, '50.00', '50.00', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('9', 'BR', 'MLBB 1860 + 335 Diamonds', 2195, '150.00', '150.00', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('10', 'BR', 'MLBB 3099 + 589 Diamonds', 3688, '250.00', '250.00', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('11', 'BR', 'MLBB 4649 + 883 Diamonds', 5532, '375.00', '375.00', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('12', 'BR', 'MLBB 7740 + 1548 Diamonds', 9288, '625.00', '625.00', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('13', 'BR', 'MLBB Twilight Pass', 0, '41.25', '41.25', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('14', 'BR', 'MLBB Weekly Diamond Pass (~220 total, 7 days)', 220, '8.00', '8.00', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('15', 'BR', 'MLBB Weekly Elite Pack - 55 Diamonds + bonuses (once a week)', 55, '4.00', '4.00', '', 'BRL');
INSERT INTO products (pid, region, name, diamonds, price, original_price, icon, currency) VALUES ('16', 'BR', 'MLBB Monthly Epic Pack - 275 Diamonds + bonuses (once a month)', 275, '19.75', '19.75', '', 'BRL');
INSERT INTO servers (server_id, server_name) VALUES ('zone', 'MLBB - Zone ID is the server/zone field');
INSERT INTO roles (uid, nickname, sid) VALUES ('123456789', 'DemoPlayer', '1234');
INSERT INTO roles (uid, nickname, sid) VALUES ('987654321', 'TestHero', '4321');
