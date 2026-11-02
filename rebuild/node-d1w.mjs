// D1's interface over node:sqlite, READ AND WRITE (worker/node-d1.mjs is read-only), for the updater's suite:
// prepare(sql).bind(...).all() / .run(), batch(statements) in one transaction, meta.changes / rows_read /
// rows_written. opts.failBatch = n: the n-th batch throws before writing (a run cut off), for the tests.
import { DatabaseSync } from 'node:sqlite';

export function nodeD1w(path, opts = {}) {
    const sql = new DatabaseSync(path);
    let batches = 0;
    const bound = (q, args) => ({
        async all() {
            const results = sql.prepare(q).all(...args);
            return { results, meta: { rows_read: results.length, rows_written: 0, changes: 0, duration: 0 } };
        },
        async run() {
            const r = sql.prepare(q).run(...args);
            const n = Number(r.changes);
            return { results: [], meta: { rows_read: 0, rows_written: n, changes: n, duration: 0 } };
        },
    });
    return {
        prepare: (q) => ({ bind: (...a) => bound(q, a), ...bound(q, []) }),
        async batch(stmts) {
            batches += 1;
            if (opts.failBatch && batches === opts.failBatch) throw new Error('cut off (test)');
            sql.exec('BEGIN');
            try {
                const out = [];
                for (const s of stmts) out.push(await s.run());
                sql.exec('COMMIT');
                return out;
            } catch (e) {
                sql.exec('ROLLBACK');
                throw e;
            }
        },
        close: () => sql.close(),
    };
}
