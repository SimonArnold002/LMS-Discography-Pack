"""Where a pack's bytes go, and how full its pages are.

Reports, for one pack file:
  * the file: size, page size, pages, free pages;
  * every table and index (each is one B-tree): pages, bytes on disk, the bytes
    of row data in it, and how full its pages are (row data / page bytes). A
    freshly VACUUMed pack is near 100%; a pack that took deltas in place shows
    where inserts split pages;
  * every column: rows, its length() total (exact for text and blobs, 16 for a
    gid; for an INTEGER it counts the digits, not the 1-8 bytes stored, so the
    B-tree section is the disk figure) and how many distinct values, so a value
    repeated on many rows (a group's gid on each of its releases) shows;
  * a few counts the pack's design rests on.

Usage: python3 -I measure_pack.py <pack.db> [label]
       python3 -I measure_pack.py --grow <old.db> <delta.db> <out.db>
           copies old.db to out.db and applies the delta exactly as the
           device does (make_delta.apply), so out.db is the file a device
           holds after that delta; then measure out.db.
Needs SQLite's dbstat table (Debian/Ubuntu builds have it); without it the
B-tree section is skipped and says so.
"""
import os, shutil, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def mb(n):
    return f'{n / 1e6:9.1f} MB'


def measure(path, label=''):
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    size = os.path.getsize(path)
    ps = c.execute('PRAGMA page_size').fetchone()[0]
    pages = c.execute('PRAGMA page_count').fetchone()[0]
    free = c.execute('PRAGMA freelist_count').fetchone()[0]
    dump = (c.execute("SELECT value FROM meta WHERE key='dump'").fetchone() or [''])[0]
    print(f'\n=== {label or path} (dump {dump})')
    print(f'file {mb(size)}  page {ps} B  pages {pages:,}  free pages {free:,} ({mb(free * ps).strip()})')

    try:
        bt = c.execute('SELECT name, count(*), sum(pgsize), sum(payload), sum(unused) '
                       'FROM dbstat GROUP BY name ORDER BY sum(pgsize) DESC').fetchall()
    except sqlite3.OperationalError as e:
        bt = None
        print(f'(no dbstat: {e}; B-tree section skipped)')
    if bt:
        kind = dict(c.execute('SELECT name, type FROM sqlite_master'))
        owner = dict(c.execute('SELECT name, tbl_name FROM sqlite_master'))
        print(f'\n{"B-tree":28} {"of table":16} {"pages":>9} {"on disk":>12} {"row data":>12} {"full":>6} {"share":>6}')
        for name, n, pg, pay, unused in bt:
            k = kind.get(name, 'index')
            print(f'{name:28} {owner.get(name, name) if k == "index" else "":16} {n:9,} {mb(pg):>12} {mb(pay):>12} '
                  f'{100 * pay / pg if pg else 0:5.1f}% {100 * pg / size:5.1f}%')
        byt = {}
        for name, n, pg, pay, unused in bt:
            byt[owner.get(name, name)] = byt.get(owner.get(name, name), 0) + pg
        print('\nby table, its indexes included:')
        for t, pg in sorted(byt.items(), key=lambda x: -x[1]):
            print(f'  {t:28} {mb(pg)}  {100 * pg / size:5.1f}%')

    print(f'\n{"table.column":34} {"rows":>12} {"bytes":>12} {"avg":>6} {"distinct":>12}')
    gid_bytes = 0
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name NOT LIKE 'sqlite%' ORDER BY name").fetchall():
        cols = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]
        rows = c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        for col in cols:
            b, d = c.execute(f'SELECT coalesce(sum(length("{col}")), 0), count(DISTINCT "{col}") FROM "{t}"').fetchone()
            print(f'{t + "." + col:34} {rows:12,} {b:12,} {b / rows if rows else 0:6.1f} {d:12,}')
            if col in ('gid', 'rg') and t in ('artist', 'rgx', 'release', 'artist_redirect'):
                gid_bytes += b
    print(f'\ngid blobs held as row data (artist, rgx, release, artist_redirect): {mb(gid_bytes).strip()}'
          ' (the unique gid indexes hold them a second time)')

    q = lambda s: c.execute(s).fetchone()[0]
    print('\ndesign counts:')
    for what, s in [
        ('artists', 'SELECT count(*) FROM artist'),
        ('  with no release group (kept for a link/alias/service id)', 'SELECT count(*) FROM artist WHERE ngroups = 0'),
        ('  with no alias, link, service id or info, 1 group', 'SELECT count(*) FROM artist a WHERE ngroups = 1 '
            'AND NOT EXISTS (SELECT 1 FROM artist_alias x WHERE x.artist = a.id) '
            'AND NOT EXISTS (SELECT 1 FROM artist_url x WHERE x.artist = a.id) '
            'AND NOT EXISTS (SELECT 1 FROM artist_rel x WHERE x.a = a.id) '
            'AND NOT EXISTS (SELECT 1 FROM artist_rel x WHERE x.b = a.id)'),
        ('artist_name keys equal to the name as stored', 'SELECT count(*) FROM artist_name n JOIN artist a ON a.id = n.artist WHERE n.key = a.name'),
        ('release map rows', 'SELECT count(*) FROM release'),
        ('  distinct groups among them', 'SELECT count(DISTINCT rg) FROM release'),
        ('groups with a row (rgx)', 'SELECT count(*) FROM rgx'),
        ('  bootleg-only', 'SELECT count(*) FROM rgx WHERE boot = 1'),
        ('  with a Discogs id', 'SELECT count(*) FROM rgx WHERE discogs IS NOT NULL'),
        ('rg_url rows', 'SELECT count(*) FROM rg_url'),
    ]:
        try:
            print(f'  {what:58} {q(s):>12,}')
        except sqlite3.Error as e:
            print(f'  {what:58} ({e})')
    # Why the artists with no release group have a row, and which artists the
    # same-name list's description (artist_info) is ever shown for: only an
    # act whose name key another act shares (Browse::_stampDisambiguation and
    # the same-name rows need 2+ acts of the name).
    try:
        c.execute('CREATE TEMP TABLE ng AS SELECT a.id, '
                  'EXISTS (SELECT 1 FROM artist_rel x WHERE x.a = a.id) OR EXISTS (SELECT 1 FROM artist_rel x WHERE x.b = a.id) AS rel, '
                  'EXISTS (SELECT 1 FROM artist_alias x WHERE x.artist = a.id) AS ali, '
                  '(SELECT group_concat(DISTINCT svc) FROM (SELECT svc FROM artist_url x WHERE x.artist = a.id ORDER BY svc)) AS svcs '
                  'FROM artist a WHERE a.ngroups = 0')
        print('  artists with no release group, by what keeps them (rel = band/collaboration link, '
              'ali = alias, svc 1 Qobuz / 2 Deezer / 3 Discogs):')
        for rel, ali, svcs, n_ in c.execute('SELECT rel, ali, coalesce(svcs, \'-\'), count(*) FROM ng '
                                            'GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 16'):
            print(f'    rel {rel} ali {ali} svc {svcs:8} {n_:>12,}')
        for svc, n_ in c.execute('SELECT svc, count(*) FROM artist_url GROUP BY svc ORDER BY svc'):
            print(f'  artist_url svc {svc}: {n_:,} rows')
        c.execute('CREATE TEMP TABLE shared AS SELECT key FROM artist_name GROUP BY key HAVING count(*) > 1')
        sk = q('SELECT count(*) FROM artist_name WHERE key IN (SELECT key FROM shared)')
        si = q('SELECT count(*) FROM artist_info WHERE artist IN '
               '(SELECT artist FROM artist_name WHERE key IN (SELECT key FROM shared))')
        ub = q('SELECT coalesce(sum(length(artist) + coalesce(length(comment), 0) + 2), 0) FROM artist_info WHERE artist NOT IN '
               '(SELECT artist FROM artist_name WHERE key IN (SELECT key FROM shared))')
        print(f'  artists whose name key another artist shares {sk:>12,}; artist_info rows among them {si:,}')
        print(f'  artist_info rows for a name key NO other artist has: {q("SELECT count(*) FROM artist_info") - si:,}, '
              f'~{mb(ub).strip()} of row data')
    except sqlite3.Error as e:
        print(f'  (breakdown: {e})')
    try:
        for kind, n, b in c.execute('SELECT kind, count(*), sum(length(url)) FROM rg_url GROUP BY kind ORDER BY kind'):
            print(f'  rg_url kind {kind}: {n:,} rows, {mb(b).strip()} of URL text, avg {b / n:.0f} chars')
    except sqlite3.Error as e:
        print(f'  rg_url by kind ({e})')
    c.close()


def grow(old, delta, out):
    import make_delta
    shutil.copyfile(old, out)
    make_delta.apply(out, delta)
    print(f'{old} ({os.path.getsize(old):,} B) + {delta} -> {out} ({os.path.getsize(out):,} B)')


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['--grow'] and len(a) == 4:
        grow(*a[1:])
    elif 1 <= len(a) <= 2:
        measure(*a)
    else:
        raise SystemExit(__doc__)
