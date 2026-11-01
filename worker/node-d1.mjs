// D1's interface over Node's own SQLite (node:sqlite, Node 22.5+), for the
// local test and the runner: the reader (worker/reader.mjs) runs unchanged.
//
// Counted per query: rows_read = the rows the query returns. That is a LOWER
// bound of D1's count: D1 counts rows SCANNED, and a join or an index probe can
// scan more than it returns (the real count is the D1 check's job).
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
