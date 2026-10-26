"""Test send() retry-with-backoff (fix for dropped replies on proxy 503s)."""
import telegram_bot


class FakeBot(telegram_bot.Bot):
    def __init__(self, failures):
        # Bypass Bot.__init__ (needs db/merchant); send() only uses self.api.
        self.failures = failures
        self.calls = 0

    def api(self, method, payload):
        self.calls += 1
        if self.calls <= self.failures:
            raise Exception("Tunnel connection failed: 503 Service Unavailable")
        return {"ok": True, "result": {"message_id": 1}}


def main():
    telegram_bot.time.sleep = lambda s: None  # no real waiting in tests

    b = FakeBot(2)
    r = b.send(123, "hi")
    assert r == {"ok": True, "result": {"message_id": 1}}, r
    assert b.calls == 3, b.calls

    b = FakeBot(5)
    r = b.send(123, "hi")
    assert r is None, r
    assert b.calls == 3, b.calls

    print("send retry tests passed")


if __name__ == "__main__":
    main()
