"""Local tests for the merchant endpoints. No Telegram token needed.

Run: python3 test_endpoints.py
Starts the merchant server on 127.0.0.1:8123 in a thread, then calls
role-check, game-order, product-list, server-list and a SUCCESS
payment notification, checking the documented status codes.
"""
import json
import os
import sys
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store
from merchant_server import MerchantApp, serve
from smile_sign import make_sign

KEY = "testkey123"
BASE = "http://127.0.0.1:8123"


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def signed(body):
    body = dict(body)
    body["sign"] = make_sign(body, KEY)
    return body


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    conn = store.init_db(":memory:")
    config = {"merchant_key": KEY,
              "products_path": os.path.join(base, "products.json"),
              "roles_path": os.path.join(base, "roles.json")}
    app = MerchantApp(conn, config)
    threading.Thread(target=serve, args=(app, "127.0.0.1", 8123), daemon=True).start()
    import time
    time.sleep(0.5)

    r = post("/api/role-check", signed({"uid": "123456789", "sid": "1234"}))
    assert r["status"] == 200 and r["nickname"] == "DemoPlayer", r
    print("role-check OK:", r)

    r = post("/api/role-check", signed({"uid": "000000", "sid": "1"}))
    assert r["status"] == 202, r
    print("role-check unknown OK:", r)

    r = post("/api/role-check", {"uid": "123456789", "sid": "1234", "sign": "bad"})
    assert r["status"] == 205, r
    print("sign error OK:", r)

    r = post("/api/game-order", signed({"uid": "123456789", "productid": "2"}))
    assert r["status"] == 200 and r["game_order"], r
    order_id = r["game_order"]
    print("game-order OK:", r)

    r = post("/api/product-list", signed({"time": "123"}))
    assert r["status"] == 200 and len(r["product_list"]) >= 4, r
    print("product-list OK:", len(r["product_list"]), "products")

    r = post("/api/server-list", signed({"time": "123"}))
    assert r["status"] == 200, r
    print("server-list OK:", r)

    # Simulate a Telegram-created order, then a SUCCESS notification.
    oid = store.create_order(conn, 999, "123456789", "1234", "2", "86.00", "Smile Coins")
    r = post("/api/payment-notification", signed({
        "game_order": oid, "uid": "123456789", "sid": "1234",
        "productid": "2", "trade_no": "T123", "trade_status": "SUCCESS"}))
    assert r["status"] == 200 and r["game_status"] == "SUCCESS", r
    assert store.get_order(conn, oid)["status"] == "DELIVERED"
    print("payment-notification SUCCESS OK:", r)

    # Idempotency: second SUCCESS must not re-deliver.
    r = post("/api/payment-notification", signed({
        "game_order": oid, "uid": "123456789", "sid": "1234",
        "productid": "2", "trade_no": "T123", "trade_status": "SUCCESS"}))
    assert r["status"] == 204, r
    print("payment-notification duplicate OK:", r)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
