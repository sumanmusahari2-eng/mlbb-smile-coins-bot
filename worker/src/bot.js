// Telegram ordering bot — Cloudflare Workers port of order_bot.py.
// Same commands, messages, order flow, bulk rules, admin gating and
// honest limits as the Python bot. Differences forced by Workers:
//   * sessions live in D1 (Workers forget memory between requests),
//   * products live in D1 (no products.json file to rewrite),
//   * wallet deduct is one atomic UPDATE (no locks on D1),
//   * delivery stays unwired: paid orders stay PAID, never fake DELIVERED,
//   * PH stays gated: no verified PH pack list, so /ph answers
//     "not available yet" and no PH prices are ever invented.
import * as store from "./store.js";
import { sendMessage, answerCallbackQuery, setCommands } from "./telegram.js";

export const REGIONS = {
  BR: { name: "Brazil", flag: "🇧🇷", currency: "BRL" },
  PH: { name: "Philippines", flag: "🇵🇭", currency: "PHP" },
};
export const CURRENCY_REGION = { BRL: "BR", PHP: "PH" };
const PER_PAGE = 8;
const BULK_MAX = 10;

function kb(rows) {
  return { inline_keyboard: rows };
}

export class Bot {
  constructor(env, db, hooks = {}) {
    this.env = env;
    this.db = db;
    this.adminIds = new Set(
      String(env.ADMIN_CHAT_IDS || "").split(",")
        .map((s) => s.trim()).filter((s) => /^\d+$/.test(s)).map(Number));
    this.autoCreditTest = String(env.FUND_AUTO_CREDIT_TEST ?? "true") === "true";
    this.sendFn = hooks.send || ((chatId, text, markup) => sendMessage(env, chatId, text, markup));
    this.answerCbFn = hooks.answerCallbackQuery || ((id) => answerCallbackQuery(env, id));
    this._sess = undefined;
    this._sessChat = null;
  }

  isAdmin(chatId) {
    return this.adminIds.has(chatId);
  }

  async send(chatId, text, replyMarkup) {
    return this.sendFn(chatId, text, replyMarkup);
  }

  // -- sessions (per-request cache over the D1 sessions table) ---------
  async loadSession(chatId, orEmpty) {
    this._sessChat = chatId;
    const saved = await store.getSession(this.db, chatId);
    this._sess = saved !== null ? saved : (orEmpty ? {} : null);
  }

  setSession(obj) {
    this._sess = obj;
  }

  dropSession() {
    this._sess = null;
  }

  async persistSession() {
    if (this._sessChat === null || this._sess === undefined) return;
    if (this._sess === null) await store.deleteSession(this.db, this._sessChat);
    else await store.saveSession(this.db, this._sessChat, this._sess);
  }

  // -- products ---------------------------------------------------------
  regionProducts(region) {
    return store.listProducts(this.db, region);
  }

  findProduct(pid, region) {
    return store.findProduct(this.db, pid, region);
  }

  findPackByDiamonds(diamonds, region) {
    return store.findPackByDiamonds(this.db, diamonds, region);
  }

  async packsText(region) {
    const info = REGIONS[region];
    const lines = [`💎 <b>MLBB Packs — ${info.flag} ${info.name}</b>:`];
    for (const p of await this.regionProducts(region)) {
      lines.push(`${p.name} - ${p.price} ${info.currency}`);
    }
    return lines.join("\n");
  }

  // -- balances ----------------------------------------------------------
  async balanceLines(chatId) {
    const lines = [];
    for (const code of ["BR", "PH"]) {
      const info = REGIONS[code];
      const bal = await store.getRegionBalance(this.db, chatId, code);
      lines.push(`Balance ${info.flag} [${code}]: ${bal.balance.toFixed(2)} ${info.currency}`);
    }
    return lines;
  }

  async setCommands() {
    return setCommands(this.env, this.adminIds);
  }

  // -- menus ---------------------------------------------------------------
  mainMenu() {
    return kb([
      [{ text: "🎮 Top Up MLBB", callback_data: "menu:topup" },
       { text: "💎 Packs", callback_data: "menu:packs" }],
      [{ text: "💰 Balance", callback_data: "menu:balance" },
       { text: "📦 My Orders", callback_data: "menu:orders" }],
      [{ text: "🆔 My ID", callback_data: "menu:getid" },
       { text: "❓ Help", callback_data: "menu:help" }],
    ]);
  }

  regionMenu() {
    return kb([
      [{ text: "🇧🇷 Brazil (BR)", callback_data: "region:BR" },
       { text: "🇵🇭 Philippines (PH)", callback_data: "region:PH" }],
      [{ text: "⬅️ Back to menu", callback_data: "menu:main" }],
    ]);
  }

  async start(chatId) {
    this.setSession({ step: "menu" });
    const lines = ["👋 <b>Welcome!</b>\n", "<b>Your Current Balance</b>"];
    lines.push(...await this.balanceLines(chatId));
    lines.push(
      "\n💎 <b>Diamond Topup Example</b>\n"
      + "For BR\n/br &lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt;\n"
      + "For PH\n/ph &lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt;\n"
      + "For Bulk (10 max)\n"
      + "/br &lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt; "
      + "&lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt; ...\n\n"
      + "Or tap Top Up below and pick a pack.");
    return this.send(chatId, lines.join("\n"), this.mainMenu());
  }

  async showPacks(chatId, region, page = 0) {
    const products = await this.regionProducts(region);
    const totalPages = Math.max(1, Math.ceil(products.length / PER_PAGE));
    page = Math.max(0, Math.min(page, totalPages - 1));
    this.setSession({ step: "pack", region, pack_page: page });
    const items = products.slice(page * PER_PAGE, (page + 1) * PER_PAGE);
    const rows = [];
    for (let i = 0; i < items.length; i += 2) {
      rows.push(items.slice(i, i + 2).map((p) => ({
        text: `${p.name} - ${p.price}`, callback_data: `pack:${region}:${p.pid}`,
      })));
    }
    const nav = [];
    if (page > 0) nav.push({ text: "◀️ Prev", callback_data: `packpage:${region}:${page - 1}` });
    if (page < totalPages - 1) nav.push({ text: "Next ▶️", callback_data: `packpage:${region}:${page + 1}` });
    if (nav.length) rows.push(nav);
    rows.push([{ text: "⬅️ Regions", callback_data: "menu:topup" },
               { text: "⬅️ Menu", callback_data: "menu:main" }]);
    return this.send(chatId,
      `Pick a pack (${REGIONS[region].flag} ${REGIONS[region].name}, page ${page + 1}/${totalPages}):`,
      kb(rows));
  }

  // -- order flow -----------------------------------------------------------
  async sendConfirm(chatId, sess) {
    const product = await this.findProduct(sess.pack, sess.region);
    sess.step = "confirm";
    const markup = kb([
      [{ text: "✅ Confirm & pay", callback_data: "confirm:yes" },
       { text: "❌ Cancel", callback_data: "confirm:no" }]]);
    const info = REGIONS[sess.region];
    return this.send(
      chatId,
      "Please check carefully, then confirm:\n"
      + `<b>${product.name}</b>\n`
      + `Region: ${info.flag} ${info.name}\n`
      + `MLBB ID: ${sess.uid}\nZone ID: ${sess.sid}\n`
      + `Price: ${product.price} ${info.currency}\n\n`
      + "Make sure the ID and Zone are correct — diamonds go to "
      + "exactly this account.",
      markup);
  }

  // Delivery is unwired by design: never pretend an order is delivered.
  async attemptDelivery(order) {
    console.log(`[DELIVERY-NOT-CONFIGURED] order=${order.game_order} product=${order.productid} uid=${order.uid} sid=${order.sid} — merchant must deliver manually`);
    return false;
  }

  async payBatch(chatId, sess) {
    const region = sess.region;
    const info = REGIONS[region];
    const currency = info.currency;
    const items = sess.batch;
    const total = items.reduce((s, it) => s + it.price, 0);
    const bal = await store.getRegionBalance(this.db, chatId, region);
    if (bal.balance < total - 1e-9) {
      const shortfall = total - bal.balance;
      sess.step = "menu";
      return this.send(
        chatId,
        "⚠️ Not enough balance.\n"
        + `Total: ${total.toFixed(2)} ${currency}\n`
        + `Your balance: ${bal.balance.toFixed(2)} ${currency}\n`
        + `You need ${shortfall.toFixed(2)} more.\n\n`
        + `Add funds with: /addfund ${region.toLowerCase()} ${shortfall.toFixed(2)}\n`
        + "Then place the order again.\n"
        + "(No order was created.)");
    }
    await store.deductRegionBalance(this.db, chatId, region, total);
    const orderIds = [];
    let delivered = 0;
    for (const it of items) {
      const orderId = await store.createOrder(this.db, chatId, it.uid, it.sid,
        it.product.pid, it.product.price, currency);
      await store.updateOrder(this.db, orderId, "PAID", "WALLET");
      const order = await store.getOrder(this.db, orderId);
      if (await this.attemptDelivery(order)) delivered += 1;
      orderIds.push(orderId);
    }
    const newBal = await store.getRegionBalance(this.db, chatId, region);
    sess.step = "done";
    const lines = ["✅ <b>Payment received.</b>",
      `Orders: ${orderIds.join(", ")}`,
      `Paid: ${total.toFixed(2)} ${currency}`,
      `Remaining balance: ${newBal.balance.toFixed(2)} ${currency}`];
    if (delivered === items.length) lines.push("All orders delivered! 🎉");
    else if (delivered) lines.push(`${delivered}/${items.length} delivered; the rest will be completed by the merchant shortly.`);
    else lines.push("Delivery will be completed by the merchant shortly — you'll get a confirmation message here once it's delivered.");
    return this.send(chatId, lines.join("\n"));
  }

  async payFromWallet(chatId, sess) {
    const product = await this.findProduct(sess.pack, sess.region);
    sess.batch = [{ product, uid: sess.uid, sid: sess.sid, price: parseFloat(product.price) }];
    return this.payBatch(chatId, sess);
  }

  async notifyDelivered(order) {
    const chatId = order.telegram_chat_id;
    if (chatId) {
      await this.send(chatId,
        `✅ <b>Delivered!</b>\nOrder ${order.game_order}\n`
        + `Product ${order.productid} for MLBB ID ${order.uid} `
        + `(Zone ${order.sid}) has been delivered.`);
    }
  }

  // -- quick orders (/br, /ph, bulk) ----------------------------------------
  async parseQuickOrder(chatId, region, args) {
    const info = REGIONS[region];
    if ((await this.regionProducts(region)).length === 0) {
      return this.send(
        chatId,
        `${info.flag} ${info.name} packs aren't available yet.\n`
        + "The official pack list for this region is still being "
        + "verified — no prices have been set, so no order can be "
        + "placed for this region right now.");
    }
    if (!args.length || args.length % 3 !== 0) {
      return this.send(
        chatId,
        `Usage: /${region.toLowerCase()} &lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt; ...\n`
        + `Example: /${region.toLowerCase()} 986305 2375 86\n`
        + `Bulk: up to ${BULK_MAX} orders in one command.`);
    }
    const triplets = [];
    for (let i = 0; i < args.length; i += 3) triplets.push(args.slice(i, i + 3));
    if (triplets.length > BULK_MAX) {
      return this.send(
        chatId,
        `Bulk max is ${BULK_MAX} orders per command — you sent ${triplets.length}. Split it into smaller commands.`);
    }
    const items = [];
    for (const [uid, sid, diamonds] of triplets) {
      if (!/^\d+$/.test(uid) || !/^\d+$/.test(sid) || !/^\d+$/.test(diamonds)) {
        return this.send(
          chatId,
          "UID, Zone and diamonds must all be numbers.\n"
          + `Bad entry: ${uid} ${sid} ${diamonds}`);
      }
      const product = await this.findPackByDiamonds(parseInt(diamonds, 10), region);
      if (!product) {
        return this.send(
          chatId,
          `No pack for ${diamonds} diamonds in ${region}. Use /packs to see the available packs.`);
      }
      items.push({ product, uid, sid, price: parseFloat(product.price) });
    }
    const total = items.reduce((s, it) => s + it.price, 0);
    const sess = { step: "batch_confirm", region, batch: items };
    this.setSession(sess);
    const lines = ["Please check carefully, then confirm:"];
    for (const it of items) {
      lines.push(`• ${it.product.name} — ID ${it.uid} (Zone ${it.sid}) — ${it.price.toFixed(2)} ${info.currency}`);
    }
    lines.push(`\nTotal: ${total.toFixed(2)} ${info.currency} (${items.length} order${items.length > 1 ? "s" : ""})`);
    const markup = kb([
      [{ text: "✅ Confirm & pay", callback_data: "batch:yes" },
       { text: "❌ Cancel", callback_data: "batch:no" }]]);
    return this.send(chatId, lines.join("\n"), markup);
  }

  // -- admin (stats + pack prices; ADMIN_CHAT_IDS only, no password) ---------
  adminMenu() {
    return kb([
      [{ text: "📊 Stats", callback_data: "admin:stats" },
       { text: "💎 Packs", callback_data: "admin:packs" }],
      [{ text: "⬅️ Back to main menu", callback_data: "menu:main" }],
    ]);
  }

  async showAdminMenu(chatId) {
    const stats = await store.getStats(this.db);
    const totals = await store.getRegionTotals(this.db);
    const walletLines = Object.keys(totals).sort()
      .map((c) => `Wallet ${REGIONS[c].flag} [${c}]: ${totals[c].toFixed(2)} ${REGIONS[c].currency}`)
      .join("\n");
    return this.send(chatId,
      "🛠 <b>Admin Panel</b>\n\n"
      + `Users: ${stats.users}\n`
      + `Orders: ${stats.orders} (${stats.delivered} delivered)\n`
      + (walletLines ? walletLines + "\n" : "")
      + `Total funded: ${stats.total_funded.toFixed(2)}\n`
      + `Total spent: ${stats.total_spent.toFixed(2)}`,
      this.adminMenu());
  }

  async showAdminRegions(chatId) {
    const rows = [[
      { text: `${REGIONS.BR.flag} ${REGIONS.BR.name}`, callback_data: "admin:packlist:BR" },
      { text: `${REGIONS.PH.flag} ${REGIONS.PH.name}`, callback_data: "admin:packlist:PH" },
    ]];
    rows.push([{ text: "⬅️ Admin menu", callback_data: "admin:menu" }]);
    return this.send(chatId, "💎 Set pack prices — pick a region:", kb(rows));
  }

  async showAdminPacks(chatId, region) {
    const products = await this.regionProducts(region);
    if (!products.length) {
      return this.send(chatId, `No packs configured for ${region} yet.`);
    }
    const rows = [];
    for (let i = 0; i < products.length; i += 2) {
      rows.push(products.slice(i, i + 2).map((p) => ({
        text: `${p.name} - ${p.price}`, callback_data: `admin:pack:${region}:${p.pid}`,
      })));
    }
    rows.push([{ text: "⬅️ Regions", callback_data: "admin:packs" },
               { text: "⬅️ Admin menu", callback_data: "admin:menu" }]);
    return this.send(chatId, `💎 Set pack prices (${region}) — tap a pack:`, kb(rows));
  }

  async handleAdminCallback(chatId, data) {
    if (data === "admin:menu") return this.showAdminMenu(chatId);
    if (data === "admin:stats") {
      const stats = await store.getStats(this.db);
      const totals = await store.getRegionTotals(this.db);
      const walletLines = Object.keys(totals).sort()
        .map((c) => `Wallet ${REGIONS[c].flag} [${c}]: ${totals[c].toFixed(2)} ${REGIONS[c].currency}`)
        .join("\n");
      await this.send(chatId,
        `📊 <b>Stats</b>\nUsers: ${stats.users}\nOrders: ${stats.orders} (${stats.delivered} delivered)\n` + walletLines);
      return this.send(chatId, "Back to admin:", this.adminMenu());
    }
    if (data === "admin:packs") return this.showAdminRegions(chatId);
    if (data.startsWith("admin:packlist:")) {
      return this.showAdminPacks(chatId, data.split(":")[2]);
    }
    if (data.startsWith("admin:pack:")) {
      const [, , region, pid] = data.split(":");
      const product = await this.findProduct(pid, region);
      if (!product) return this.send(chatId, "Pack not found.");
      const markup = kb([
        [{ text: "✏️ Set new price", callback_data: `admin:setprice:${region}:${pid}` }],
        [{ text: "⬅️ Packs", callback_data: `admin:packlist:${region}` },
         { text: "⬅️ Admin menu", callback_data: "admin:menu" }],
      ]);
      return this.send(chatId,
        `💎 ${product.name}\nCurrent price: ${product.price} ${REGIONS[region].currency}`,
        markup);
    }
    if (data.startsWith("admin:setprice:")) {
      const [, , region, pid] = data.split(":");
      const product = await this.findProduct(pid, region);
      if (!product) return this.send(chatId, "Pack not found.");
      this.setSession({ step: "admin_set_price", admin_price_region: region, admin_price_pid: pid });
      return this.send(chatId,
        `Send the new price for ${product.name} in ${REGIONS[region].currency} (e.g. 6.50).`);
    }
    return null;
  }

  // -- commands / messages ----------------------------------------------------
  helpText() {
    return (
      "🤖 <b>All Commands</b>\n\n"
      + "/start — Welcome + your balances + order examples\n"
      + "/getid — Your account ID\n"
      + "/packs — List all packs and prices\n"
      + "/orders — List your orders\n"
      + "/balance — Your per-region balances\n"
      + "/br &lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt; — Quick order, Brazil\n"
      + "/ph &lt;uid&gt; &lt;zone&gt; &lt;diamonds&gt; — Quick order, Philippines\n"
      + "Bulk: repeat uid + zone + diamonds, up to 10 orders in one "
      + "command, e.g. /br 986305 2375 86 986305 2375 86\n"
      + "/addfund [region] &lt;amount&gt; — Add funds (test mode)\n"
      + "/admin — Admin panel\n"
      + "/help — This list");
  }

  async handleMessage(chatId, text) {
    await this.loadSession(chatId, false);
    try {
      return await this.handleMessageInner(chatId, text);
    } finally {
      await this.persistSession();
    }
  }

  async handleMessageInner(chatId, text) {
    const sess = this._sess;
    text = (text || "").trim();
    if (text.startsWith("/admin")) {
      if (!this.isAdmin(chatId)) return this.send(chatId, "Admin access only.");
      return this.showAdminMenu(chatId);
    }
    if (sess && sess.step === "admin_set_price") {
      if (!this.isAdmin(chatId)) {
        this.dropSession();
        return this.send(chatId, "Admin access only.");
      }
      const region = sess.admin_price_region || "BR";
      const pid = sess.admin_price_pid;
      const product = pid ? await this.findProduct(pid, region) : null;
      let value = -1;
      try {
        value = parseFloat(text.replace(",", "."));
        if (Number.isNaN(value)) value = -1;
      } catch { value = -1; }
      if (!product || !(value > 0)) {
        return this.send(chatId, "Send a valid price above 0, e.g. 6.50.");
      }
      const old = product.price;
      const ok = await store.updateProductPrice(this.db, region, pid, value.toFixed(2));
      if (ok) {
        this.dropSession();
        return this.send(chatId, `✅ ${product.name} price updated: ${old} → ${value.toFixed(2)}.`);
      }
      return this.send(chatId, "Could not save the new price — nothing changed.");
    }
    if (text.startsWith("/start")) return this.start(chatId);
    if (text.startsWith("/getid")) {
      return this.send(chatId, `🆔 <b>Your Account ID:</b> ${chatId}\nThis is your Telegram ID for this bot.`);
    }
    if (text.startsWith("/help")) return this.send(chatId, this.helpText());
    if (text.startsWith("/packs")) {
      await this.send(chatId, await this.packsText("BR"));
      if ((await this.regionProducts("PH")).length) {
        await this.send(chatId, await this.packsText("PH"));
      }
      return this.send(chatId, "Want to buy one? Tap Top Up below:", this.mainMenu());
    }
    if (text.startsWith("/orders")) {
      const orders = await store.listOrders(this.db, chatId);
      if (!orders.length) return this.send(chatId, "No orders yet. Use /start to top up.");
      const lines = orders.map((o) => {
        const region = CURRENCY_REGION[o.currency] || o.currency;
        return `${o.game_order} - ${o.productid} [${region}] - ${o.status}`;
      });
      return this.send(chatId, "Your orders:\n" + lines.join("\n"));
    }
    if (text.startsWith("/balance")) {
      const lines = ["💰 <b>Your Current Balance</b>", ...await this.balanceLines(chatId)];
      return this.send(chatId, lines.join("\n"));
    }
    if (text.startsWith("/addfund")) {
      const parts = text.split(/\s+/);
      let region = "BR", amountS = null;
      if (parts.length === 2) amountS = parts[1];
      else if (parts.length === 3 && REGIONS[parts[1].toUpperCase()]) {
        region = parts[1].toUpperCase(); amountS = parts[2];
      }
      if (amountS === null) {
        return this.send(chatId,
          "Usage: /addfund [region] <amount>\nExamples: /addfund 100, /addfund br 100");
      }
      const amount = parseFloat(amountS);
      if (Number.isNaN(amount) || amount <= 0) {
        return this.send(chatId, "Send a positive amount, e.g. /addfund 100");
      }
      if (!this.autoCreditTest) {
        return this.send(chatId, "Test auto-credit is off. Ask the merchant to add funds to your wallet.");
      }
      const bal = await store.creditRegionBalance(this.db, chatId, region, amount);
      return this.send(chatId,
        `✅ <b>Test mode:</b> ${amount.toFixed(2)} ${REGIONS[region].currency} added instantly to your ${region} wallet.\n`
        + `New ${region} balance: ${bal.balance.toFixed(2)}`);
    }
    const m = text.toLowerCase().match(/^\/(br|ph)(?:\s+(.*))?$/);
    if (m) {
      const region = m[1].toUpperCase();
      const args = m[2] ? m[2].split(/\s+/) : [];
      return this.parseQuickOrder(chatId, region, args);
    }
    if (!sess) return this.start(chatId);
    if (sess.step === "uid") {
      if (!/^\d+$/.test(text)) {
        return this.send(chatId, "MLBB ID must be numbers only. Send it again.");
      }
      sess.uid = text;
      sess.step = "sid";
      return this.send(chatId,
        "Now send your Zone ID (the number in brackets after your MLBB ID, e.g. 1234).");
    }
    if (sess.step === "sid") {
      if (!/^\d+$/.test(text)) {
        return this.send(chatId, "Zone ID must be numbers only. Send it again.");
      }
      sess.sid = text;
      return this.sendConfirm(chatId, sess);
    }
    return this.send(chatId, "Use /start to begin a new top-up.");
  }

  async handleCallback(chatId, data) {
    await this.loadSession(chatId, true);
    try {
      return await this.handleCallbackInner(chatId, data);
    } finally {
      await this.persistSession();
    }
  }

  async handleCallbackInner(chatId, data) {
    const sess = this._sess;
    if (data.startsWith("admin:")) {
      if (!this.isAdmin(chatId)) return this.send(chatId, "Admin access only.");
      return this.handleAdminCallback(chatId, data);
    }
    if (data === "menu:main") return this.start(chatId);
    if (data === "menu:topup") {
      this.setSession({ step: "region" });
      return this.send(chatId, "Pick a region:", this.regionMenu());
    }
    if (data.startsWith("region:")) {
      const region = data.split(":")[1];
      if (!REGIONS[region]) return this.send(chatId, "Unknown region.");
      if (!(await this.regionProducts(region)).length) {
        return this.send(chatId, `${REGIONS[region].flag} ${REGIONS[region].name} packs aren't available yet.`);
      }
      return this.showPacks(chatId, region, 0);
    }
    if (data === "menu:packs") {
      await this.send(chatId, await this.packsText("BR"));
      if ((await this.regionProducts("PH")).length) {
        await this.send(chatId, await this.packsText("PH"));
      }
      return this.send(chatId, "Back to menu:", this.mainMenu());
    }
    if (data.startsWith("packpage:")) {
      let region = "BR", page = 0;
      try {
        const parts = data.split(":");
        region = parts[1]; page = parseInt(parts[2], 10);
        if (Number.isNaN(page)) throw new Error("bad page");
      } catch { region = "BR"; page = 0; }
      return this.showPacks(chatId, region, page);
    }
    if (data === "menu:balance") {
      const lines = ["💰 <b>Your Current Balance</b>", ...await this.balanceLines(chatId)];
      await this.send(chatId, lines.join("\n"));
      return this.send(chatId, "Back to menu:", this.mainMenu());
    }
    if (data === "menu:orders") {
      const orders = await store.listOrders(this.db, chatId);
      if (!orders.length) {
        await this.send(chatId, "No orders yet. Use Top Up from the menu.");
      } else {
        const lines = orders.map((o) => {
          const region = CURRENCY_REGION[o.currency] || o.currency;
          return `${o.game_order} - ${o.productid} [${region}] - ${o.status}`;
        });
        await this.send(chatId, "Your orders:\n" + lines.join("\n"));
      }
      return this.send(chatId, "Back to menu:", this.mainMenu());
    }
    if (data === "menu:getid") {
      return this.send(chatId, `🆔 <b>Your Account ID:</b> ${chatId}`);
    }
    if (data === "menu:help") return this.handleMessage(chatId, "/help");
    if (data.startsWith("pack:")) {
      const parts = data.split(":");
      let region, pid;
      if (parts.length === 3) { [, region, pid] = parts; }
      else { region = "BR"; pid = parts[1]; }
      const product = await this.findProduct(pid, region);
      if (!product) return this.send(chatId, "Pack not found. Pick another:");
      sess.pack = product.pid;
      sess.region = region;
      sess.step = "uid";
      return this.send(chatId,
        `Pack: ${product.name} - ${product.price} ${REGIONS[region].currency}.\nSend your MLBB ID (numbers only).`);
    }
    if (data === "confirm:yes") {
      if (!sess.pack || !sess.uid || !sess.sid) {
        this.dropSession();
        return this.start(chatId);
      }
      return this.payFromWallet(chatId, sess);
    }
    if (data === "confirm:no") {
      this.dropSession();
      return this.start(chatId);
    }
    if (data === "batch:yes") {
      if (!sess.batch || !sess.region) {
        this.dropSession();
        return this.start(chatId);
      }
      return this.payBatch(chatId, sess);
    }
    if (data === "batch:no") {
      this.dropSession();
      return this.start(chatId);
    }
    return null;
  }

  async handleUpdate(upd) {
    const msg = upd.message || {};
    const cb = upd.callback_query || {};
    if (msg && "text" in msg) {
      await this.handleMessage(msg.chat.id, msg.text);
    } else if (cb && cb.data) {
      await this.handleCallback(cb.message.chat.id, cb.data);
      try {
        await this.answerCbFn(cb.id);
      } catch { /* non-fatal */ }
    }
  }
}
