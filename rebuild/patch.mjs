// THE BUCKET REBUILD (LMS-Discography docs/hosted-pack-plan.md §11, end): one keyed bucket rebuilt from
// its stored value and one line of an entry delta (builder/entry_delta.py), as the Worker does it in D1
// and rebuild/apply_node.mjs does it under Node. Runs on Workers and on Node 22+ alike (Web APIs only).
//
// A bucket is checked by its CANONICAL text (entry_delta.canon): its entries in key order (code points,
// the UTF-8 bytes' order), '{' + JSON key + ':' + JSON.stringify(value) joined by ',' + '}', SHA-256.

const te = new TextEncoder();

// Code point order. JS's own < compares UTF-16 units, which puts U+10000 and above (surrogate pairs,
// 0xD800-0xDFFF) before U+E000-U+FFFF; Python and SQLite put them after.
export function cmpKey(a, b) {
    const n = Math.min(a.length, b.length);
    for (let i = 0; i < n; i++) {
        const x = a.charCodeAt(i), y = b.charCodeAt(i);
        if (x !== y) {
            const sx = x >= 0xd800 && x <= 0xdfff, sy = y >= 0xd800 && y <= 0xdfff;
            return sx === sy ? x - y : sx ? 1 : -1;
        }
    }
    return a.length - b.length;
}

// m: Map key -> the value's JSON text
export function canon(m) {
    return '{' + [...m.keys()].sort(cmpKey).map((k) => JSON.stringify(k) + ':' + m.get(k)).join(',') + '}';
}

export async function sha256(text) {
    const d = new Uint8Array(await crypto.subtle.digest('SHA-256', te.encode(text)));
    let s = '';
    for (const x of d) s += x.toString(16).padStart(2, '0');
    return s;
}

// D1 hands a BLOB back as an array of numbers, node:sqlite as a Uint8Array.
export const u8 = (v) => (v instanceof Uint8Array ? v : v instanceof ArrayBuffer ? new Uint8Array(v) : Uint8Array.from(v));

export async function inflate(v) {
    return new Response(new Blob([u8(v)]).stream().pipeThrough(new DecompressionStream('deflate-raw'))).text();
}

export async function deflate(text) {
    const s = new Blob([te.encode(text)]).stream().pipeThrough(new CompressionStream('deflate-raw'));
    return new Uint8Array(await new Response(s).arrayBuffer());
}

// The stored bucket as key -> value text (empty when there is none).
export async function entriesOf(stored) {
    const m = new Map();
    if (stored != null) {
        const obj = JSON.parse(await inflate(stored));
        for (const k of Object.keys(obj)) m.set(k, JSON.stringify(obj[k]));
    }
    return m;
}

export async function hashOf(m) {
    return m.size ? sha256(canon(m)) : null;
}

// stored: the bucket's stored value or null; line: {put: [[key, value]], del: [key], ha, hb}.
// -> {ok: true, out: Uint8Array (write it) | null (delete the row) | undefined (already the after: no write)}
//    {ok: false, why: 'before' | 'del' | 'after'}: nothing to write
// A bucket already holding the after (a batch retried after its answer was lost) is left as it is.
export async function rebuild(stored, line) {
    const m = await entriesOf(stored);
    const before = m.size;
    const h0 = await hashOf(m);
    if (h0 !== line.ha) {
        return h0 === line.hb ? { ok: true, out: undefined, before, after: before, already: true }
                              : { ok: false, why: 'before', before };
    }
    for (const k of line.del) if (!m.delete(k)) return { ok: false, why: 'del', before };
    for (const [k, v] of line.put) m.set(k, JSON.stringify(v));
    if (!m.size) return line.hb === null ? { ok: true, out: null, before, after: 0 } : { ok: false, why: 'after', before };
    const text = canon(m);
    if (await sha256(text) !== line.hb) return { ok: false, why: 'after', before };
    return { ok: true, out: await deflate(text), before, after: m.size };
}
