"""Smile.one merchant signing, as described in the Smile.one Docs.

Summary of the documented scheme (see README for the ambiguity note):
  1. Take all business fields, EXCLUDING 'sign' itself.
  2. Sort them by key, ascending.
  3. Join them as key=value pairs with '&'.
  4. Append the merchant encryption key DIRECTLY after that string.
  5. sign = md5(md5(that string))

The docs show the string in plain form, but their PHP example uses
http_build_query(), which URL-encodes values. Those two can differ when a
value contains special characters. To avoid a signature mismatch in
production, verify_sign() accepts either the plain form or the
URL-encoded form. Both still require the secret merchant key.
"""
import hashlib
import urllib.parse


def _plain_string(fields: dict) -> str:
    items = sorted((k, v) for k, v in fields.items() if k != "sign")
    return "&".join(f"{k}={v}" for k, v in items)


def _encoded_string(fields: dict) -> str:
    items = sorted((k, v) for k, v in fields.items() if k != "sign")
    # http_build_query-style encoding, close enough for the common cases.
    return urllib.parse.urlencode(items)


def make_sign(fields: dict, merchant_key: str) -> str:
    plain = _plain_string(fields) + merchant_key
    return hashlib.md5(hashlib.md5(plain.encode()).hexdigest().encode()).hexdigest()


def verify_sign(fields: dict, merchant_key: str) -> bool:
    given = fields.get("sign", "")
    if not given:
        return False
    if make_sign(fields, merchant_key) == given:
        return True
    # Encoded variant, for the http_build_query ambiguity noted above.
    encoded = _encoded_string(fields) + merchant_key
    alt = hashlib.md5(hashlib.md5(encoded.encode()).hexdigest().encode()).hexdigest()
    return alt == given
