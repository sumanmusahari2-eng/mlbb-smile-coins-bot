// Smile.one merchant signing, ported exactly from smile_sign.py:
//   1. All business fields except 'sign', sorted by key ascending.
//   2. Joined as key=value pairs with '&'.
//   3. Merchant encryption key appended directly.
//   4. sign = md5(md5(that string)) — inner md5 as a hex string.
// The URL-encoded variant covers the docs' http_build_query ambiguity.
import { md5Hex } from "./md5.js";

function plainString(fields) {
  return Object.keys(fields)
    .filter((k) => k !== "sign")
    .sort()
    .map((k) => `${k}=${fields[k]}`)
    .join("&");
}

function encodedString(fields) {
  const params = new URLSearchParams();
  Object.keys(fields)
    .filter((k) => k !== "sign")
    .sort()
    .forEach((k) => params.append(k, String(fields[k])));
  return params.toString();
}

export function makeSign(fields, merchantKey) {
  return md5Hex(md5Hex(plainString(fields) + merchantKey));
}

export function verifySign(fields, merchantKey) {
  const given = fields.sign || "";
  if (!given) return false;
  if (makeSign(fields, merchantKey) === given) return true;
  const alt = md5Hex(md5Hex(encodedString(fields) + merchantKey));
  return alt === given;
}
