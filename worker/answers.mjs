// Answer a list of lookups from a hosted pack file, one JSON line
// per query, in order: {"r": answer}. builder/hosted.py `queries` writes the
// list; `compare` holds these answers against Pack.pm's on today's pack.
//
// Usage: node worker/answers.mjs <hosted.db> <queries.jsonl> <answers.jsonl>
import { createReadStream, createWriteStream } from 'node:fs';
import { createInterface } from 'node:readline';
import { nodeD1 } from './node-d1.mjs';
import { openPack } from './reader.mjs';

const [db, queries, out] = process.argv.slice(2);
if (!out) { console.error('usage: answers.mjs <hosted.db> <queries.jsonl> <answers.jsonl>'); process.exit(2); }

const d1 = nodeD1(db);
const pack = await openPack(d1);
const w = createWriteStream(out);
let n = 0;
const t0 = Date.now();
for await (const line of createInterface({ input: createReadStream(queries), crlfDelay: Infinity })) {
    if (!line) continue;
    const { f, a } = JSON.parse(line);
    if (typeof pack[f] !== 'function') throw new Error(`no lookup ${f}`);
    const r = await pack[f](...a);
    if (!w.write(JSON.stringify({ r: r === undefined ? null : r }) + '\n')) await new Promise((ok) => w.once('drain', ok));
    n += 1;
}
await new Promise((ok) => w.end(ok));
d1.close();
console.log(JSON.stringify({ db, layout: pack.layout, queries: n, ms: Date.now() - t0, ...d1.stats }));
