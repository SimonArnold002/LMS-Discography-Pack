// The hosted pack's reader: what Discography asks its pack, answered from D1 in
// Pack.pm's own shapes (LMS-Discography Discography/Pack.pm), so the plugin's
// hosted client changes the transport and nothing that reads the answers.
// Reads both layouts builder/hosted.py writes, of the same content (LMS-Discography
// docs/hosted-pack-plan.md §11: today's pack plus every artist's group list, the
// whole release map, barcodes and service album ids, the alias / old-name keys):
//   narrow  the local pack's tables (format 3) plus the extras' tables, read with
//           Pack.pm's own SQL;
//   keyed   bucketed JSON documents, deflated, one primary-key read per bucket.
// The same code runs in the Worker and, for the local test, under Node behind
// worker/node-d1.mjs (Node 22.5+: node:sqlite, DecompressionStream).
//
// `db` is D1's interface, or one shaped like it:
//   db.prepare(sql).bind(...args).all() -> Promise<{ results: [ {col: value} ] }>
//   db.blob(Uint8Array) -> the value the driver binds as a BLOB

const KIND = { 1: 'member of band', 2: 'collaboration' };
const SVC_URL = [null, 'https://open.qobuz.com/artist/%s', 'https://www.deezer.com/artist/%s',
                 'https://www.discogs.com/artist/%s'];
const UUID_RE = /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i;
const MAX_PARAMS = 100;                 // D1: bound parameters per query
const GID_KINDS = new Set(['art', 'lst', 'grp', 'rel']);
const enc = new TextEncoder();
const dec = new TextDecoder();

// An mbid's 32 lower-case hex digits, or null for anything that is not a uuid
// (Pack.pm's _hex).
export function hexOf(mbid) {
    return typeof mbid === 'string' && UUID_RE.test(mbid) ? mbid.toLowerCase().replace(/-/g, '') : null;
}

export function uuidOf(h) {
    return typeof h === 'string' && h.length === 32
        ? `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}` : '';
}

const bytesOfHex = (h) => Uint8Array.from(h.match(/../g), (x) => parseInt(x, 16));
// D1 hands a BLOB back as an array of numbers, Node as a Uint8Array.
const u8 = (v) => (v instanceof Uint8Array ? v : Uint8Array.from(v));
const hexOfBytes = (b) => Array.from(u8(b), (x) => x.toString(16).padStart(2, '0')).join('');

// Perl's `cmp` on character strings: code point order (JS `<` compares UTF-16
// code units, which differs above U+FFFF).
export function cmpCp(a, b) {
    const ia = a[Symbol.iterator](), ib = b[Symbol.iterator]();
    for (;;) {
        const x = ia.next(), y = ib.next();
        if (x.done || y.done) return x.done ? (y.done ? 0 : -1) : 1;
        const cx = x.value.codePointAt(0), cy = y.value.codePointAt(0);
        if (cx !== cy) return cx < cy ? -1 : 1;
    }
}

async function inflate(v) {
    const s = new Blob([u8(v)]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
    return new Response(s).text();
}

function chunks(xs, n = MAX_PARAMS) {
    const out = [];
    for (let i = 0; i < xs.length; i += n) out.push(xs.slice(i, i + n));
    return out;
}

// --- the answers, in Pack.pm's shapes -----------------------------------------

function artistReply(gid, name, aliases, rels, urls) {
    return {
        id: uuidOf(gid), name,
        aliases: aliases.map(([n, loc, p]) => ({ name: n, locale: loc.length ? loc : null, primary: p ? 1 : null })),
        relations: [
            ...rels.map(([d, k, ep, g, n]) => ({
                type: KIND[k], direction: d ? 'backward' : 'forward', 'target-type': 'artist',
                attributes: ep ? ['eponymous'] : [], artist: { id: uuidOf(g), name: n } })),
            ...urls.filter(([s]) => SVC_URL[s])
                .map(([s, e]) => ({ 'target-type': 'url', url: { resource: SVC_URL[s].replace('%s', String(e)) } })),
        ],
    };
}

function groupExtras([boot, dg, aliases, editions, urls]) {
    return { boot: boot ? 1 : 0, discogs: dg == null ? null : dg > 0 ? `master:${dg}` : `release:${-dg}`,
             aliases, editions, urls: Object.fromEntries(urls.map(([k, u]) => [String(k), u])) };
}

function sameNameRow([gid, name, ng, cmt, type, cty, en], names) {
    const r = { mbid: uuidOf(gid), name, score: ng, disambiguation: cmt ?? null, country: cty ?? null,
                type: type == null ? null : (names.artist_type[type] ?? null) };
    if (en != null) r.en = en;
    return r;
}

function releaseRow(rg, type, sec, names) {
    const secIds = Object.keys(names.rg_sec).map(Number).sort((a, b) => a - b);
    return { rg: uuidOf(rg), type: type == null ? '' : (names.rg_type[type] ?? ''),
             secondary: secIds.filter((i) => (sec || 0) & (1 << i)).map((i) => names.rg_sec[i]) };
}

// --- keyed documents ------------------------------------------------------------

class Keyed {
    constructor(db, meta) {
        this.db = db;
        this.bits = Number(meta.hosted_bits);
        this.names = JSON.parse(meta.names);
        this.layout = 'keyed';
    }

    // A gid kind's bucket: the first `bits` bits of the gid; any other kind's:
    // of the SHA-1 of its key text (builder/hosted.py). bits 0: the key itself.
    async bucketKey(kind, key) {
        const raw = GID_KINDS.has(kind) ? bytesOfHex(key) : enc.encode(key);
        if (this.bits === 0) return raw;
        const src = GID_KINDS.has(kind) ? raw : new Uint8Array(await crypto.subtle.digest('SHA-1', raw));
        let x = 0n;
        for (let i = 0; i < 8; i++) x = (x << 8n) | BigInt(src[i]);
        const n = Number(x >> BigInt(64 - this.bits));
        return Uint8Array.of((n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255);
    }

    // { key -> entry } for the keys that have one; each bucket read once.
    async get(kind, keys) {
        const want = new Map();
        for (const k of keys) {
            const b = await this.bucketKey(kind, k);
            const id = hexOfBytes(b);
            if (!want.has(id)) want.set(id, { b, keys: new Set() });
            want.get(id).keys.add(k);
        }
        const out = new Map();
        for (const part of chunks([...want.values()])) {
            const { results } = await this.db
                .prepare(`SELECT k, v FROM ${kind} WHERE k IN (${part.map(() => '?').join(',')})`)
                .bind(...part.map((x) => this.db.blob(x.b))).all();
            for (const r of results) {
                const w = want.get(hexOfBytes(r.k));
                if (!w) continue;
                if (this.bits === 0) {
                    for (const k of w.keys) out.set(k, JSON.parse(dec.decode(u8(r.v))));
                } else {
                    const obj = JSON.parse(await inflate(r.v));
                    for (const k of w.keys) if (Object.prototype.hasOwnProperty.call(obj, k)) out.set(k, obj[k]);
                }
            }
        }
        return out;
    }

    // [gid, document] of the artist, through a merged-away gid's redirect.
    async artist(mbid) {
        const h = hexOf(mbid);
        if (!h) return null;
        let d = (await this.get('art', [h])).get(h);
        if (typeof d === 'string') {
            const t = d;
            d = (await this.get('art', [t])).get(t);
            return Array.isArray(d) ? [t, d] : null;
        }
        return Array.isArray(d) ? [h, d] : null;
    }

    async artistRead(mbid) {
        const x = await this.artist(mbid);
        if (!x) return [];
        const [g, d] = x;
        return [artistReply(g, d[0], d[5], d[6], d[7]), d[1]];
    }

    async collaboration(mbid) {
        const x = await this.artist(mbid);
        if (!x) return null;
        const d = x[1];
        const from = new Set(d[6].filter(([dir, k]) => dir === 1 && k === 2).map((r) => r[3]));
        return { members: from.size || 0, groups: d[1] || 0 };
    }

    async groupCount(mbid) {
        const x = await this.artist(mbid);
        return x ? x[1][1] : null;
    }

    // The artist's group list (kind lst, under the artist's own gid, so a
    // merged-away gid goes through the art redirect first); [] when it has none.
    async artistList(mbid) {
        const x = await this.artist(mbid);
        if (!x) return null;
        return (await this.get('lst', [x[0]])).get(x[0]) || [];
    }

    async groups(mbids) {
        const hs = (mbids || []).map(hexOf).filter(Boolean);
        const out = {};
        for (const [h, x] of await this.get('grp', hs)) out[uuidOf(h)] = groupExtras(x);
        return out;
    }

    async sameName(key) {
        if (typeof key !== 'string' || !key.length) return null;
        const rows = (await this.get('nam', [key])).get(key) || [];
        return rows.map((r) => sameNameRow(r, this.names));
    }

    async aliasName(key) {
        if (typeof key !== 'string' || !key.length) return null;
        return ((await this.get('ali', [key])).get(key) || []).map(([g, name, ng]) => ({ mbid: uuidOf(g), name, score: ng }));
    }

    async releases(mbids) {
        const hs = (mbids || []).map(hexOf).filter(Boolean);
        const got = await this.get('rel', hs);
        const out = {};
        for (const [h, [rg, t, s]] of got) out[uuidOf(h)] = releaseRow(rg, t, s, this.names);
        return out;
    }

    async barcode(code) {
        const k = String(code);
        return ((await this.get('bar', [k])).get(k) || []).map(uuidOf);
    }

    async svcAlbum(svc, ext) {
        const k = `${svc}:${ext}`;
        return ((await this.get('svc', [k])).get(k) || []).map(uuidOf);
    }
}

// --- the narrow tables, Pack.pm's own SQL (format 3) ------------------------------

class Narrow {
    constructor(db, meta, names, tables) {
        this.db = db;
        this.names = names;
        this.lists = tables.has('artist_rg');
        this.rgGid = this.lists ? 'rg' : 'rg_gid';
        this.layout = 'narrow';
    }

    async rows(sql, ...args) {
        return (await this.db.prepare(sql).bind(...args).all()).results;
    }

    async one(sql, ...args) {
        return (await this.rows(sql, ...args))[0];
    }

    async id(mbid) {
        const h = hexOf(mbid);
        if (!h) return null;
        const raw = this.db.blob(bytesOfHex(h));
        const r = (await this.one('SELECT id FROM artist WHERE gid = ?', raw))
               || (await this.one('SELECT artist AS id FROM artist_redirect WHERE gid = ?', raw));
        return r ? r.id : null;
    }

    async artistRead(mbid) {
        const id = await this.id(mbid);
        if (id == null) return [];
        const a = await this.one('SELECT gid, name, ngroups FROM artist WHERE id = ?', id);
        const aliases = (await this.rows('SELECT name, locale, prim FROM artist_alias WHERE artist = ?', id))
            .map((r) => [r.name, r.locale, r.prim])
            .sort((x, y) => cmpCp(x[0], y[0]) || cmpCp(x[1], y[1]));
        const rels = [];
        for (const [d, me, other] of [[0, 'a', 'b'], [1, 'b', 'a']]) {
            for (const r of await this.rows(`SELECT r.kind, r.epon, t.gid, t.name FROM artist_rel r JOIN artist t `
                                            + `ON t.id = r.${other} WHERE r.${me} = ? ORDER BY r.kind, r.skey`, id)) {
                rels.push([d, r.kind, r.epon, hexOfBytes(r.gid), r.name]);
            }
        }
        const urls = (await this.rows('SELECT svc, ext FROM artist_url WHERE artist = ? ORDER BY svc, ext', id))
            .map((r) => [r.svc, r.ext]);
        return [artistReply(hexOfBytes(a.gid), a.name, aliases, rels, urls), a.ngroups];
    }

    async collaboration(mbid) {
        const id = await this.id(mbid);
        if (id == null) return null;
        const n = await this.one('SELECT count(DISTINCT a) AS n FROM artist_rel WHERE b = ? AND kind = 2', id);
        const g = await this.one('SELECT ngroups FROM artist WHERE id = ?', id);
        return { members: n.n || 0, groups: g.ngroups || 0 };
    }

    async groupCount(mbid) {
        const id = await this.id(mbid);
        if (id == null) return null;
        return (await this.one('SELECT ngroups FROM artist WHERE id = ?', id)).ngroups;
    }

    // Every group the artist is credited on, any position (the extras' group
    // lists): [gid, title, date, type, sec, cover, credit], by group id.
    async artistList(mbid) {
        if (!this.lists) return null;
        const id = await this.id(mbid);
        if (id == null) return null;
        return (await this.rows('SELECT g.gid, g.title, g.date, g.type, g.sec, g.cover, c.name AS credit '
                                + 'FROM artist_rg ar JOIN rg g ON g.id = ar.rg LEFT JOIN credit c ON c.id = g.credit '
                                + 'WHERE ar.artist = ? ORDER BY g.id', id))
            .map((r) => [hexOfBytes(r.gid), r.title, r.date, r.type, r.sec, r.cover, r.credit]);
    }

    async byGids(table, cols, mbids) {
        const hs = [...new Set((mbids || []).map(hexOf).filter(Boolean))];
        const out = [];
        for (const part of chunks(hs)) {
            out.push(...await this.rows(`SELECT ${cols} FROM ${table} WHERE gid IN (${part.map(() => '?').join(',')})`,
                                        ...part.map((h) => this.db.blob(bytesOfHex(h)))));
        }
        return out;
    }

    async groups(mbids) {
        const out = {}, byId = new Map();
        for (const r of await this.byGids('rgx', 'id, gid, boot, discogs', mbids)) {
            const x = [r.boot, r.discogs, [], [], []];
            byId.set(r.id, x);
            out[uuidOf(hexOfBytes(r.gid))] = x;
        }
        for (const part of chunks([...byId.keys()].sort((a, b) => a - b))) {
            const ph = part.map(() => '?').join(',');
            for (const r of await this.rows(`SELECT rg, name FROM rg_alias WHERE rg IN (${ph}) ORDER BY rg, name`, ...part)) byId.get(r.rg)[2].push(r.name);
            for (const r of await this.rows(`SELECT rg, title FROM rg_edition WHERE rg IN (${ph}) ORDER BY rg, title`, ...part)) byId.get(r.rg)[3].push(r.title);
            for (const r of await this.rows(`SELECT rg, kind, url FROM rg_url WHERE rg IN (${ph}) ORDER BY rg, kind`, ...part)) byId.get(r.rg)[4].push([r.kind, r.url]);
        }
        for (const k of Object.keys(out)) out[k] = groupExtras(out[k]);
        return out;
    }

    async sameName(key) {
        if (typeof key !== 'string' || !key.length) return null;
        return (await this.rows(
            'SELECT a.gid, a.name, a.ngroups, i.comment, i.type, i.country, '
            + "(SELECT al.name FROM artist_alias al WHERE al.artist = a.id AND al.locale = 'en' AND al.prim = 1 "
            + ' ORDER BY al.name LIMIT 1) AS en '
            + 'FROM artist_name n JOIN artist a ON a.id = n.artist LEFT JOIN artist_info i ON i.artist = a.id '
            + 'WHERE n.key = ? ORDER BY a.ngroups DESC, a.name, a.id', key))
            .map((r) => sameNameRow([hexOfBytes(r.gid), r.name, r.ngroups, r.comment, r.type, r.country, r.en], this.names));
    }

    // The acts an alias or old name names (its key is not the act's own name
    // key), most groups first, as sameName orders.
    async aliasName(key) {
        if (typeof key !== 'string' || !key.length) return null;
        return (await this.rows('SELECT a.gid, a.name, a.ngroups FROM alias_name n JOIN artist a ON a.id = n.artist '
                                + 'WHERE n.key = ? ORDER BY a.ngroups DESC, a.name, a.id', key))
            .map((r) => ({ mbid: uuidOf(hexOfBytes(r.gid)), name: r.name, score: r.ngroups }));
    }

    async releases(mbids) {
        const out = {};
        for (const r of await this.byGids('release', 'gid, rg, type, sec', mbids)) {
            out[uuidOf(hexOfBytes(r.gid))] = releaseRow(hexOfBytes(r.rg), r.type, r.sec, this.names);
        }
        return out;
    }

    async barcode(code) {
        return (await this.rows(`SELECT g.gid FROM barcode b JOIN ${this.rgGid} g ON g.id = b.rg WHERE b.code = ? ORDER BY g.gid`,
                                Number(code))).map((r) => uuidOf(hexOfBytes(r.gid)));
    }

    async svcAlbum(svc, ext) {
        return (await this.rows(`SELECT g.gid FROM svc_album s JOIN ${this.rgGid} g ON g.id = s.rg `
                                + 'WHERE s.svc = ? AND s.ext = ? ORDER BY g.gid', svc, ext))
            .map((r) => uuidOf(hexOfBytes(r.gid)));
    }
}

// The reader of a hosted pack file.
export async function openPack(db) {
    const meta = Object.fromEntries(((await db.prepare('SELECT key, value FROM meta').all()).results)
        .map((r) => [r.key, r.value]));
    if (meta.hosted_layout === 'keyed') return new Keyed(db, meta);
    if (meta.hosted_layout !== 'narrow') throw new Error(`not a hosted pack (layout ${meta.hosted_layout})`);
    const tables = new Set(((await db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all()).results)
        .map((r) => r.name));
    const names = {};
    for (const t of ['artist_type', 'rg_type', 'rg_sec']) {
        names[t] = Object.fromEntries((await db.prepare(`SELECT id, name FROM ${t}`).all()).results
            .map((r) => [String(r.id), r.name]));
    }
    return new Narrow(db, meta, names, tables);
}
