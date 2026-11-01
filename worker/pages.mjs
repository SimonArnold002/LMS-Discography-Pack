// What one cold artist page reads from a hosted pack: the lookups Discography
// makes with today's pack for a first visit, against one file, counted the way
// D1 bills them (worker/node-d1.mjs: exact for the keyed layout, a lower bound
// for the narrow one). builder/hosted.py `pages` writes the list:
//   [{ "label", "mbid", "key", "groups": [group gids the first list holds] }]
// A page asks: the artist read; the same-name list for its name (the search
// that led to it); every collaboration's vetting; the group count; the extras
// of every group on it (its list comes from ListenBrainz and the community
// API, not the pack).
//
// Usage: node worker/pages.mjs <hosted.db> <pages.json>   -> JSON on stdout
import { readFileSync } from 'node:fs';
import { nodeD1 } from './node-d1.mjs';
import { openPack } from './reader.mjs';

const [db, pagesPath] = process.argv.slice(2);
if (!pagesPath) { console.error('usage: pages.mjs <hosted.db> <pages.json>'); process.exit(2); }
const d1 = nodeD1(db);
const pack = await openPack(d1);
const out = [];
for (const p of JSON.parse(readFileSync(pagesPath, 'utf8'))) {
    d1.reset();
    const t0 = process.hrtime.bigint();
    const read = await pack.artistRead(p.mbid);
    await pack.sameName(p.key);
    const collabs = read.length ? read[0].relations.filter((r) => r.type === 'collaboration').map((r) => r.artist.id) : [];
    for (const c of collabs) await pack.collaboration(c);
    await pack.groupCount(p.mbid);
    await pack.groups(p.groups);
    out.push({ label: p.label, groups: p.groups.length, collaborations: collabs.length,
               ms: Number(process.hrtime.bigint() - t0) / 1e6, ...d1.stats });
}
d1.close();
console.log(JSON.stringify({ db, layout: pack.layout, bits: pack.bits ?? null, pages: out }));
