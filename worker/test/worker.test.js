// Worker tests: run with `node --test test/worker.test.js` from worker/.
// The real store.js / bot.js / merchant.js / sign.js code runs against a
// fake D1 (in-memory tables, same prepare/bind/first/all/run shape) and
// a recording sender — no Cloudflare account or network needed.
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import { md5Hex } from "../src/md5.js";
import { makeSign, verifySign } from "../src/sign.js";
import { Bot } from "../src/bot.js";
import { Merchant } from "../src/merchant.js";
import worker from "../src/index.js";

// -- fake D1 -------------------------------------------------------------
function norm(sql) {
  return sql.replace(/\s+/g, " ").trim().toLowerCase();
}

class FakeD1 {
  constructor() {
    this.users = new Map();
    this.regionBalances = new Map();
    this.orders = new Map();
    this.orderSeq = 0;
    this.sessions = new Map();
    this.products = [];
    this.roles = new Map();
    this.servers = [];
  }

  seedProducts(products) {
    this.products = products.map((p) => ({ ...p }));
  }

  prepare(sql) {
    const q = norm(sql);
    const self = this;
    const stmt = {
      bind(...args) {
        return {
          async first() {
            if (q.startsWith("select balance, total_funded, total_spent from users")) {
              return self.users.get(args[0]) || null;
            }
            if (q.startsWith("select balance from region_balances")) {
              return self.regionBalances.get(`${args[0]}|${args[1]}`) || null;
            }
            if (q.startsWith("select balance, total_funded, total_spent from region_balances")) {
              return self.regionBalances.get(`${args[0]}|${args[1]}`) || null;
            }
            if (q.startsWith("select * from orders where game_order")) {
              return self.orders.get(args[0]) || null;
            }
            if (q.startsWith("select * from products where pid")) {
              return self.products.find((p) => p.pid === String(args[0]) && p.region === args[1]) || null;
            }
            if (q.startsWith("select * from roles where uid")) {
              return self.roles.get(String(args[0])) || null;
            }
            if (q.startsWith("select state from sessions")) {
              const s = self.sessions.get(args[0]);
              return s === undefined ? null : { state: s };
            }
            if (q.startsWith("select count(*) as c, coalesce(sum(balance)")) {
              const rows = [...self.users.values()];
              return {
                c: rows.length,
                bal: rows.reduce((s, r) => s + r.balance, 0),
                funded: rows.reduce((s, r) => s + r.total_funded, 0),
                spent: rows.reduce((s, r) => s + r.total_spent, 0),
              };
            }
            if (q.startsWith("select count(*) as c, sum(case when status")) {
              const rows = [...self.orders.values()];
              return { c: rows.length, d: rows.filter((r) => r.status === "DELIVERED").length };
            }
            if (q.startsWith("select count(*) as c from fund_requests")) {
              return { c: 0 };
            }
            return null;
          },
          async all() {
            if (q.startsWith("select region, balance from region_balances")) {
              return { results: [...self.regionBalances.values()]
                .filter((r) => r.telegram_chat_id === args[0])
                .sort((a, b) => a.region.localeCompare(b.region))
                .map((r) => ({ region: r.region, balance: r.balance })) };
            }
            if (q.startsWith("select region, sum(balance)")) {
              const totals = {};
              for (const r of self.regionBalances.values()) {
                totals[r.region] = (totals[r.region] || 0) + r.balance;
              }
              return { results: Object.entries(totals).map(([region, total]) => ({ region, total })) };
            }
            if (q.startsWith("select * from orders where telegram_chat_id")) {
              const rows = [...self.orders.values()]
                .filter((r) => r.telegram_chat_id === args[0])
                .sort((a, b) => (b.created_at - a.created_at) || (b._seq - a._seq));
              return { results: rows.slice(0, args[1]) };
            }
            if (q.startsWith("select * from products where region")) {
              return { results: self.products
                .filter((p) => p.region === args[0])
                .sort((a, b) => parseInt(a.pid, 10) - parseInt(b.pid, 10)) };
            }
            if (q.startsWith("select * from servers")) {
              return { results: self.servers };
            }
            return { results: [] };
          },
          async run() {
            if (q.startsWith("insert or ignore into users")) {
              if (!self.users.has(args[0])) {
                self.users.set(args[0], { telegram_chat_id: args[0], balance: 0, total_funded: 0, total_spent: 0 });
              }
              return { meta: { changes: 1 } };
            }
            if (q.startsWith("insert or ignore into region_balances")) {
              const key = `${args[0]}|${args[1]}`;
              if (!self.regionBalances.has(key)) {
                self.regionBalances.set(key, { telegram_chat_id: args[0], region: args[1], balance: args[2], total_funded: args[3], total_spent: args[4] });
              }
              return { meta: { changes: 1 } };
            }
            if (q.startsWith("update region_balances set balance = balance +")) {
              const r = self.regionBalances.get(`${args[2]}|${args[3]}`);
              r.balance += args[0]; r.total_funded += args[1];
              return { meta: { changes: 1 } };
            }
            if (q.startsWith("update region_balances set balance = balance -")) {
              const r = self.regionBalances.get(`${args[2]}|${args[3]}`);
              // Mirror the SQL guard: apply only if balance >= amount - 1e-9
              // (args[4] already carries amount - 1e-9 from store.js).
              if (r.balance < args[4]) return { meta: { changes: 0 } };
              r.balance -= args[0]; r.total_spent += args[1];
              return { meta: { changes: 1 } };
            }
            if (q.startsWith("insert into orders")) {
              self.orderSeq += 1;
              self.orders.set(args[0], {
                game_order: args[0], telegram_chat_id: args[1], uid: args[2], sid: args[3],
                productid: args[4], amount: args[5], currency: args[6], status: "PENDING",
                trade_no: "", created_at: args[7], updated_at: args[8], _seq: self.orderSeq,
              });
              return { meta: { changes: 1 } };
            }
            if (q.startsWith("update orders set status")) {
              const o = self.orders.get(args[3]);
              if (!o) return { meta: { changes: 0 } };
              o.status = args[0]; o.trade_no = args[1]; o.updated_at = args[2];
              return { meta: { changes: 1 } };
            }
            if (q.startsWith("insert or replace into sessions")) {
              self.sessions.set(args[0], args[1]);
              return { meta: { changes: 1 } };
            }
            if (q.startsWith("delete from sessions")) {
              self.sessions.delete(args[0]);
              return { meta: { changes: 1 } };
            }
            if (q.startsWith("update products set price")) {
              const p = self.products.find((x) => x.pid === String(args[1]) && x.region === args[2]);
              if (!p) return { meta: { changes: 0 } };
              p.price = args[0];
              return { meta: { changes: 1 } };
            }
            throw new Error(`FakeD1: unhandled SQL: ${sql}`);
          },
        };
      },
      // Real D1 statements also expose first/all/run without bind().
      async first() { return stmt.bind().first(); },
      async all() { return stmt.bind().all(); },
      async run() { return stmt.bind().run(); },
    };
    return stmt;
  }
}

const BR_PRODUCTS = [
  { pid: "1", region: "BR", name: "MLBB 50 + 5 Diamonds", diamonds: 55, price: "4.00", original_price: "4.00", icon: "", currency: "BRL" },
  { pid: "5", region: "BR", name: "MLBB 78 + 8 Diamonds", diamonds: 86, price: "6.25", original_price: "6.25", icon: "", currency: "BRL" },
  { pid: "8", region: "BR", name: "MLBB 625 + 81 Diamonds", diamonds: 706, price: "50.00", original_price: "50.00", icon: "", currency: "BRL" },
];

function makeEnv(db, overrides = {}) {
  return {
    TELEGRAM_BOT_TOKEN: "TESTTOKEN",
    SMILEONE_MERCHANT_KEY: "testkey",
    ADMIN_CHAT_IDS: "111",
    FUND_AUTO_CREDIT_TEST: "true",
    WEBHOOK_SECRET: "",
    SETUP_SECRET: "setup123",
    DB: db,
    ...overrides,
  };
}

function makeBot(db, envOverrides) {
  const messages = [];
  const bot = new Bot(makeEnv(db, envOverrides), db, {
    send: async (chatId, text, kb) => { messages.push({ chatId, text, kb }); },
    answerCallbackQuery: async () => {},
  });
  return { bot, messages };
}

function freshDb() {
  const db = new FakeD1();
  db.seedProducts(BR_PRODUCTS);
  db.roles.set("123456789", { uid: "123456789", nickname: "DemoPlayer", sid: "1234" });
  db.servers = [{ server_id: "zone", server_name: "MLBB - Zone ID is the server/zone field" }];
  return db;
}

// -- md5 + signing ---------------------------------------------------------
test("md5 matches node crypto (incl. unicode)", () => {
  for (const s of ["", "abc", "hello world", "héllo", "x".repeat(1000)]) {
    assert.equal(md5Hex(s), createHash("md5").update(s, "utf8").digest("hex"));
  }
  assert.equal(md5Hex(""), "d41d8cd98f00b204e9800998ecf8427e");
});

test("smile sign matches the Python implementation", () => {
  assert.equal(
    makeSign({ uid: "123456789", productid: "5", amount: "6.25" }, "testkey"),
    "cd720ad0b99c6fccc6d1d542cff3b4e6");
  assert.equal(
    makeSign({ uid: "123456789", sid: "1234", time: "1700000000" }, "testkey"),
    "9db657f45c962208ffe52abce1201a99");
  assert.equal(
    verifySign({ uid: "123456789", productid: "5", amount: "6.25", sign: "cd720ad0b99c6fccc6d1d542cff3b4e6" }, "testkey"),
    true);
  assert.equal(
    verifySign({ uid: "123456789", productid: "5", amount: "6.25", sign: "deadbeef" }, "testkey"),
    false);
});

// -- bot flows ---------------------------------------------------------------
test("/start shows balances and menu", async () => {
  const db = freshDb();
  const { bot, messages } = makeBot(db);
  await bot.handleMessage(999, "/start");
  assert.match(messages[0].text, /Welcome/);
  assert.match(messages[0].text, /Balance 🇧🇷 \[BR\]: 0\.00 BRL/);
  assert.match(messages[0].text, /Balance 🇵🇭 \[PH\]: 0\.00 PHP/);
  assert.equal(messages[0].kb.inline_keyboard[0][0].text, "🎮 Top Up MLBB");
});

test("quick order /br: fund, confirm, pay, PAID, balance deducted", async () => {
  const db = freshDb();
  const { bot, messages } = makeBot(db);
  await bot.handleMessage(999, "/addfund 100");
  assert.match(messages.at(-1).text, /100\.00 BRL added/);
  await bot.handleMessage(999, "/br 986305 2375 86");
  assert.match(messages.at(-1).text, /Total: 6\.25 BRL/);
  assert.match(messages.at(-1).text, /MLBB 78 \+ 8 Diamonds/);
  await bot.handleCallback(999, "batch:yes");
  assert.match(messages.at(-1).text, /Payment received/);
  assert.match(messages.at(-1).text, /Remaining balance: 93\.75 BRL/);
  const orders = [...db.orders.values()];
  assert.equal(orders.length, 1);
  assert.equal(orders[0].status, "PAID");
  assert.equal(orders[0].productid, "5");
  await bot.handleMessage(999, "/orders");
  assert.match(messages.at(-1).text, /\[BR\] - PAID/);
});

test("bulk validation: too many, bad numbers, unknown pack", async () => {
  const db = freshDb();
  const { bot, messages } = makeBot(db);
  const eleven = Array(11).fill("986305 2375 86").join(" ");
  await bot.handleMessage(999, `/br ${eleven}`);
  assert.match(messages.at(-1).text, /Bulk max is 10/);
  await bot.handleMessage(999, "/br abc 2375 86");
  assert.match(messages.at(-1).text, /must all be numbers/);
  await bot.handleMessage(999, "/br 986305 2375 99999");
  assert.match(messages.at(-1).text, /No pack for 99999/);
  assert.equal(db.orders.size, 0);
});

test("insufficient balance creates no order", async () => {
  const db = freshDb();
  const { bot, messages } = makeBot(db);
  await bot.handleMessage(999, "/br 986305 2375 86");
  await bot.handleCallback(999, "batch:yes");
  assert.match(messages.at(-1).text, /Not enough balance/);
  assert.equal(db.orders.size, 0);
});

test("menu flow: region -> pack -> uid -> sid -> confirm -> pay", async () => {
  const db = freshDb();
  const { bot, messages } = makeBot(db);
  await bot.handleMessage(999, "/addfund br 50");
  await bot.handleCallback(999, "menu:topup");
  await bot.handleCallback(999, "region:BR");
  assert.match(messages.at(-1).text, /Pick a pack/);
  await bot.handleCallback(999, "pack:BR:5");
  assert.match(messages.at(-1).text, /Send your MLBB ID/);
  await bot.handleMessage(999, "notanumber");
  assert.match(messages.at(-1).text, /numbers only/);
  await bot.handleMessage(999, "986305");
  await bot.handleMessage(999, "2375");
  assert.match(messages.at(-1).text, /Please check carefully/);
  assert.match(messages.at(-1).text, /MLBB ID: 986305/);
  await bot.handleCallback(999, "confirm:yes");
  assert.match(messages.at(-1).text, /Payment received/);
  assert.equal([...db.orders.values()][0].status, "PAID");
});

test("PH is gated, never priced", async () => {
  const db = freshDb();
  const { bot, messages } = makeBot(db);
  await bot.handleMessage(999, "/ph 986305 2375 11");
  assert.match(messages.at(-1).text, /aren't available yet/);
  await bot.handleCallback(999, "region:PH");
  assert.match(messages.at(-1).text, /aren't available yet/);
});

test("admin gating + price setting persists in D1", async () => {
  const db = freshDb();
  const { bot, messages } = makeBot(db);
  await bot.handleMessage(222, "/admin");
  assert.match(messages.at(-1).text, /Admin access only/);
  await bot.handleMessage(111, "/admin");
  assert.match(messages.at(-1).text, /Admin Panel/);
  await bot.handleCallback(111, "admin:packs");
  await bot.handleCallback(111, "admin:packlist:BR");
  await bot.handleCallback(111, "admin:pack:BR:5");
  await bot.handleCallback(111, "admin:setprice:BR:5");
  assert.match(messages.at(-1).text, /Send the new price/);
  await bot.handleMessage(111, "7.00");
  assert.match(messages.at(-1).text, /price updated: 6\.25 → 7\.00/);
  assert.equal(db.products.find((p) => p.pid === "5").price, "7.00");
  await bot.handleMessage(111, "/packs");
  // /packs sends the BR list, then the "Want to buy one?" prompt.
  assert.match(messages.at(-2).text, /MLBB 78 \+ 8 Diamonds - 7\.00 BRL/);
});

// -- merchant API --------------------------------------------------------------
test("merchant: sign errors, demo roles, order lifecycle", async () => {
  const db = freshDb();
  const env = makeEnv(db);
  const merchant = new Merchant(env, db);
  const bad = { uid: "123456789", sign: "nope" };
  assert.equal((await merchant.roleCheck(bad)).status, 205);
  const roleOk = { uid: "123456789", sid: "1234", sign: makeSign({ uid: "123456789", sid: "1234" }, "testkey") };
  assert.deepEqual(await merchant.roleCheck(roleOk), { status: 200, nickname: "DemoPlayer", error: "" });
  const roleMissing = { uid: "000", sign: makeSign({ uid: "000" }, "testkey") };
  assert.equal((await merchant.roleCheck(roleMissing)).status, 202);
  const badProduct = { uid: "123456789", sid: "1234", productid: "999", sign: makeSign({ uid: "123456789", sid: "1234", productid: "999" }, "testkey") };
  assert.equal((await merchant.gameOrder(badProduct)).status, 206);
  const orderOk = { uid: "123456789", sid: "1234", productid: "5", sign: makeSign({ uid: "123456789", sid: "1234", productid: "5" }, "testkey") };
  const created = await merchant.gameOrder(orderOk);
  assert.equal(created.status, 200);
  assert.ok(created.game_order);
  // SUCCESS with unwired delivery answers 201 and stays NOT delivered.
  const pay = { game_order: created.game_order, trade_no: "T1", trade_status: "SUCCESS", sign: makeSign({ game_order: created.game_order, trade_no: "T1", trade_status: "SUCCESS" }, "testkey") };
  const paid = await merchant.paymentNotification(pay);
  assert.equal(paid.status, 201);
  assert.equal(db.orders.get(created.game_order).status, "PENDING");
  const refund = { game_order: created.game_order, trade_no: "T1", trade_status: "REFUND", sign: makeSign({ game_order: created.game_order, trade_no: "T1", trade_status: "REFUND" }, "testkey") };
  const refunded = await merchant.paymentNotification(refund);
  assert.equal(refunded.status, 200);
  assert.equal(refunded.game_status, "REFUND");
  const servers = await merchant.serverList({ sign: makeSign({}, "testkey") });
  assert.equal(servers.status, 200);
  assert.equal(servers.serverList[0].serverId, "zone");
  const plist = await merchant.productList({ sign: makeSign({}, "testkey") });
  assert.equal(plist.status, 200);
  assert.deepEqual(plist.product_list.map((p) => p.pid), ["1", "5", "8"]);
});

// -- worker routing --------------------------------------------------------------
test("index: webhook verifies secret, merchant routes answer", async () => {
  const db = freshDb();
  const calls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return { json: async () => ({ ok: true, result: true }) };
  };
  try {
    const env = makeEnv(db, { WEBHOOK_SECRET: "s3cr3t" });
    // Wrong secret -> 403, no bot work.
    let res = await worker.fetch(new Request("https://bot.example/telegram-webhook", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: { chat: { id: 999 }, text: "/start" } }),
    }), env);
    assert.equal(res.status, 403);
    assert.equal(calls.length, 0);
    // Right secret -> handled, reply sent via Telegram API.
    res = await worker.fetch(new Request("https://bot.example/telegram-webhook", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "s3cr3t" },
      body: JSON.stringify({ message: { chat: { id: 999 }, text: "/start" } }),
    }), env);
    assert.equal(res.status, 200);
    assert.ok(calls.some((u) => u.includes("/sendMessage")));
    // Merchant route through the worker.
    res = await worker.fetch(new Request("https://bot.example/api/product-list", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sign: makeSign({}, "testkey") }),
    }), env);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.status, 200);
    assert.equal(body.product_list.length, 3);
  } finally {
    globalThis.fetch = origFetch;
  }
});
