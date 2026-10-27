// Cloudflare Worker entry: Telegram webhook + Smile.one merchant API.
// Routes:
//   GET  /health              -> { ok: true }
//   GET  /                    -> short info
//   POST /telegram-webhook    -> Telegram updates (secret_token checked
//                                when WEBHOOK_SECRET is set)
//   POST /api/role-check | /api/game-order | /api/payment-notification
//        /api/server-list | /api/product-list -> merchant API (sign-checked)
//   GET  /setup?key=...      -> one-time setup, guarded by SETUP_SECRET:
//                                sets the Telegram webhook to this
//                                Worker's own URL + the menu commands.
import { Bot } from "./bot.js";
import { Merchant } from "./merchant.js";
import { tg, setCommands } from "./telegram.js";

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const db = env.DB;
    const bot = new Bot(env, db);
    const merchant = new Merchant(env, db, {
      onDelivered: (order) => bot.notifyDelivered(order),
    });

    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "mlbb-smile-bot-worker" });
    }
    if (request.method === "GET" && url.pathname === "/") {
      return new Response("MLBB Smile Coins bot (Cloudflare Worker).", {
        headers: { "Content-Type": "text/plain" },
      });
    }

    if (request.method === "POST" && url.pathname === "/telegram-webhook") {
      const secret = env.WEBHOOK_SECRET || "";
      if (secret && request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== secret) {
        return json({ ok: false, error: "bad secret" }, 403);
      }
      let upd;
      try {
        upd = await request.json();
      } catch {
        return json({ ok: false, error: "Invalid JSON" });
      }
      try {
        await bot.handleUpdate(upd);
      } catch (e) {
        console.log(`[webhook] handler error: ${e}`);
      }
      // Always 200: Telegram retries anything else, duplicating orders.
      return json({ ok: true });
    }

    const merchantRoutes = {
      "/api/role-check": (b) => merchant.roleCheck(b),
      "/api/game-order": (b) => merchant.gameOrder(b),
      "/api/payment-notification": (b) => merchant.paymentNotification(b),
      "/api/server-list": (b) => merchant.serverList(b),
      "/api/product-list": (b) => merchant.productList(b),
    };
    if (request.method === "POST" && merchantRoutes[url.pathname]) {
      let body;
      try {
        body = await request.json();
      } catch {
        return json({ status: 500, error: "Invalid JSON" });
      }
      try {
        return json(await merchantRoutes[url.pathname](body));
      } catch (e) {
        console.log(`[merchant] error: ${e}`);
        return json({ status: 500, error: "System error" });
      }
    }
    if (request.method === "POST") {
      return json({ status: 500, error: "Unknown endpoint" });
    }

    if (request.method === "GET" && url.pathname === "/setup") {
      const setupSecret = env.SETUP_SECRET || "";
      if (!setupSecret || url.searchParams.get("key") !== setupSecret) {
        return json({ ok: false, error: "forbidden" }, 403);
      }
      const webhookUrl = `${url.origin}/telegram-webhook`;
      const results = {};
      try {
        const payload = { url: webhookUrl };
        if (env.WEBHOOK_SECRET) payload.secret_token = env.WEBHOOK_SECRET;
        results.setWebhook = await tg(env, "setWebhook", payload);
      } catch (e) {
        results.setWebhook = { ok: false, error: String(e) };
      }
      try {
        results.setCommands = await setCommands(env, bot.adminIds);
      } catch (e) {
        results.setCommands = { ok: false, error: String(e) };
      }
      return json({ ok: true, webhookUrl, results });
    }

    return new Response("Not found", { status: 404 });
  },
};
