// Pure-JS MD5 (RFC 1321). WebCrypto has no MD5, and the Smile.one
// merchant signature is md5(md5(...)), so Workers need this.
// K constants are computed at runtime (floor(abs(sin(i+1)) * 2^32))
// instead of a 64-entry table — same values, fewer typos to make.

function leftRotate(x, c) {
  return ((x << c) | (x >>> (32 - c))) >>> 0;
}

const S = [
  7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
  5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
  4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
  6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];

export function md5Hex(input) {
  const msg = new TextEncoder().encode(input);
  const bitLen = msg.length * 8;
  // Padding: 0x80, zeros, then 64-bit little-endian bit length.
  const totalLen = (((msg.length + 9 + 63) >> 6) << 6);
  const buf = new Uint8Array(totalLen);
  buf.set(msg);
  buf[msg.length] = 0x80;
  const view = new DataView(buf.buffer);
  view.setUint32(totalLen - 8, bitLen >>> 0, true);
  view.setUint32(totalLen - 4, Math.floor(bitLen / 4294967296), true);

  let a0 = 0x67452301, b0 = 0xefcdab89, c0 = 0x98badcfe, d0 = 0x10325476;
  for (let off = 0; off < totalLen; off += 64) {
    const M = [];
    for (let i = 0; i < 16; i++) M.push(view.getUint32(off + i * 4, true));
    let A = a0, B = b0, C = c0, D = d0;
    for (let i = 0; i < 64; i++) {
      let F, g;
      if (i < 16) { F = (B & C) | (~B & D); g = i; }
      else if (i < 32) { F = (D & B) | (~D & C); g = (5 * i + 1) % 16; }
      else if (i < 48) { F = B ^ C ^ D; g = (3 * i + 5) % 16; }
      else { F = C ^ (B | ~D); g = (7 * i) % 16; }
      const K = Math.floor(Math.abs(Math.sin(i + 1)) * 4294967296) >>> 0;
      F = (F + A + K + M[g]) >>> 0;
      A = D; D = C; C = B;
      B = (B + leftRotate(F, S[i])) >>> 0;
    }
    a0 = (a0 + A) >>> 0; b0 = (b0 + B) >>> 0;
    c0 = (c0 + C) >>> 0; d0 = (d0 + D) >>> 0;
  }
  const out = new Uint8Array(16);
  const oview = new DataView(out.buffer);
  oview.setUint32(0, a0, true); oview.setUint32(4, b0, true);
  oview.setUint32(8, c0, true); oview.setUint32(12, d0, true);
  return [...out].map((b) => b.toString(16).padStart(2, "0")).join("");
}
