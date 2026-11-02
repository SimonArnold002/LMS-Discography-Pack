// THE SELF-APPLYING UPDATE (LMS-Discography docs/hosted-pack-plan.md §11, end): one scheduled run of the hosted
// pack's Worker. It reads the manifest of the newest entry delta (builder/entry_delta.py parts) and, when the
// database is at the dump the delta goes from, applies the next part: every changed bucket rebuilt in place
// (rebuild/patch.mjs), PER_BATCH at a time, each batch's writes one D1 batch (one transaction).
// - Its place is kept in the table `upd` (row 'run'), so a run cut off part-way is simply done again: a bucket
//   already rebuilt is left alone (patch.mjs's "already").
// - One run at a time: a lease (row 'lease', an epoch-ms deadline), taken by one conditional upsert.
// - When every part is in, the delta's meta line is written last, with the 'run' row deleted, in one batch: the
//   database names the new dump only once all of it is there.
// - A part that does not match its manifest (size, SHA-256, lines) is fetched again next run, nothing written; a
//   bucket that is not the one its line was made from STOPS the update ('run'.error): the database is not the
//   dump it says it is.
// Works on D1 and on rebuild/node-d1w.mjs (node:sqlite); Web APIs only.
import { rebuild, u8 } from './patch.mjs';

export const KINDS = new Set(['art', 'lst', 'grp', 'nam', 'ali', 'rel', 'bar', 'svc']);
export const PER_BATCH = 50;
export const LEASE_MS = 5 * 60 * 1000;

const key = (b) => Uint8Array.of((b >>> 24) & 255, (b >>> 16) & 255, (b >>> 8) & 255, b & 255);
const num = (v) => { const k = u8(v); return ((k[0] << 24) >>> 0) + (k[1] << 16) + (k[2] << 8) + k[3]; };

export const meter = () => ({ queries: 0, rows_read: 0, rows_written: 0, d1_ms: 0 });
export function note(st, r) {
    st.queries += 1;
    st.rows_read += (r.meta && r.meta.rows_read) || 0;
    st.rows_written += (r.meta && r.meta.rows_written) || 0;
    st.d1_ms += (r.meta && r.meta.duration) || 0;
}

// The stored values of buckets bs of one kind, 50 keys a query (D1 binds at most 100 parameters).
export async function read(db, kind, bs, st) {
    const out = new Map();
    for (let i = 0; i < bs.length; i += 50) {
        const part = bs.slice(i, i + 50);
        const r = await db.prepare(`SELECT k, v FROM ${kind} WHERE k IN (${part.map(() => '?').join(',')})`)
            .bind(...part.map(key)).all();
        note(st, r);
        for (const row of r.results) out.set(num(row.k), row.v);
    }
    return out;
}

// Parsed delta lines applied perBatch at a time: their buckets read, rebuilt, written in one batch.
export async function applyLines(db, lines, st, perBatch = PER_BATCH) {
    const res = { n: lines.length, written: 0, deleted: 0, already: 0, bad: [], bytes_in: 0, bytes_out: 0 };
    for (let i = 0; i < lines.length; i += perBatch) {
        const by = new Map();
        for (const x of lines.slice(i, i + perBatch)) {
            if (!KINDS.has(x.t)) throw new Error(`kind ${x.t}`);
            if (!by.has(x.t)) by.set(x.t, []);
            by.get(x.t).push(x);
        }
        const stmts = [];
        for (const [kind, xs] of by) {
            const stored = await read(db, kind, xs.map((x) => x.b), st);
            for (const x of xs) {
                const s = stored.has(x.b) ? stored.get(x.b) : null;
                if (s) res.bytes_in += u8(s).length;
                const r = await rebuild(s, x);
                if (!r.ok) { res.bad.push([x.t, x.b, r.why]); continue; }
                if (r.already) { res.already += 1; continue; }
                if (r.out === null) {
                    stmts.push(db.prepare(`DELETE FROM ${kind} WHERE k = ?`).bind(key(x.b)));
                    res.deleted += 1;
                } else {
                    stmts.push(db.prepare(`INSERT INTO ${kind} (k, v) VALUES (?, ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v`)
                        .bind(key(x.b), r.out));
                    res.written += 1;
                    res.bytes_out += r.out.length;
                }
            }
        }
        if (stmts.length) for (const r of await db.batch(stmts)) note(st, r);
    }
    return res;
}

async function first(db, sql, ...args) {
    const r = await db.prepare(sql).bind(...args).all();
    return (r.results && r.results[0]) || null;
}

async function sha256Bytes(b) {
    const d = new Uint8Array(await crypto.subtle.digest('SHA-256', b));
    let s = '';
    for (const x of d) s += x.toString(16).padStart(2, '0');
    return s;
}

async function gunzip(bytes) {
    return new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
}

// One scheduled run. env: DB (D1), MANIFEST_URL (the delta's manifest; its parts sit beside it).
// opts: fetch, now (epoch ms), perBatch (tests). Returns what it did ({state, ...}).
export async function tick(env, opts = {}) {
    const fetchFn = opts.fetch || ((...a) => fetch(...a));
    const now = opts.now || Date.now;
    const db = env.DB, st = meter();
    let dump;
    try {
        dump = await first(db, "SELECT value FROM meta WHERE key = 'dump'");
    } catch (e) {
        return { state: 'no database' };                  // no meta table yet
    }
    if (!dump) return { state: 'no database' };          // a load not finished (its meta goes in last)
    dump = dump.value;
    await db.prepare('CREATE TABLE IF NOT EXISTS upd (k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID').run();
    const t = now();
    const lease = await db.prepare("INSERT INTO upd (k, v) VALUES ('lease', ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v "
        + 'WHERE CAST(upd.v AS INTEGER) <= ?').bind(String(t + LEASE_MS), t).run();
    if (!(lease.meta && lease.meta.changes)) return { state: 'busy', dump };
    try {
        const mr = await fetchFn(env.MANIFEST_URL);
        if (!mr.ok) return { state: 'no manifest', status: mr.status, dump };
        const man = await mr.json();
        if (dump === man.to) return { state: 'current', dump };
        if (dump !== man.from) return { state: 'no path', dump, from: man.from, to: man.to };
        const row = await first(db, "SELECT v FROM upd WHERE k = 'run'");
        let run = row ? JSON.parse(row.v) : null;
        if (!run || run.to !== man.to || run.from !== man.from) run = { from: man.from, to: man.to, part: 0 };
        if (run.error) return { state: 'stopped', dump, error: run.error, part: run.part };
        const save = async (r) => note(st, await db.prepare("INSERT INTO upd (k, v) VALUES ('run', ?) "
            + 'ON CONFLICT (k) DO UPDATE SET v = excluded.v').bind(JSON.stringify(r)).run());
        if (run.part < man.parts.length) {
            const p = man.parts[run.part];
            const pr = await fetchFn(new URL(p.name, env.MANIFEST_URL).toString());
            const bytes = pr.ok ? new Uint8Array(await pr.arrayBuffer()) : new Uint8Array(0);
            const damaged = { state: 'part damaged', part: run.part + 1, status: pr.status, dump };
            if (!pr.ok || bytes.length !== p.bytes || await sha256Bytes(bytes) !== p.sha256) return damaged;
            const lines = (await gunzip(bytes)).split('\n').filter(Boolean).map((x) => JSON.parse(x));
            if (lines.length !== p.lines) return damaged;
            const out = await applyLines(db, lines, st, opts.perBatch);
            if (out.bad.length) {
                run.error = `refused ${out.bad.length}: ${JSON.stringify(out.bad.slice(0, 5))}`;
                await save(run);
                return { state: 'stopped', part: run.part + 1, error: run.error, dump, ...out, ...st };
            }
            run.part += 1;
            await save(run);
            return { state: 'part', part: run.part, of: man.parts.length, dump, ...out, ...st };
        }
        const stmts = [
            ...man.meta.del.map((k) => db.prepare('DELETE FROM meta WHERE key = ?').bind(k)),
            ...man.meta.put.map(([k, v]) => db.prepare('INSERT INTO meta (key, value) VALUES (?, ?) '
                + 'ON CONFLICT (key) DO UPDATE SET value = excluded.value').bind(k, v)),
            db.prepare("DELETE FROM upd WHERE k = 'run'")];
        for (const r of await db.batch(stmts)) note(st, r);
        return { state: 'done', dump: man.to, from: man.from, ...st };
    } finally {
        await db.prepare("UPDATE upd SET v = '0' WHERE k = 'lease'").run();
    }
}
