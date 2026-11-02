// THE SPARE-COPY LOAD (LMS-Discography docs/hosted-pack-plan.md §11, end; Simon 2026-11-02: "keep two copies only
// during an update, one is updated the other stays as it was and is kept live until completion and then its switched
// over"). Two D1 databases are bound to the Worker for good, DB1 and DB2.
// - The copy that SERVES is the newest COMPLETE one; a copy is complete once its meta names a dump, and the loader
//   writes the meta LAST. So a reader never sees a copy half loaded, and the switch is the meta's one batch.
// - One scheduled run (tick): when the manifest of the newest whole file (builder/load_parts.py) names a dump newer
//   than the serving copy's, the OTHER copy (DB1 when none serves) is loaded, partsPerRun parts a run, every row an
//   upsert, so a run cut off is simply done again. When every part is in and every table holds the manifest's rows,
//   the meta goes in.
// - The copy that served before is emptied (its meta dropped first, then its tables) EMPTY_AFTER_MS after the switch:
//   a reader keeps its choice of copy for READ_CACHE_MS at most, far less. So is a half-loaded copy whose load was
//   given up (the manifest back at the serving dump). A copy waiting to be emptied that a newer dump is loaded into
//   is not emptied meanwhile: emptying happens only when there is nothing to load, and the switch names the copy
//   that served before it.
// - The loader's own rows live in DB1's table `upd`, which is never dropped: the lease (one run at a time), 'run' (the
//   load in progress: its dump, its copy, the copy serving when it began, the parts done) and 'old' (the copy to
//   empty, and when it stopped serving).
// - A part that does not match its manifest (size, SHA-256) is fetched again next run, nothing written; a part whose
//   records are not the manifest's, or a table whose rows are not, STOPS the load ('run'.error) before the meta.
// Works on D1 and on rebuild/node-d1w.mjs (node:sqlite); Web APIs only.
import { KINDS, LEASE_MS, meter, note, first, sha256Bytes } from './updater.mjs';

export const COPIES = ['DB1', 'DB2'];
export const PARTS_PER_RUN = 2;
export const EMPTY_AFTER_MS = 5 * 60 * 1000;
export const READ_CACHE_MS = 30 * 1000;
export const BATCH_BYTES = 3_000_000;

export async function dumpOf(db) {
    try {
        const r = await first(db, "SELECT value FROM meta WHERE key = 'dump'");
        return r ? r.value : null;
    } catch (e) {
        return null;                                       // no meta table: an empty copy, or one being loaded
    }
}

export async function dumps(env) {
    const out = {};
    for (const name of COPIES) out[name] = await dumpOf(env[name]);
    return out;
}

// The newest complete copy's name, or null. Dump names sort as their dates (YYYYMMDD-HHMMSS).
export function servingOf(d) {
    let best = null;
    for (const name of COPIES) if (d[name] && (!best || d[name] > d[best])) best = name;
    return best;
}

// What a reader asks: the serving copy, kept READ_CACHE_MS in this isolate.
let picked = null;
export async function serving(env, now = Date.now(), ttl = READ_CACHE_MS) {
    if (!picked || now - picked.at >= ttl) picked = { name: servingOf(await dumps(env)), at: now };
    return picked.name;
}

const sameKinds = (k) => Array.isArray(k) && k.length === KINDS.size && k.every((x) => KINDS.has(x));

// One part's records upserted, BATCH_BYTES a D1 batch. Returns the records, or -1 when a record runs past the end.
async function loadPart(db, kinds, bytes, st) {
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const ins = new Map(kinds.map((k) => [k, db.prepare(`INSERT INTO ${k} (k, v) VALUES (?, ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v`)]));
    let stmts = [], size = 0, n = 0;
    const flush = async () => {
        if (stmts.length) for (const r of await db.batch(stmts)) note(st, r);
        stmts = [];
        size = 0;
    };
    for (let o = 0; o < bytes.length;) {
        if (o + 9 > bytes.length) return -1;
        const len = dv.getUint32(o + 5);
        const kind = kinds[bytes[o]];
        if (!kind || o + 9 + len > bytes.length) return -1;
        stmts.push(ins.get(kind).bind(bytes.slice(o + 1, o + 5), bytes.slice(o + 9, o + 9 + len)));
        size += 9 + len;
        n += 1;
        o += 9 + len;
        if (size >= BATCH_BYTES) await flush();
    }
    await flush();
    return n;
}

// One scheduled run. env: DB1, DB2 (D1), MANIFEST_URL (the whole file's manifest; its parts sit beside it).
// opts: fetch, now (epoch ms), partsPerRun, emptyAfter (tests). Returns what it did ({state, ...}).
export async function tick(env, opts = {}) {
    const fetchFn = opts.fetch || ((...a) => fetch(...a));
    const now = opts.now || Date.now;
    const perRun = opts.partsPerRun || Number(env.PARTS_PER_RUN) || PARTS_PER_RUN;
    const emptyAfter = opts.emptyAfter ?? (Number(env.EMPTY_AFTER_MS) || EMPTY_AFTER_MS);
    const ctl = env.DB1, st = meter();
    await ctl.prepare('CREATE TABLE IF NOT EXISTS upd (k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID').run();
    const t = now();
    const lease = await ctl.prepare("INSERT INTO upd (k, v) VALUES ('lease', ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v "
        + 'WHERE CAST(upd.v AS INTEGER) <= ?').bind(String(t + LEASE_MS), t).run();
    if (!(lease.meta && lease.meta.changes)) return { state: 'busy' };
    const get = async (k) => { const r = await first(ctl, 'SELECT v FROM upd WHERE k = ?', k); return r ? JSON.parse(r.v) : null; };
    const put = async (k, v) => note(st, await ctl.prepare('INSERT INTO upd (k, v) VALUES (?, ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v')
        .bind(k, JSON.stringify(v)).run());
    const del = async (k) => note(st, await ctl.prepare('DELETE FROM upd WHERE k = ?').bind(k).run());
    try {
        const d = await dumps(env);
        const live = servingOf(d);
        const mr = await fetchFn(env.MANIFEST_URL);
        if (!mr.ok) return { state: 'no manifest', status: mr.status, serving: live };
        const man = await mr.json();
        if (man.format !== 'load-1' || !sameKinds(man.kinds) || !man.meta.some(([k, v]) => k === 'dump' && v === man.dump))
            return { state: 'bad manifest', serving: live };
        let run = await get('run');
        if (live && d[live] >= man.dump) {
            // Nothing to load. A 'run' row left here is a switch cut off after its meta (the copy that served before is
            // then the one to empty) or a load given up (the manifest went back): its half-loaded copy is emptied.
            if (run) {
                await del('run');
                const name = run.dump === d[live] ? run.from : run.target;
                if (name && name !== live) await put('old', { name, at: t });
            }
            const old = await get('old');
            if (!old || old.name === live) {
                if (old) await del('old');
                return { state: 'current', serving: live, dump: d[live], ...st };
            }
            if (t - old.at < emptyAfter) return { state: 'current', serving: live, dump: d[live], empty_in: old.at + emptyAfter - t, ...st };
            const db = env[old.name];
            for (const tb of ['meta', ...man.kinds]) note(st, await db.prepare(`DROP TABLE IF EXISTS ${tb}`).run());
            await del('old');
            return { state: 'emptied', copy: old.name, serving: live, dump: d[live], ...st };
        }
        // A newer dump: loaded into the copy that does not serve.
        if (!run || run.dump !== man.dump) {
            const target = live === 'DB1' ? 'DB2' : 'DB1';
            const db = env[target];
            for (const tb of ['meta', ...man.kinds]) note(st, await db.prepare(`DROP TABLE IF EXISTS ${tb}`).run());
            note(st, await db.prepare('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID').run());
            for (const k of man.kinds) note(st, await db.prepare(`CREATE TABLE ${k} (k BLOB PRIMARY KEY, v BLOB NOT NULL) WITHOUT ROWID`).run());
            run = { dump: man.dump, target, from: live, part: 0, rows: 0 };
            await put('run', run);
        }
        if (run.error) return { state: 'stopped', error: run.error, part: run.part, serving: live };
        const db = env[run.target];
        const stop = async (error) => {
            run.error = error;
            await put('run', run);
            return { state: 'stopped', error, part: run.part, target: run.target, serving: live, ...st };
        };
        let did = 0;
        while (run.part < man.parts.length && did < perRun) {
            const p = man.parts[run.part];
            const pr = await fetchFn(new URL(p.name, env.MANIFEST_URL).toString());
            const bytes = pr.ok ? new Uint8Array(await pr.arrayBuffer()) : new Uint8Array(0);
            if (!pr.ok || bytes.length !== p.bytes || await sha256Bytes(bytes) !== p.sha256)
                return { state: 'part damaged', part: run.part + 1, status: pr.status, target: run.target, serving: live, ...st };
            const n = await loadPart(db, man.kinds, bytes, st);
            if (n !== p.records) return stop(`part ${run.part + 1}: ${n} records, the manifest says ${p.records}`);
            run.part += 1;
            run.rows += n;
            did += 1;
            await put('run', run);
        }
        if (run.part < man.parts.length)
            return { state: 'parts', part: run.part, of: man.parts.length, rows: run.rows, target: run.target, serving: live, ...st };
        for (const k of man.kinds) {
            const r = await db.prepare(`SELECT count(*) AS n FROM ${k}`).all();
            note(st, r);
            const n = Number(r.results[0].n);
            if (n !== man.tables[k]) return stop(`${k} holds ${n} rows, the manifest says ${man.tables[k]}`);
        }
        const stmts = man.meta.map(([k, v]) => db.prepare('INSERT INTO meta (key, value) VALUES (?, ?) '
            + 'ON CONFLICT (key) DO UPDATE SET value = excluded.value').bind(k, v));
        for (const r of await db.batch(stmts)) note(st, r);
        await del('run');
        if (run.from) await put('old', { name: run.from, at: t });
        return { state: 'switched', copy: run.target, dump: man.dump, from: run.from, rows: run.rows, ...st };
    } finally {
        await ctl.prepare("UPDATE upd SET v = '0' WHERE k = 'lease'").run();
    }
}
