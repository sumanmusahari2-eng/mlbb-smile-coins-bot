// Smile.one merchant API — Worker port of merchant_server.py.
// Same endpoints, same status codes (200/201/202/204/205/206/500),
// same sign verification. Delivery stays unwired, exactly like the
// Python OrderMerchantApp with no hook: a SUCCESS notification for an
// undelivered order answers 201 ("Error delivering items") and the
// order is NOT marked delivered. That is the current live behaviour,
// kept on purpose — never fake a delivery.
import * as store from "./store.js";
import { verifySign } from "./sign.js";

export class Merchant {
  constructor(env, db, hooks = {}) {
    this.env = env;
    this.db = db;
    // Called with a DELIVERED order (only when a real delivery hook is
    // wired). index.js wires it to bot.notifyDelivered.
    this.onDelivered = hooks.onDelivered || null;
  }

  badSign(body) {
    return !verifySign(body, this.env.SMILEONE_MERCHANT_KEY || "1234567890");
  }

  async verifyRole(uid, sid) {
    // DEMO lookup (same as roles.json). Replace with a real
    // game-account lookup before production use.
    const role = await store.getRole(this.db, uid);
    if (!role) return null;
    if (sid && role.sid && String(role.sid) !== String(sid)) return null;
    return role;
  }

  // Unwired delivery hook: returns false, like OrderMerchantApp.
  async deliverItems(order) {
    console.log(`[DELIVERY-NOT-CONFIGURED] order=${order.game_order} product=${order.productid} uid=${order.uid} sid=${order.sid} — merchant must deliver manually`);
    return false;
  }

  async roleCheck(body) {
    if (this.badSign(body)) return { status: 205, nickname: "", error: "Sign error" };
    const role = await this.verifyRole(body.uid, body.sid);
    if (!role) return { status: 202, nickname: "", error: "Uid or Sid non-existent" };
    return { status: 200, nickname: role.nickname, error: "" };
  }

  async gameOrder(body) {
    if (this.badSign(body)) return { status: 205, game_order: "", error: "Sign error" };
    if (!(await this.verifyRole(body.uid, body.sid))) {
      return { status: 202, game_order: "", error: "Uid or Sid non-existent" };
    }
    const product = await store.findProduct(this.db, body.productid, "BR")
      || await store.findProduct(this.db, body.productid, "PH");
    if (!product) return { status: 206, game_order: "", error: "Order initialization failed" };
    const orderId = await store.createOrder(this.db, null, body.uid, body.sid,
      body.productid, body.amount ?? product.price, body.currency ?? "Smile Coins");
    return { status: 200, game_order: orderId, error: "" };
  }

  async paymentNotification(body) {
    if (this.badSign(body)) {
      return { status: 205, game_order: body.game_order || "", game_status: "", error: "Sign error" };
    }
    const order = await store.getOrder(this.db, body.game_order || "");
    if (!order) {
      return { status: 201, game_order: body.game_order || "", game_status: "", error: "Order not found" };
    }
    const tradeStatus = body.trade_status || "";
    if (tradeStatus === "SUCCESS") {
      if (order.status === "DELIVERED") {
        return { status: 204, game_order: order.game_order, game_status: "SUCCESS", error: "Order has been delivered" };
      }
      if (await this.deliverItems(order)) {
        await store.updateOrder(this.db, order.game_order, "DELIVERED", body.trade_no || "");
        const updated = await store.getOrder(this.db, order.game_order);
        if (this.onDelivered) {
          try { await this.onDelivered(updated); } catch (e) { console.log(`[notify] failed: ${e}`); }
        }
        return { status: 200, game_order: order.game_order, game_status: "SUCCESS", error: "" };
      }
      return { status: 201, game_order: order.game_order, game_status: "", error: "Error delivering items" };
    }
    // REFUND / CHARGEBACK / REVERSED: never deliver.
    await store.updateOrder(this.db, order.game_order, tradeStatus, body.trade_no || "");
    return { status: 200, game_order: order.game_order, game_status: tradeStatus, error: "" };
  }

  async serverList(body) {
    if (this.badSign(body)) return { status: 205, serverList: [], error: "Sign error" };
    const servers = await store.listServers(this.db);
    return {
      status: 200,
      serverList: servers.map((s) => ({ serverName: s.server_name, serverId: s.server_id })),
      error: "",
    };
  }

  async productList(body) {
    if (this.badSign(body)) return { status: 205, product_list: [], error: "Sign error" };
    const products = await store.listProducts(this.db, "BR");
    return {
      status: 200,
      product_list: products.map((p) => ({
        pid: p.pid, icon: p.icon || "", original_price: p.original_price, price: p.price,
      })),
      error: "",
    };
  }
}
