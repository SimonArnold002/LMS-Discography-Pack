"""More records for the keyed data file, read from the same MusicBrainz dump as the rest: an artist's dates,
areas and official homepage, every link between artists with its years and attributes, the credits of recordings and
releases, the labels of releases, and the whole-record credits a person's page lists.

  fetch    <dump> <dir>      the build's second pass of the dump: TABLES only (the first, measure_extra.fetch,
                             is today's, made and deleted before this one starts, so the two sets never share the
                             disk)
  entries  <dir> <more.db>   the records, into e(kind, h, k, v) as keyed.entries writes its own (k: the key's text, a
                             gid as lower-case hex; h: its bucket hash); the names of link types and attributes in
                             meta (`more_names`); the counts in stat

keyed.entries then appends abo and lnk to today's artist record and rlb and rcr to today's release record
(keyed.FOLD), and writes the rest as kinds of their own (prf: the abo and lnk of an artist today's records lack; crd,
who, pgr as they are; rol: the names in meta, as two records).

The records. A date is y*10000 + m*100 + d, a part not known 0 (the group lists' own form). A record drops its
trailing empty fields (null, 0, [], ''), so a reader takes a missing field as empty. Each is keyed as the build keys
its own (a gid kind by the gid's first bits, a text kind by SHA-1 of the text):
  abo  an artist: [begin, end, ended, area, begin area, [official homepages]] (area names)          artist gid
  lnk  an artist's links to other artists, every type, both ways: [way (0 from this artist, 1 to it), link type,
       the other's gid, the other's name, begin, end, ended, [attribute ids]]; by way, type, MusicBrainz's link
       order, begin, end (a missing date last)                                                     artist gid
  crd  a recording's credits: [artist id, link type, [attribute ids]], in MusicBrainz's link order    recording gid
  rcr  a release's own credits (links to the release itself), as crd                                release gid
  who  every artist crd and rcr name: [gid, name]                                                    artist id, as text
  rlb  a release's labels: [label name, catalog number]                                              release gid
  pgr  an artist's links to whole releases and release groups, one per group: [group gid, the group's title, the
       first date of the linked releases, the group's printed credit, [[link type, [attribute ids]] each role once]]
                                                                                                   artist gid
Not taken (the community API answers them by MusicBrainz id): an artist's Wikidata page and Bandcamp link, artist and
album genres.

Usage: python3 -I more.py fetch <dump> <dir>
       python3 -I more.py entries <dir> <more.db>
"""
import json, os, sqlite3, sys, time, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_dump, keyed, measure_extra  # noqa: E402
from measure_extra import Batch  # noqa: E402

TABLES = ['area', 'artist', 'artist_credit', 'l_artist_artist', 'l_artist_recording', 'l_artist_release',
          'l_artist_release_group', 'l_artist_url', 'label', 'link', 'link_attribute', 'link_attribute_type',
          'link_type', 'recording', 'release', 'release_country', 'release_group', 'release_label',
          'release_unknown_country', 'url']
HOMEPAGE = 'official homepage'
KINDS = ['abo', 'lnk', 'crd', 'rcr', 'who', 'rlb', 'pgr']

T0 = time.time()


def log(msg):
    print(f'[{time.time() - T0:7.1f}s] {msg}', file=sys.stderr, flush=True)


def date(y, m, d):
    """y*10000 + m*100 + d, a part not known 0; None without a year."""
    if y is None:
        return None
    return int(y) * 10000 + (int(m) if m is not None else 0) * 100 + (int(d) if d is not None else 0)


def trim(x):
    """The record without its trailing empty fields (null, 0, [], '')."""
    while x and (x[-1] is None or x[-1] == [] or x[-1] == '' or (type(x[-1]) is int and x[-1] == 0)):
        x.pop()
    return x


def heads(src, table, n):
    """The first n fields of each row of a table, as written (no escapes undone): for tables read only by their ids
    and gids, which never hold one; a row with fewer fields stops the run."""
    with open(os.path.join(src, table), encoding='utf-8', newline='\n') as f:
        for line in f:
            p = line.split('\t', n)
            if len(p) < n:
                raise SystemExit(f'{table}: {len(p)} fields, expected at least {n}: {line[:200]!r}')
            if len(p) == n:
                p[-1] = p[-1].rstrip('\n')
            yield p[:n]


def num(v):
    return None if v is None or v == '\\N' else int(v)


# --- fetch ---------------------------------------------------------------------

def fetch(dump, out):
    """The second pass of the main dump: TABLES only (fetch_dump's own streaming and MD5 check)."""
    fetch_dump.TABLES = list(TABLES)
    fetch_dump.fetch(dump, out)


# --- entries -------------------------------------------------------------------

def entries(src_dir, out):
    """Every record of KINDS, into e(kind, h, k, v), with the names in meta and the counts in stat. A work file beside
    `out` holds the joins, and goes. Returns the records made, by kind."""
    SRC = os.path.join(src_dir, 'mbdump')
    rows = measure_extra.reader(SRC)
    work = out + '.work'
    for p in (out, work):
        if os.path.exists(p):
            os.remove(p)
    w = sqlite3.connect(work, isolation_level=None)
    w.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-1000000;')
    w.execute('BEGIN')
    o = sqlite3.connect(out, isolation_level=None)
    o.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;'
                    'CREATE TABLE e (kind TEXT NOT NULL, h INTEGER NOT NULL, k TEXT NOT NULL, v TEXT NOT NULL);'
                    'CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;'
                    'CREATE TABLE stat (n INTEGER PRIMARY KEY, key TEXT NOT NULL, value);')
    o.execute('BEGIN')
    stat, made = {}, {k: 0 for k in KINDS}

    def put(gen):
        """gen yields (kind, key text, hash, value JSON)."""
        def counted():
            for kind, k, h, v in gen:
                made[kind] += 1
                yield kind, h, k, v
        o.executemany('INSERT INTO e VALUES (?,?,?,?)', counted())

    def by_gid(kind, g, rec):
        return kind, keyed.hx(g), keyed._h_gid(g), keyed.jdump(rec)

    def grouped(cur, key_of, row_of):
        """(key, [row_of(r) ...]) for runs of rows with the same key_of(r), in the cursor's order."""
        cur_k, acc = None, []
        for r in cur:
            k = key_of(r)
            if k != cur_k:
                if acc:
                    yield cur_k, acc
                cur_k, acc = k, []
            acc.append(row_of(r))
        if acc:
            yield cur_k, acc

    # --- names --------------------------------------------------------------------
    ltype = {int(r[0]): (r[4], r[5], r[6]) for r in rows('link_type', 7, exact=False)}
    attr = {int(r[0]): r[5] for r in rows('link_attribute_type', 6, exact=False)}
    area = {int(r[0]): r[2] for r in rows('area', 3, exact=False)}
    label = {int(r[0]): r[2] for r in rows('label', 3, exact=False)}
    o.execute('INSERT INTO meta VALUES (?,?)', ('more_names', keyed.jdump({
        'link_type': {str(i): t[2] for i, t in sorted(ltype.items())},
        'attribute': {str(i): n for i, n in sorted(attr.items())}})))
    stat['link types'], stat['link attribute types'] = len(ltype), len(attr)
    stat['areas'], stat['labels'] = len(area), len(label)

    # --- links: type, dates, ended, attributes -----------------------------------
    w.execute('CREATE TABLE lk (id INTEGER PRIMARY KEY, type INTEGER NOT NULL, b INTEGER, e INTEGER, '
              'ended INTEGER NOT NULL, attrs TEXT)')
    w.executemany('INSERT INTO lk VALUES (?,?,?,?,?,NULL)',
                  ((int(r[0]), int(r[1]), date(r[2], r[3], r[4]), date(r[5], r[6], r[7]), 1 if r[10] == 't' else 0)
                   for r in rows('link', 11, exact=False)))
    w.execute('CREATE TABLE la (link INTEGER NOT NULL, attr INTEGER NOT NULL)')
    w.executemany('INSERT INTO la VALUES (?,?)', ((int(p[0]), int(p[1])) for p in heads(SRC, 'link_attribute', 2)))
    stat['links'] = w.execute('SELECT count(*) FROM lk').fetchone()[0]
    stat['link attributes'] = w.execute('SELECT count(*) FROM la').fetchone()[0]
    w.executemany('UPDATE lk SET attrs = ? WHERE id = ?',
                  ((keyed.jdump(xs), i) for i, xs in grouped(
                      w.cursor().execute('SELECT link, attr FROM la ORDER BY link, attr'),
                      lambda r: r[0], lambda r: r[1])))
    w.execute('DROP TABLE la')
    log(f"links {stat['links']:,}, attributes {stat['link attributes']:,}")

    def attrs(t):
        return json.loads(t) if t else []

    def l_table(name, table):
        """An l_artist_* table into the work file: (id, link, a, x, ord); returns x's largest id."""
        w.execute(f'CREATE TABLE {name} (id INTEGER NOT NULL, link INTEGER NOT NULL, a INTEGER NOT NULL, '
                  'x INTEGER NOT NULL, ord INTEGER NOT NULL)')
        top = 0

        def gen():
            nonlocal top
            for p in heads(SRC, table, 7):
                x = int(p[3])
                top = max(top, x)
                yield int(p[0]), int(p[1]), int(p[2]), x, int(p[6])
        w.executemany(f'INSERT INTO {name} VALUES (?,?,?,?,?)', gen())
        stat[f'{table} rows'] = w.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
        return top

    # --- official homepages, by artist id, in MusicBrainz's link order ----------------
    home_types = sorted(i for i, (e0, e1, nm) in ltype.items() if e0 == 'artist' and e1 == 'url' and nm == HOMEPAGE)
    l_table('lau', 'l_artist_url')
    pairs = w.execute(f"SELECT u.a, u.x FROM lau u JOIN lk t ON t.id = u.link "
                      f"WHERE t.type IN ({','.join('?' * len(home_types))}) ORDER BY u.a, u.ord, u.id",
                      home_types).fetchall() if home_types else []
    w.execute('DROP TABLE lau')
    want_url = {x for _, x in pairs}
    text = {int(r[0]): r[2] for r in rows('url', 3, exact=False) if int(r[0]) in want_url}
    homes = {}
    for a, x in pairs:
        if x in text:
            homes.setdefault(a, []).append(text[x])
    stat['  artist links to official homepage'] = sum(len(v) for v in homes.values())
    del pairs, want_url, text

    # --- artists, and abo as it is read -------------------------------------------------
    w.execute('CREATE TABLE a (id INTEGER PRIMARY KEY, gid BLOB NOT NULL, name TEXT NOT NULL)')
    out_a = Batch(w, 'INSERT INTO a VALUES (?,?,?)')
    na = 0

    def abo():
        nonlocal na
        for r in rows('artist', 19, exact=False):
            i, g = int(r[0]), uuid.UUID(r[1]).bytes
            out_a.add((i, g, r[2]))
            na += 1
            rec = trim([date(r[4], r[5], r[6]), date(r[7], r[8], r[9]), 1 if r[16] == 't' else 0,
                        area.get(int(r[11])) if r[11] else None, area.get(int(r[17])) if r[17] else None,
                        homes.get(i, [])])
            if rec:
                yield by_gid('abo', g, rec)
        out_a.flush()
    put(abo())
    stat['artists'] = na
    del area, homes
    log(f"artists {na:,}; abo {made['abo']:,}")

    # --- lnk: every artist-artist link, both ways ---------------------------------
    l_table('laa', 'l_artist_artist')
    put((by_gid('lnk', g, [trim([way, ty, keyed.hx(og), on, b, e, en, attrs(at)]) for _, way, ty, og, on, b, e, en, at in recs])
         for g, recs in grouped(w.execute(
             'SELECT m.gid, x.way, t.type, o.gid, o.name, t.b, t.e, t.ended, t.attrs FROM ('
             '  SELECT a AS me, 0 AS way, x AS other, link, ord, id FROM laa'
             '  UNION ALL SELECT x, 1, a, link, ord, id FROM laa) x '
             'JOIN lk t ON t.id = x.link JOIN a m ON m.id = x.me JOIN a o ON o.id = x.other '
             'ORDER BY x.me, x.way, t.type, x.ord, t.b IS NULL, t.b, t.e IS NULL, t.e, x.id'),
             lambda r: r[0], lambda r: r)))
    w.execute('DROP TABLE laa')
    log(f"lnk {made['lnk']:,}")

    # --- crd: recordings' credits -------------------------------------------------------
    named = bytearray(max(1, w.execute('SELECT max(id) FROM a').fetchone()[0] or 0) + 1)
    top_rec = l_table('lar', 'l_artist_recording')
    has = bytearray(top_rec + 1)
    for (x, a) in w.execute('SELECT x, a FROM lar'):
        has[x] = 1
        named[a] = 1
    w.execute('CREATE TABLE rec (id INTEGER PRIMARY KEY, gid BLOB NOT NULL)')
    nrec = 0

    def recs():
        nonlocal nrec
        for p in heads(SRC, 'recording', 2):
            nrec += 1
            i = int(p[0])
            if i <= top_rec and has[i]:
                yield i, uuid.UUID(p[1]).bytes
    w.executemany('INSERT INTO rec VALUES (?,?)', recs())
    del has
    stat['recordings'] = nrec
    stat['  with credits'] = w.execute('SELECT count(*) FROM rec').fetchone()[0]
    w.execute('CREATE INDEX lar_x ON lar (x, ord, id)')
    put(by_gid('crd', g, [trim([a, ty, attrs(at)]) for _, a, ty, at in rs]) for g, rs in grouped(w.execute(
        'SELECT r.gid, l.a, t.type, t.attrs FROM lar l JOIN rec r ON r.id = l.x JOIN lk t ON t.id = l.link '
        'ORDER BY l.x, l.ord, l.id'), lambda r: r[0], lambda r: r))
    w.execute('DROP TABLE lar')
    w.execute('DROP TABLE rec')
    log(f"crd {made['crd']:,}")

    # --- releases and groups (ids, gids, titles, printed credits) --------------------------
    w.execute('CREATE TABLE rel (id INTEGER PRIMARY KEY, gid BLOB NOT NULL, name TEXT NOT NULL, ac INTEGER NOT NULL, '
              'rg INTEGER NOT NULL)')
    w.executemany('INSERT INTO rel VALUES (?,?,?,?,?)', ((int(r[0]), uuid.UUID(r[1]).bytes, r[2], int(r[3]), int(r[4]))
                                                       for r in rows('release', 5, exact=False)))
    w.execute('CREATE TABLE rgt (id INTEGER PRIMARY KEY, gid BLOB NOT NULL, name TEXT NOT NULL, ac INTEGER NOT NULL)')
    w.executemany('INSERT INTO rgt VALUES (?,?,?,?)', ((int(r[0]), uuid.UUID(r[1]).bytes, r[2], int(r[3]))
                                                     for r in rows('release_group', 4, exact=False)))
    stat['releases'] = w.execute('SELECT count(*) FROM rel').fetchone()[0]
    stat['release groups'] = w.execute('SELECT count(*) FROM rgt').fetchone()[0]

    # --- rcr: releases' own credits ------------------------------------------------------
    l_table('lrl', 'l_artist_release')
    for (a,) in w.execute('SELECT a FROM lrl'):
        named[a] = 1
    put(by_gid('rcr', g, [trim([a, ty, attrs(at)]) for _, a, ty, at in rs]) for g, rs in grouped(w.execute(
        'SELECT r.gid, l.a, t.type, t.attrs FROM lrl l JOIN rel r ON r.id = l.x JOIN lk t ON t.id = l.link '
        'ORDER BY l.x, l.ord, l.id'), lambda r: r[0], lambda r: r))
    log(f"rcr {made['rcr']:,}")

    # --- who: every artist crd and rcr name ----------------------------------------------
    put(('who', str(i), keyed._h_text(str(i)), keyed.jdump([keyed.hx(g), n]))
        for i, g, n in w.execute('SELECT id, gid, name FROM a ORDER BY id') if i < len(named) and named[i])
    del named
    log(f"who {made['who']:,}")

    # --- rlb: labels -----------------------------------------------------------------------
    w.execute('CREATE TABLE rlab (id INTEGER NOT NULL, rel INTEGER NOT NULL, label INTEGER, catno TEXT)')
    w.executemany('INSERT INTO rlab VALUES (?,?,?,?)', ((int(r[0]), int(r[1]), num(r[2]), r[3])
                                                       for r in rows('release_label', 4, exact=False)))
    stat['release labels'] = w.execute('SELECT count(*) FROM rlab').fetchone()[0]
    put(by_gid('rlb', g, rs) for g, rs in grouped(w.execute(
        'SELECT r.gid, x.label, x.catno FROM rlab x JOIN rel r ON r.id = x.rel ORDER BY x.rel, x.id'),
        lambda r: r[0], lambda r: trim([label.get(r[1]) if r[1] is not None else None, r[2]])))
    w.execute('DROP TABLE rlab')
    del label
    log(f"rlb {made['rlb']:,}")

    # --- pgr: links to whole releases and release groups, one record a group ----------------
    l_table('lrg', 'l_artist_release_group')
    want_rel = {r for (r,) in w.execute('SELECT DISTINCT x FROM lrl')}
    NB = 0xFF
    first = {}           # release id -> (year, month or NB, day or NB): MusicBrainz's first date, unknown parts last

    def event(rel, y, m, d):
        if rel in want_rel and y is not None:
            k = (int(y), NB if m is None else int(m), NB if d is None else int(d))
            if rel not in first or k < first[rel]:
                first[rel] = k
    for r in rows('release_country', 5, exact=False):
        event(int(r[0]), r[2], r[3], r[4])
    for r in rows('release_unknown_country', 4, exact=False):
        event(int(r[0]), r[1], r[2], r[3])
    as_int = lambda t: t[0] * 10000 + (0 if t[1] == NB else t[1]) * 100 + (0 if t[2] == NB else t[2])
    del want_rel
    want_ac = {ac for (ac,) in w.execute('SELECT r.ac FROM lrl l JOIN rel r ON r.id = l.x UNION '
                                         'SELECT g.ac FROM lrg l JOIN rgt g ON g.id = l.x')}
    acname = {int(r[0]): r[1] for r in rows('artist_credit', 2, exact=False) if int(r[0]) in want_ac}
    del want_ac

    def pgr():
        for g, rs in grouped(w.execute(
                'SELECT m.gid, g.id, g.gid, g.name, g.ac, t.type, t.attrs, l.x FROM lrl l JOIN a m ON m.id = l.a '
                'JOIN rel r ON r.id = l.x JOIN rgt g ON g.id = r.rg JOIN lk t ON t.id = l.link '
                'UNION ALL '
                'SELECT m.gid, g.id, g.gid, g.name, g.ac, t.type, t.attrs, NULL FROM lrg l JOIN a m ON m.id = l.a '
                'JOIN rgt g ON g.id = l.x JOIN lk t ON t.id = l.link '
                'ORDER BY 1, 2, 8'), lambda r: r[0], lambda r: r):
            out = []
            for _, gs in grouped(rs, lambda r: r[1], lambda r: r):
                _, _, gg, title, ac = gs[0][:5]
                when = min((first[r[7]] for r in gs if r[7] in first), default=None)
                roles = sorted({(r[5], tuple(attrs(r[6]))) for r in gs})
                out.append(trim([keyed.hx(gg), title, as_int(when) if when else None, acname.get(ac),
                                 [trim([ty, list(at)]) for ty, at in roles]]))
            yield by_gid('pgr', g, out)
    put(pgr())
    del first, acname
    log(f"pgr {made['pgr']:,}")

    w.execute('COMMIT')
    w.close()
    os.remove(work)
    for k in KINDS:
        stat[f'records {k}'] = made[k]
    o.executemany('INSERT INTO stat VALUES (?,?,?)', ((i, k, v) for i, (k, v) in enumerate(stat.items())))
    o.execute('COMMIT')
    o.execute('CREATE INDEX e_bucket ON e (kind, h, k)')
    dup = o.execute('SELECT kind, k FROM e GROUP BY kind, k HAVING count(*) > 1 LIMIT 1').fetchone()
    o.close()
    if dup:
        raise SystemExit(f'two records for one key: {dup}')
    log(f'more records done: {os.path.getsize(out):,} bytes')
    return made


def main():
    cmd, a = (sys.argv[1] if len(sys.argv) > 1 else ''), sys.argv[1:]
    if cmd == 'fetch' and len(a) == 3:
        fetch(a[1], a[2])
    elif cmd == 'entries' and len(a) == 3:
        entries(a[1], a[2])
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
