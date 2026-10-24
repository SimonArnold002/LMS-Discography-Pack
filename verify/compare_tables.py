"""Two format-4 packs hold the same rows in every table, with the same columns.

Usage: python3 -I compare_tables.py <a.db> <b.db>   (exit 1 on any difference)
Used by verify.yml: the builder's format 4 against the published format-3 pack
of the same dump converted by compact_proto.py's rules (meta aside: its
`built` time and `format` differ by construction).
"""
import sqlite3, sys

a, b = sys.argv[1:3]
c = sqlite3.connect(':memory:')
c.execute('ATTACH ? AS a', (a,))
c.execute('ATTACH ? AS b', (b,))
ta = [r[0] for r in c.execute("SELECT name FROM a.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite%' ORDER BY name")]
tb = [r[0] for r in c.execute("SELECT name FROM b.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite%' ORDER BY name")]
bad = 0
if ta != tb:
    print(f'tables differ: {sorted(set(ta) ^ set(tb))}')
    bad += 1
for t in sorted(set(ta) & set(tb)):
    ca = [r[1:3] for r in c.execute(f'PRAGMA a.table_info("{t}")')]
    cb = [r[1:3] for r in c.execute(f'PRAGMA b.table_info("{t}")')]
    if ca != cb:
        print(f'{t}: columns differ {ca} vs {cb}')
        bad += 1
        continue
    where = " WHERE key NOT IN ('built', 'format')" if t == 'meta' else ''
    n = c.execute(f'SELECT count(*) FROM a."{t}"{where}').fetchone()[0]
    for x, y in (('a', 'b'), ('b', 'a')):
        d = c.execute(f'SELECT count(*) FROM (SELECT * FROM {x}."{t}"{where} EXCEPT SELECT * FROM {y}."{t}"{where})').fetchone()[0]
        if d:
            print(f'{t}: {d} row(s) in {x} not in {y}')
            bad += 1
    print(f'  {t:16} {n:>10,} rows')
print('tables: SAME' if not bad else f'tables: {bad} difference(s)')
sys.exit(1 if bad else 0)
