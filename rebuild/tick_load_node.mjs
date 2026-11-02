// Scheduled runs of rebuild/loader.mjs on two database files (DB1, DB2), the manifest and its parts read from a folder
// as if fetched from the release. One JSON line per run, with the copy a reader would take after it (`reads`, no
// cache). The clock steps STEP_MS (default 60,000) a run. Stops at a state that would repeat.
//   node rebuild/tick_load_node.mjs <DB1 file> <DB2 file> <folder> <manifest name> [<max runs>]
// env PARTS_PER_RUN; EMPTY_AFTER_MS; FAIL_BATCH = n: the FIRST run's n-th write batch to FAIL_DB (default DB1)
// throws (a run cut off part-way); LEASE_HELD = 1: another run holds the lease before the first.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { tick, dumps, servingOf } from './loader.mjs';
import { nodeD1w } from './node-d1w.mjs';

const [f1, f2, dir, manifest, max = '100'] = process.argv.slice(2);
const fetchFile = async (url) => {
    const name = decodeURIComponent(new URL(url).pathname.split('/').pop());
    try {
        return new Response(readFileSync(join(dir, name)), { status: 200 });
    } catch {
        return new Response('', { status: 404 });
    }
};
const step = Number(process.env.STEP_MS || 60_000);
let clock = 1_800_000_000_000;
if (process.env.LEASE_HELD === '1') {
    const s = new DatabaseSync(f1);
    s.exec('CREATE TABLE IF NOT EXISTS upd (k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID');
    s.prepare("INSERT INTO upd (k, v) VALUES ('lease', ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v").run(String(clock + 3_600_000));
    s.close();
}
const ENDS = new Set(['stopped', 'busy', 'no manifest', 'bad manifest']);
for (let i = 0; i < Number(max); i++) {
    const fail = (name) => ({ failBatch: i === 0 && (process.env.FAIL_DB || 'DB1') === name ? Number(process.env.FAIL_BATCH || 0) : 0 });
    const env = { DB1: nodeD1w(f1, fail('DB1')), DB2: nodeD1w(f2, fail('DB2')), MANIFEST_URL: `https://releases.test/download/${manifest}` };
    let r;
    try {
        r = await tick(env, {
            fetch: fetchFile, now: () => (clock += step),
            partsPerRun: process.env.PARTS_PER_RUN ? Number(process.env.PARTS_PER_RUN) : undefined,
            emptyAfter: process.env.EMPTY_AFTER_MS ? Number(process.env.EMPTY_AFTER_MS) : undefined,
        });
    } catch (e) {
        r = { state: 'threw', error: String((e && e.message) || e) };
    }
    r.reads = servingOf(await dumps(env));
    env.DB1.close();
    env.DB2.close();
    console.log(JSON.stringify(r));
    if (ENDS.has(r.state) || (r.state === 'current' && r.empty_in === undefined)) break;
}
