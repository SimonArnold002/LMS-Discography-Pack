"""The delta between two packs of one format, and its proof.

A delta is a small SQLite file:
  put_<table>   every row of the NEW pack that the old one lacks or holds
                differently, whole (same columns, same order)
  del_<table>   the primary key of every row the old pack holds and the new
                one does not
  delta_meta    format, from (the old pack's meta.dump), to (the new one's)
for every table of the pack, `meta` included (so the version moves with the
rows). A table with nothing to change still gets its two (empty) tables, so the
device sees one fixed shape.

The device applies it in ONE transaction: per table, the deletes, then the puts
(INSERT OR REPLACE), each in batches of rowid ranges, all through its own SQL
(the file supplies rows, never statements). `apply()` below is that same
sequence; `verify()` applies the delta to a copy of the old pack and checks
every table equals the new pack row for row, so a delta is published only once
old + delta == new has been shown.

Usage: python3 -I make_delta.py <old.db> <new.db> <delta.db>
Exit 3 when the two packs do not have the same tables and columns (a format
change: no delta, the device downloads the whole pack).
"""
import os, shutil, sqlite3, sys, tempfile

BATCH = 500


def tables(c, schema='main'):
    """{table: (columns, pk columns)} for every table, in name order."""
    out = {}
    for (t,) in c.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' "
                          f"AND name NOT LIKE 'sqlite%' ORDER BY name"):
        info = c.execute(f'PRAGMA {schema}.table_info("{t}")').fetchall()
        cols = [r[1] for r in info]
        pk = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
        types = {r[1]: r[2] for r in info}
        out[t] = (cols, pk, types)
    return out


def q(name):
    return '"' + name.replace('"', '""') + '"'


def meta(c, schema, key):
    r = c.execute(f'SELECT value FROM {schema}.meta WHERE key = ?', (key,)).fetchone()
    return r[0] if r else None


class FormatChange(Exception):
    pass


def make(old, new, out):
    if os.path.exists(out):
        os.remove(out)
    c = sqlite3.connect(out)
    c.execute('ATTACH DATABASE ? AS o', (old,))
    c.execute('ATTACH DATABASE ? AS n', (new,))
    to, tn = tables(c, 'o'), tables(c, 'n')
    if {t: v[:2] for t, v in to.items()} != {t: v[:2] for t, v in tn.items()}:
        raise FormatChange('the packs differ in tables or columns')
    fo, fn = meta(c, 'o', 'format'), meta(c, 'n', 'format')
    frm, to_dump = meta(c, 'o', 'dump'), meta(c, 'n', 'dump')
    if fo != fn:
        raise FormatChange(f'format {fo} -> {fn}')
    if not frm or not to_dump or frm == to_dump:
        raise SystemExit(f'need two different meta.dump values (old {frm!r}, new {to_dump!r})')
    c.execute('BEGIN')
    counts = {}
    for t, (cols, pk, types) in tn.items():
        if not pk:
            raise SystemExit(f'{t}: no primary key')
        cl = ', '.join(q(x) for x in cols)
        pl = ', '.join(q(x) for x in pk)
        c.execute(f'CREATE TABLE main.{q("put_" + t)} ({", ".join(q(x) + " " + types[x] for x in cols)})')
        c.execute(f'CREATE TABLE main.{q("del_" + t)} ({", ".join(q(x) + " " + types[x] for x in pk)})')
        c.execute(f'INSERT INTO main.{q("put_" + t)} ({cl}) '
                  f'SELECT {cl} FROM n.{q(t)} EXCEPT SELECT {cl} FROM o.{q(t)} ORDER BY {pl}')
        c.execute(f'INSERT INTO main.{q("del_" + t)} ({pl}) '
                  f'SELECT {pl} FROM o.{q(t)} EXCEPT SELECT {pl} FROM n.{q(t)} ORDER BY {pl}')
        counts[t] = (c.execute(f'SELECT count(*) FROM main.{q("put_" + t)}').fetchone()[0],
                     c.execute(f'SELECT count(*) FROM main.{q("del_" + t)}').fetchone()[0])
    c.execute('CREATE TABLE main.delta_meta (key TEXT NOT NULL, value TEXT NOT NULL)')   # no key: no index
    c.executemany('INSERT INTO main.delta_meta VALUES (?,?)',
                  [('format', fn), ('from', frm), ('to', to_dump)])
    c.execute('COMMIT')
    c.execute('DETACH DATABASE o')
    c.execute('DETACH DATABASE n')
    c.execute('VACUUM')
    c.close()
    return frm, to_dump, counts


def apply(pack, delta, batch=BATCH):
    """The device's sequence: one transaction; per table the deletes then the
    puts, BATCH rows of the delta's rowids at a time; the version checked
    before and after. Raises on any mismatch (the transaction rolls back)."""
    c = sqlite3.connect(pack, isolation_level=None)
    try:
        c.execute('ATTACH DATABASE ? AS d', (delta,))
        dm = dict(c.execute('SELECT key, value FROM d.delta_meta'))
        if meta(c, 'main', 'dump') != dm['from']:
            raise ValueError(f"pack is {meta(c, 'main', 'dump')!r}, delta is from {dm['from']!r}")
        mine = tables(c, 'main')
        c.execute('BEGIN IMMEDIATE')
        for t, (cols, pk, _) in mine.items():
            cl = ', '.join(q(x) for x in cols)
            pl = ', '.join(q(x) for x in pk)
            for kind in ('del', 'put'):
                top = c.execute(f'SELECT max(rowid) FROM d.{q(kind + "_" + t)}').fetchone()[0] or 0
                lo = 0
                while lo < top:
                    hi = lo + batch
                    if kind == 'del':
                        lhs = q(pk[0]) if len(pk) == 1 else f'({pl})'
                        c.execute(f'DELETE FROM main.{q(t)} WHERE {lhs} IN '
                                  f'(SELECT {pl} FROM d.{q("del_" + t)} WHERE rowid > ? AND rowid <= ?)', (lo, hi))
                    else:
                        c.execute(f'INSERT OR REPLACE INTO main.{q(t)} ({cl}) '
                                  f'SELECT {cl} FROM d.{q("put_" + t)} WHERE rowid > ? AND rowid <= ?', (lo, hi))
                    lo = hi
        if meta(c, 'main', 'dump') != dm['to']:
            raise ValueError(f"after the delta the pack is {meta(c, 'main', 'dump')!r}, expected {dm['to']!r}")
        c.execute('COMMIT')
    except Exception:
        if c.in_transaction:
            c.execute('ROLLBACK')
        raise
    finally:
        c.close()


def same(a, b):
    """Every table of a equals b row for row (and they have the same tables)."""
    c = sqlite3.connect(':memory:')
    c.execute('ATTACH DATABASE ? AS a', (a,))
    c.execute('ATTACH DATABASE ? AS b', (b,))
    ta, tb = tables(c, 'a'), tables(c, 'b')
    if set(ta) != set(tb):
        return f'tables differ: {sorted(set(ta) ^ set(tb))}'
    for t, (cols, _, _) in ta.items():
        cl = ', '.join(q(x) for x in cols)
        for x, y in (('a', 'b'), ('b', 'a')):
            n = c.execute(f'SELECT count(*) FROM (SELECT {cl} FROM {x}.{q(t)} EXCEPT SELECT {cl} FROM {y}.{q(t)})').fetchone()[0]
            if n:
                return f'{t}: {n} row(s) in {x} not in {y}'
    c.close()
    return ''


def verify(old, new, delta):
    """Apply the delta to a copy of old and compare with new. '' = proven."""
    with tempfile.TemporaryDirectory() as d:
        copy = os.path.join(d, 'pack.db')
        shutil.copyfile(old, copy)
        apply(copy, delta)
        return same(copy, new)


def main():
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    old, new, out = sys.argv[1:]
    try:
        frm, to_dump, counts = make(old, new, out)
    except FormatChange as e:
        print(f'no delta: {e}')
        sys.exit(3)
    for t, (p, d) in counts.items():
        if p or d:
            print(f'  {t:16} put {p:>8,}  del {d:>8,}')
    bad = verify(old, new, out)
    if bad:
        os.remove(out)
        raise SystemExit(f'delta {frm} -> {to_dump} FAILED its proof: {bad}')
    print(f'delta {frm} -> {to_dump}: {os.path.getsize(out):,} bytes, proven (old + delta == new)')


if __name__ == '__main__':
    main()
