"""PROTOTYPE: rewrite a pack in small ordered steps, as a device would after a
delta, into the proposed format 4, and say whether it comes out the size of a
fresh pack.

Why: a delta applied in place splits B-tree pages, so a device's pack grows
past a fresh one (31 MB from one 0.57 MB delta, measured 2026-10-23); VACUUM
would compact it but runs in ONE go and would freeze LMS. Copying each table
in its key order into a new file, a few thousand rows per step, fills pages
the way VACUUM does, and a device can yield between steps. That only works
when nothing has to be built in one go, so format 4 has NO secondary index:
  * artist_gid index -> table artist_gk, rgx keyed by (gk, id) (gk = the first
    8 bytes of the gid, BLOB, with the id; the caller checks the full gid),
  * artist_rel_b index -> table artist_rel_b (b, a, kind),
  * artist_info folded into artist (comment, type, country).
Every table is keyed by its PRIMARY KEY and copied in that order.

Usage: python3 -I compact_proto.py <format-3 pack.db> <out.db> [rows per step]
Prints the size, and the time per step (median, 99th percentile, worst) and
rows per second. sqlite_stat1 is not copied (format 4 runs no ANALYZE).

       python3 -I compact_proto.py --whole <format-4 pack.db> <out.db>
Copies each table of a format-4 pack in ONE statement (INSERT INTO t SELECT *
FROM s.t: SQLite's transfer path, the one VACUUM uses, which packs pages
full; measured 2026-10-23 that a copy in steps, any way it is written, leaves
WITHOUT ROWID tables ~10 points less full). A device would yield BETWEEN
tables, so the worst table's time is how long LMS would wait.
"""
import os, sqlite3, sys, time

SCHEMA4 = '''
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE artist (id INTEGER PRIMARY KEY, gid BLOB NOT NULL, name TEXT NOT NULL, ngroups INTEGER NOT NULL,
                     comment TEXT, type INTEGER, country TEXT);
CREATE TABLE artist_gk (gk BLOB NOT NULL, id INTEGER NOT NULL, PRIMARY KEY (gk, id)) WITHOUT ROWID;
CREATE TABLE artist_alias (artist INTEGER NOT NULL, name TEXT NOT NULL, locale TEXT NOT NULL, prim INTEGER NOT NULL,
                           PRIMARY KEY (artist, name, locale)) WITHOUT ROWID;
CREATE TABLE artist_url (artist INTEGER NOT NULL, svc INTEGER NOT NULL, ext INTEGER NOT NULL,
                         PRIMARY KEY (artist, svc, ext)) WITHOUT ROWID;
CREATE TABLE artist_rel (a INTEGER NOT NULL, b INTEGER NOT NULL, kind INTEGER NOT NULL,
                         epon INTEGER NOT NULL, skey BLOB NOT NULL,
                         PRIMARY KEY (a, b, kind)) WITHOUT ROWID;
CREATE TABLE artist_rel_b (b INTEGER NOT NULL, a INTEGER NOT NULL, kind INTEGER NOT NULL,
                           PRIMARY KEY (b, a, kind)) WITHOUT ROWID;
CREATE TABLE artist_redirect (gid BLOB PRIMARY KEY, artist INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE rgx (gk BLOB NOT NULL, id INTEGER NOT NULL, gid BLOB NOT NULL, boot INTEGER NOT NULL, discogs INTEGER,
                  PRIMARY KEY (gk, id)) WITHOUT ROWID;
CREATE TABLE rg_alias (rg INTEGER NOT NULL, name TEXT NOT NULL, PRIMARY KEY (rg, name)) WITHOUT ROWID;
CREATE TABLE rg_edition (rg INTEGER NOT NULL, title TEXT NOT NULL, PRIMARY KEY (rg, title)) WITHOUT ROWID;
CREATE TABLE rg_url (rg INTEGER NOT NULL, kind INTEGER NOT NULL, url TEXT NOT NULL,
                     PRIMARY KEY (rg, kind)) WITHOUT ROWID;
CREATE TABLE artist_name (key TEXT NOT NULL, artist INTEGER NOT NULL, PRIMARY KEY (key, artist)) WITHOUT ROWID;
CREATE TABLE artist_type (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE release (gid BLOB PRIMARY KEY, rg BLOB NOT NULL, type INTEGER, sec INTEGER) WITHOUT ROWID;
CREATE TABLE rg_type (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE rg_sec (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE namekey_probe (name TEXT PRIMARY KEY, key TEXT NOT NULL) WITHOUT ROWID;
'''

# (destination table, columns written, SELECT from the format-3 pack `s`
# returning those columns plus the order key last, order key columns). Each
# step reads the next N rows after the last key, in key order, through an
# index the source already has, so a step never sorts the table.
PLAN = [
    ('meta', 'key, value', 'SELECT key, value, key FROM s.meta', ['key']),
    ('artist', 'id, gid, name, ngroups, comment, type, country',
     'SELECT a.id, a.gid, a.name, a.ngroups, i.comment, i.type, i.country, a.id '
     'FROM s.artist a LEFT JOIN s.artist_info i ON i.artist = a.id', ['a.id']),
    ('artist_gk', 'gk, id', 'SELECT substr(gid, 1, 8), id, gid FROM s.artist INDEXED BY artist_gid', ['gid']),
    ('artist_alias', 'artist, name, locale, prim', 'SELECT artist, name, locale, prim, artist, name, locale FROM s.artist_alias',
     ['artist', 'name', 'locale']),
    ('artist_url', 'artist, svc, ext', 'SELECT artist, svc, ext, artist, svc, ext FROM s.artist_url', ['artist', 'svc', 'ext']),
    ('artist_rel', 'a, b, kind, epon, skey', 'SELECT a, b, kind, epon, skey, a, b, kind FROM s.artist_rel', ['a', 'b', 'kind']),
    ('artist_rel_b', 'b, a, kind', 'SELECT b, a, kind, b, a, kind FROM s.artist_rel INDEXED BY artist_rel_b', ['b', 'a', 'kind']),
    ('artist_redirect', 'gid, artist', 'SELECT gid, artist, gid FROM s.artist_redirect', ['gid']),
    ('rgx', 'gk, id, gid, boot, discogs',
     'SELECT substr(gid, 1, 8), id, gid, boot, discogs, gid FROM s.rgx INDEXED BY rgx_gid', ['gid']),
    ('rg_alias', 'rg, name', 'SELECT rg, name, rg, name FROM s.rg_alias', ['rg', 'name']),
    ('rg_edition', 'rg, title', 'SELECT rg, title, rg, title FROM s.rg_edition', ['rg', 'title']),
    ('rg_url', 'rg, kind, url', 'SELECT rg, kind, url, rg, kind FROM s.rg_url', ['rg', 'kind']),
    ('artist_name', 'key, artist', 'SELECT key, artist, key, artist FROM s.artist_name', ['key', 'artist']),
    ('artist_type', 'id, name', 'SELECT id, name, id FROM s.artist_type', ['id']),
    ('release', 'gid, rg, type, sec', 'SELECT gid, rg, type, sec, gid FROM s.release', ['gid']),
    ('rg_type', 'id, name', 'SELECT id, name, id FROM s.rg_type', ['id']),
    ('rg_sec', 'id, name', 'SELECT id, name, id FROM s.rg_sec', ['id']),
    ('namekey_probe', 'name, key', 'SELECT name, key, name FROM s.namekey_probe', ['name']),
]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0.0


def run(src, out, step=5000):
    if os.path.exists(out):
        os.remove(out)
    c = sqlite3.connect(out, isolation_level=None)
    c.executescript('PRAGMA page_size=4096; PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;' + SCHEMA4)
    c.execute('ATTACH DATABASE ? AS s', (src,))
    times, total_rows, t_all = [], 0, time.time()
    for dest, cols, sel, keys in PLAN:
        ncol = len(cols.split(','))
        last = None
        n_t, t_t = 0, time.time()
        while True:
            if last is None:
                where, args = '', ()
            elif len(keys) == 1:
                where, args = f' WHERE {keys[0]} > ?', (last[0],)
            else:
                where, args = f' WHERE ({", ".join(keys)}) > ({", ".join("?" * len(keys))})', tuple(last)
            q = f'{sel}{where} ORDER BY {", ".join(keys)} LIMIT {step}'
            t0 = time.time()
            rows = c.execute(q, args).fetchall()
            if not rows:
                break
            c.execute('BEGIN')
            c.executemany(f'INSERT INTO main.{dest} ({cols}) VALUES ({", ".join("?" * ncol)})',
                          [r[:ncol] for r in rows])
            c.execute('COMMIT')
            times.append(time.time() - t0)
            last = rows[-1][ncol:]
            n_t += len(rows)
        total_rows += n_t
        print(f'  {dest:16} {n_t:>10,} rows  {time.time() - t_t:6.1f} s')
    c.execute("UPDATE main.meta SET value = '4' WHERE key = 'format'")
    c.execute('DETACH DATABASE s')
    c.close()
    size = os.path.getsize(out)
    el = time.time() - t_all
    print(f'{out}: {size:,} bytes; {total_rows:,} rows in {len(times):,} steps of {step}, {el:.1f} s '
          f'({total_rows / el:,.0f} rows/s); step median {1000 * pct(times, .5):.0f} ms, '
          f'p99 {1000 * pct(times, .99):.0f} ms, worst {1000 * max(times or [0]):.0f} ms')
    return size


def whole(src, out):
    if os.path.exists(out):
        os.remove(out)
    c = sqlite3.connect(out, isolation_level=None)
    c.executescript('PRAGMA page_size=4096; PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;' + SCHEMA4)
    c.execute('ATTACH DATABASE ? AS s', (src,))
    worst, t_all = (0.0, ''), time.time()
    for dest, *_ in PLAN:
        t0 = time.time()
        c.execute('BEGIN')
        c.execute(f'INSERT INTO main.{dest} SELECT * FROM s.{dest}')
        c.execute('COMMIT')
        el = time.time() - t0
        n = c.execute(f'SELECT count(*) FROM main.{dest}').fetchone()[0]
        print(f'  {dest:16} {n:>10,} rows in one go  {1000 * el:7.0f} ms')
        worst = max(worst, (el, dest))
    c.execute('DETACH DATABASE s')
    c.close()
    size = os.path.getsize(out)
    print(f'{out}: {size:,} bytes in {time.time() - t_all:.1f} s; the longest table: {worst[1]} {1000 * worst[0]:.0f} ms')
    return size


def vacuumed(path):
    """The same file VACUUMed (the builder's compaction), for comparison."""
    v = path + '.vac'
    c = sqlite3.connect(path)
    c.execute('VACUUM INTO ?', (v,))
    c.close()
    s = os.path.getsize(v)
    os.remove(v)
    return s


if __name__ == '__main__':
    if sys.argv[1:2] == ['--whole'] and len(sys.argv) == 4:
        size = whole(sys.argv[2], sys.argv[3])
        v = vacuumed(sys.argv[3])
        print(f'the same rows VACUUMed: {v:,} bytes ({size - v:+,} for the table-by-table copy)')
        sys.exit(0)
    if len(sys.argv) not in (3, 4):
        raise SystemExit(__doc__)
    size = run(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) == 4 else 5000)
    v = vacuumed(sys.argv[2])
    print(f'the same rows VACUUMed: {v:,} bytes ({size - v:+,} for the stepped copy)')
