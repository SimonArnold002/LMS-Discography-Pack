// What ONE request per artist page would cost (LMS-Discography
// docs/hosted-pack-plan.md §12: the Worker answers a moment, not a lookup).
// For each named artist the Worker's whole answer is built from one hosted file,
// as the Worker would build it, and measured: D1 queries, rows and bytes read
// (worker/node-d1.mjs: rows returned, a lower bound of D1's), the answer's size
// as JSON and gzipped, and the time to build it here (a local file, so no D1
// round trips: the query count is what adds them).
//
// The answer holds what a page asks the pack today (pages.mjs: the artist read,
// the same-name list for its name, every collaboration's vetting) plus the
// extras of EVERY group on the file's own list for the artist, in one lookup
// (artistGroups: any credit position, so the first list's groups are covered
// whatever ListenBrainz and the community API return; a group newer than the
// dump has no extras anywhere). The release page reads its links from it, with
// no request. Each answer's extras are checked against groups(<his list>), the
// long way, outside the measured reads (`same_as_groups`).
// Three variants, each measured on its own:
//   page    the above;
//   owned   + releases() for up to 100 of the artist's release ids (what a
//           library owner's tags would send with the request);
//   fold    + each led band's read and its groups' extras; "led" here is the
//           forward member-of-band links marked eponymous (fold_e, exact for
//           those) and every forward member-of-band link (fold_all, an upper
//           bound: Discography also folds a band NAMED after him, which needs
//           its _norm, so the real fold lies between the two).
// Each answer is also written to <outdir> for verify/decode_answers.pl (the time
// LMS takes to decode it).
//
// Usage: node worker/moments.mjs <hosted.db> <pages.json> <outdir>   -> JSON on stdout
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { join } from 'node:path';
import { nodeD1 } from './node-d1.mjs';
import { openPack } from './reader.mjs';

const OWNED_MAX = 100;

// The page's whole answer, and how many groups the file lists for the artist.
// `bands`: 'none', 'eponymous' or 'all'.
export async function pageAnswer(pack, p, { owned = [], bands = 'none' } = {}) {
    const read = await pack.artistRead(p.mbid);
    const a = { artist: read, sameName: await pack.sameName(p.key), collaborations: {}, groups: {} };
    const rels = read.length ? read[0].relations.filter((r) => r['target-type'] === 'artist') : [];
    for (const r of rels.filter((r) => r.type === 'collaboration')) {
        a.collaborations[r.artist.id] = await pack.collaboration(r.artist.id);
    }
    // His groups' extras in one lookup (artistGroups: joins, or the keyed list's
    // own entries), the same answer as groups(<his list>).
    const groupsOf = async (mbid) => (await pack.artistGroups(mbid)) || {};
    a.groups = await groupsOf(p.mbid);
    if (owned.length) a.releases = await pack.releases(owned);
    if (bands !== 'none') {
        a.bands = {};
        for (const r of rels) {
            if (r.type !== 'member of band' || r.direction !== 'forward') continue;
            if (bands === 'eponymous' && !r.attributes.includes('eponymous')) continue;
            if (a.bands[r.artist.id] || r.artist.id === read[0].id) continue;
            a.bands[r.artist.id] = { artist: await pack.artistRead(r.artist.id), groups: await groupsOf(r.artist.id) };
        }
    }
    return a;
}

// The same extras asked the long way, groups(<the artist's list>), outside the
// measured reads: `same_as_groups` in the result says the one lookup gave them.
async function longWay(pack, mbid) {
    return pack.groups(((await pack.artistList(mbid)) || []).map((g) => g[0]));
}

function canon(x) {
    if (Array.isArray(x)) return x.map(canon);
    if (x && typeof x === 'object') return Object.fromEntries(Object.keys(x).sort().map((k) => [k, canon(x[k])]));
    return x;
}

async function main() {
    const [db, pagesPath, outdir] = process.argv.slice(2);
    if (!outdir) { console.error('usage: moments.mjs <hosted.db> <pages.json> <outdir>'); process.exit(2); }
    mkdirSync(outdir, { recursive: true });
    const d1 = nodeD1(db);
    const pack = await openPack(d1);
    const out = [];
    const pages = JSON.parse(readFileSync(pagesPath, 'utf8'));
    for (const [i, p] of pages.entries()) {
        const owned = (p.releases || []).slice(0, OWNED_MAX);
        const row = { label: p.label, owned_ids: owned.length };
        for (const [v, opt] of [['page', {}], ['owned', { owned }], ['fold_e', { bands: 'eponymous' }],
                                ['fold_all', { bands: 'all' }]]) {
            d1.reset();
            const t0 = process.hrtime.bigint();
            const a = await pageAnswer(pack, p, opt);
            const ms = Number(process.hrtime.bigint() - t0) / 1e6;
            const stats = { ...d1.stats };
            const listed = ((await pack.artistList(p.mbid)) || []).length;
            let same = JSON.stringify(canon(a.groups)) === JSON.stringify(canon(await longWay(pack, p.mbid)));
            for (const [b, x] of Object.entries(a.bands || {})) {
                same = same && JSON.stringify(canon(x.groups)) === JSON.stringify(canon(await longWay(pack, b)));
            }
            const json = Buffer.from(JSON.stringify(a), 'utf8');
            const file = join(outdir, `${i}-${v}.json`);
            writeFileSync(file, json);
            row[v] = { ms, ...stats, same_as_groups: same, bytes: json.length, gz: gzipSync(json, { level: 6 }).length,
                       listed, extras: Object.keys(a.groups).length, bands: a.bands ? Object.keys(a.bands).length : 0,
                       file };
        }
        out.push(row);
    }
    d1.close();
    console.log(JSON.stringify({ db, layout: pack.layout, bits: pack.bits ?? null, pages: out }));
}

if (import.meta.url === `file://${process.argv[1]}`) await main();
