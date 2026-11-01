// D1's interface over Node's own SQLite (node:sqlite, Node 22.5+), for the
// local test and the runner: the reader (worker/reader.mjs) runs unchanged.
//
// Counted per query: rows_read = the rows the query returns. That is a LOWER
// bound of D1's count: D1 counts rows SCANNED. Measured on real D1 (2026-11-01):
// a primary-key lookup reads 1 (a miss too), an IN list of keys 2 per key, an
// index lookup 2, a join of 2 rows 3-4. For the keyed layout (IN lists of
// bucket keys) D1's count is up to twice this one.
import { DatabaseSync } from 'node:sqlite';

export function nodeD1(path) {
    const sql = new DatabaseSync(path, { readOnly: true });
    const stmts = new Map();
    const stats = { queries: 0, rows_read: 0, bytes_read: 0 };
    const bytesOf = (v) => (v instanceof Uint8Array ? v.length : typeof v === 'string' ? Buffer.byteLength(v) : 0);
    return {
        stats,
        reset() { stats.queries = stats.rows_read = stats.bytes_read = 0; },
        blob: (b) => b,
        prepare(q) {
            let st = stmts.get(q);
            if (!st) { st = sql.prepare(q); stmts.set(q, st); }
            const bound = (args) => ({
                async all() {
                    const results = st.all(...args);
                    stats.queries += 1;
                    stats.rows_read += results.length;
                    for (const r of results) for (const v of Object.values(r)) stats.bytes_read += bytesOf(v);
                    return { results, meta: { rows_read: results.length } };
                },
            });
            return { bind: (...args) => bound(args), ...bound([]) };
        },
        close() { sql.close(); },
    };
}
