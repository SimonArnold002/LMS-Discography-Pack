// An entry delta (builder/entry_delta.py) applied to a COPY of a keyed file with rebuild/patch.mjs, as
// the Worker applies it to D1, then every bucket of the result hashed into a list to hold against the
// run's bhash list. Node 22.5+ (node:sqlite).
//   node rebuild/apply_node.mjs <keyed file copy, changed in place> <edelta .jsonl.gz> <out hashes .tsv>
// Prints the counts as JSON; exits 1 when a line does not apply.
import { DatabaseSync } from 'node:sqlite';
import { createReadStream, writeFileSync } from 'node:fs';
import { createGunzip } from 'node:zlib';
import { createInterface } from 'node:readline';
import { rebuild, entriesOf, hashOf } from './patch.mjs';

const KINDS = ['art', 'lst', 'grp', 'nam', 'ali', 'rel', 'bar', 'svc'];
const [file, delta, out] = process.argv.slice(2);
const db = new DatabaseSync(file);
const key = (b) => Uint8Array.of((b >>> 24) & 255, (b >>> 16) & 255, (b >>> 8) & 255, b & 255);
const n = { lines: 0, written: 0, deleted: 0, already: 0, bad: [], bytes_in: 0, bytes_out: 0, zb: 0 };
// Node's own CPU for the rebuild (reads and writes of node:sqlite included, which on Cloudflare are D1's,
// not the Worker's): a preview of the Worker's, never its measurement.
const c0 = process.cpuUsage(), t0 = Date.now();
db.exec('BEGIN');
const rl = createInterface({ input: createReadStream(delta).pipe(createGunzip()), crlfDelay: Infinity });
for await (const text of rl) {
    if (!text) continue;
    const x = JSON.parse(text);
    if (x.t === 'meta') {
        for (const k of x.del) db.prepare('DELETE FROM meta WHERE key = ?').run(k);
        for (const [k, v] of x.put) db.prepare('INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value').run(k, v);
        continue;
    }
    if (!KINDS.includes(x.t)) throw new Error(`kind ${x.t}`);
    n.lines++;
    n.zb += x.zb || 0;
    const row = db.prepare(`SELECT v FROM ${x.t} WHERE k = ?`).get(key(x.b));
    const r = await rebuild(row ? row.v : null, x);
    n.bytes_in += row ? row.v.length : 0;
    if (!r.ok) { n.bad.push([x.t, x.b, r.why]); continue; }
    if (r.already) { n.already++; continue; }
    if (r.out === null) {
        db.prepare(`DELETE FROM ${x.t} WHERE k = ?`).run(key(x.b));
        n.deleted++;
    } else {
        db.prepare(`INSERT INTO ${x.t} (k, v) VALUES (?, ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v`).run(key(x.b), r.out);
        n.written++;
        n.bytes_out += r.out.length;
    }
}
db.exec('COMMIT');
const c1 = process.cpuUsage(c0);
n.cpu_ms = Math.round((c1.user + c1.system) / 1000);
n.wall_ms = Date.now() - t0;
// Every bucket of the result, as entry_delta.keyed_hashes lists them (the meta line is Python's: skipped).
const lines = [];
for (const kind of KINDS) {
    for (const r of db.prepare(`SELECT k, v FROM ${kind} ORDER BY k`).iterate()) {
        const b = ((r.k[0] << 24) >>> 0) + (r.k[1] << 16) + (r.k[2] << 8) + r.k[3];
        lines.push(`${kind}\t${b}\t${await hashOf(await entriesOf(r.v))}`);
    }
}
writeFileSync(out, lines.join('\n') + '\n');
db.close();
console.log(JSON.stringify(n));
process.exit(n.bad.length ? 1 : 0);
