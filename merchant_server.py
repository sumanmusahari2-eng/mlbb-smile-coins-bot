"""Merchant APIs that Smile.one calls, per the Smile.one Docs.

Endpoints (all POST, JSON body):
  /api/role-check            uid, sid?, sign  -> {status, nickname, error}
  /api/game-order            uid, productid, amount?, currency?, sign
                             -> {status, game_order, error}
  /api/payment-notification  game_order, uid, sid?, roleid?, productid,
                             amount?, currency?, trade_no, trade_status, sign
                             -> {status, game_order, game_status, error}
  /api/server-list           time?, sign -> {status, serverList, error}
  /api/product-list          uid?, sid?, time?, sign
                             -> {status, product_list, error}

Status codes (Appendix 1): 200 success, 201 not paid / delivery error,
202 uid/sid does not exist, 203 already has card, 204 already delivered,
205 sign error, 206 order init failed, 500 system error.
Trade statuses (Appendix 3): SUCCESS, REFUND, CHARGEBACK, REVERSED.

Delivery hook: deliver_items() is a STUB. It logs the delivery and marks
the order delivered. Replace it with your real MLBB / game delivery call.
Do NOT claim live delivery until that hook is real and tested.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from smile_sign import verify_sign
import store


class MerchantApp:
    def __init__(self, conn, config, on_delivered=None):
        self.conn = conn
        self.config = config
        self.products = store.load_json(config["products_path"], {"products": []})["products"]
        self.servers = store.load_json(config["products_path"], {}).get("servers", [])
        self.roles = store.load_json(config["roles_path"], {"roles": {}})["roles"]
        self.on_delivered = on_delivered  # callback(order) -> notify Telegram user
        self.lock = threading.Lock()

    # -- helpers ---------------------------------------------------------
    def _bad_sign(self, body):
        return not verify_sign(body, self.config["merchant_key"])

    def verify_role(self, uid, sid):
        """TODO: replace demo lookup with your real game-account lookup."""
        role = self.roles.get(str(uid))
        if not role:
            return None
        if sid and role.get("sid") and str(role["sid"]) != str(sid):
            return None
        return role

    def deliver_items(self, order):
        """STUB delivery. Replace with the real game delivery API call."""
        print(f"[DELIVERY-STUB] Deliver product {order['productid']} "
              f"to uid={order['uid']} sid={order['sid']} "
              f"order={order['game_order']}")
        return True

    def find_product(self, pid):
        for p in self.products:
            if str(p["pid"]) == str(pid):
                return p
        return None

    # -- endpoints -------------------------------------------------------
    def role_check(self, body):
        if self._bad_sign(body):
            return {"status": 205, "nickname": "", "error": "Sign error"}
        role = self.verify_role(body.get("uid"), body.get("sid"))
        if not role:
            return {"status": 202, "nickname": "", "error": "Uid or Sid non-existent"}
        return {"status": 200, "nickname": role["nickname"], "error": ""}

    def game_order(self, body):
        if self._bad_sign(body):
            return {"status": 205, "game_order": "", "error": "Sign error"}
        if not self.verify_role(body.get("uid"), body.get("sid")):
            return {"status": 202, "game_order": "", "error": "Uid or Sid non-existent"}
        product = self.find_product(body.get("productid"))
        if not product:
            return {"status": 206, "game_order": "", "error": "Order initialization failed"}
        order_id = store.create_order(
            self.conn, None, body.get("uid"), body.get("sid"),
            body.get("productid"), body.get("amount", product["price"]),
            body.get("currency", "Smile Coins"))
        return {"status": 200, "game_order": order_id, "error": ""}

    def payment_notification(self, body):
        if self._bad_sign(body):
            return {"status": 205, "game_order": body.get("game_order", ""),
                    "game_status": "", "error": "Sign error"}
        order = store.get_order(self.conn, body.get("game_order", ""))
        if not order:
            return {"status": 201, "game_order": body.get("game_order", ""),
                    "game_status": "", "error": "Order not found"}
        trade_status = body.get("trade_status", "")
        if trade_status == "SUCCESS":
            if order["status"] == "DELIVERED":
                return {"status": 204, "game_order": order["game_order"],
                        "game_status": "SUCCESS", "error": "Order has been delivered"}
            if self.deliver_items(order):
                store.update_order(self.conn, order["game_order"], "DELIVERED",
                                   body.get("trade_no", ""))
                order = store.get_order(self.conn, order["game_order"])
                if self.on_delivered:
                    try:
                        self.on_delivered(order)
                    except Exception as e:
                        print(f"[notify] failed: {e}")
                return {"status": 200, "game_order": order["game_order"],
                        "game_status": "SUCCESS", "error": ""}
            return {"status": 201, "game_order": order["game_order"],
                    "game_status": "", "error": "Error delivering items"}
        # REFUND / CHARGEBACK / REVERSED: never deliver.
        store.update_order(self.conn, order["game_order"], trade_status,
                           body.get("trade_no", ""))
        return {"status": 200, "game_order": order["game_order"],
                "game_status": trade_status, "error": ""}

    def server_list(self, body):
        if self._bad_sign(body):
            return {"status": 205, "serverList": [], "error": "Sign error"}
        return {"status": 200,
                "serverList": [{"serverName": s["serverName"], "serverId": s["serverId"]}
                               for s in self.servers], "error": ""}

    def product_list(self, body):
        if self._bad_sign(body):
            return {"status": 205, "product_list": [], "error": "Sign error"}
        plist = [{"pid": p["pid"], "icon": p.get("icon", ""),
                  "original_price": p["original_price"], "price": p["price"]}
                 for p in self.products]
        return {"status": 200, "product_list": plist, "error": ""}


def make_handler(app):
    routes = {
        "/api/role-check": app.role_check,
        "/api/game-order": app.game_order,
        "/api/payment-notification": app.payment_notification,
        "/api/server-list": app.server_list,
        "/api/product-list": app.product_list,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            print("[http]", *args)

        def _send(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                return self._send({"status": 500, "error": "Invalid JSON"})
            fn = routes.get(self.path)
            if not fn:
                return self._send({"status": 500, "error": "Unknown endpoint"})
            try:
                self._send(fn(body))
            except Exception as e:
                print("[merchant] error:", e)
                self._send({"status": 500, "error": "System error"})

    return Handler


def serve(app, host, port):
    server = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"[merchant] listening on http://{host}:{port}")
    server.serve_forever()
