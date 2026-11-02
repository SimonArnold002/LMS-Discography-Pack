// Scheduled runs of rebuild/updater.mjs on a keyed file (changed in place), the manifest and its parts read from a
// folder as if fetched from the release. One JSON line per run; stops at a state that would repeat.
//   node rebuild/tick_node.mjs <keyed file> <folder> <manifest name> [<max runs>]
// env PER_BATCH = buckets a write batch (default the updater's); FAIL_BATCH = n: the FIRST run's n-th write batch
// throws (a run cut off part-way); LEASE_HELD = 1: another run holds the lease before the first.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { tick } from './updater.mjs';
import { nodeD1w } from './node-d1w.mjs';

const [file, dir, manifest, max = '100'] = process.argv.slice(2);
const fetchFile = async (url) => {
    const name = decodeURIComponent(new URL(url).pathname.split('/').pop());
    try {
        return new Response(readFileSync(join(dir, name)), { status: 200 });
    } catch {
        return new Response('', { status: 404 });
    }
};
let clock = 1_800_000_000_000;
if (process.env.LEASE_HELD === '1') {
    const s = new DatabaseSync(file);
    s.exec('CREATE TABLE IF NOT EXISTS upd (k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID');
    s.prepare("INSERT INTO upd (k, v) VALUES ('lease', ?)").run(String(clock + 3_600_000));
    s.close();
}
const ENDS = new Set(['current', 'stopped', 'no path', 'busy', 'no database', 'no manifest']);
for (let i = 0; i < Number(max); i++) {
    const db = nodeD1w(file, { failBatch: i === 0 ? Number(process.env.FAIL_BATCH || 0) : 0 });
    let r;
    try {
        r = await tick({ DB: db, MANIFEST_URL: `https://releases.test/download/${manifest}` },
            { fetch: fetchFile, now: () => (clock += 1000), perBatch: process.env.PER_BATCH ? Number(process.env.PER_BATCH) : undefined });
    } catch (e) {
        r = { state: 'threw', error: String((e && e.message) || e) };
    }
    db.close();
    console.log(JSON.stringify(r));
    if (ENDS.has(r.state)) break;
}
