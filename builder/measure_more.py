"""More record kinds for the keyed data file, measured on one MusicBrainz dump: what each holds, its size, and how
small the file can be made.

Runs on the build's runner only: nothing that reads the real dump runs anywhere else (the tests use made-up tables).
The build's own steps make today's keyed file. `fetch` takes a second pass of the dump (the tables below only),
`entries` makes the records, and `measure` writes them three ways and reports each, with the file before and after:
  as built  each new kind a table of its own, bucketed the build's way (keyed.py), today's bits;
  folded    the new kinds keyed as today's art and rel are, appended to those records (one read for both): abo and
            lnk to art, rlb and rcr to rel; a key today's kind lacks goes to a kind of its own (FOLD);
  sized     every table at the bucket count, from bits - SWEEP_DOWN to bits + SWEEP_UP, that stores it smallest
            while its average bucket stays within today's largest average bucket (so no lookup reads more than
            today's do); a bucket over the 2 MB cap rules a count out. Today's kinds are sized too.
Nothing is published.

Left out (round 2, 2026-11-20): what the community API answers by MusicBrainz id, an artist's Wikidata page and
Bandcamp link, artist and album genres (run 44734388638 measured them: COMMUNITY). The official homepage, which it
lacks, rides in abo.

The kinds. A date is y*10000 + m*100 + d, a part not known 0 (the lists' own form). A record drops its trailing empty
fields (null, 0, [], ''), so a reader takes a missing field as empty. Each is keyed as the build keys its own (a gid
kind by the gid's first bits, a text kind by SHA-1 of the text):
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
Measured beside them, never in a total:
  abn  abo without the homepages: what they cost
  per  pgr one per release, as run 1 had it: [release gid (null for a group's link), group gid, title, date (the
       release's first), printed credit, link type, [attribute ids]]
The names of link types and attributes: one meta row, `more_names`.

Usage: python3 -I measure_more.py fetch <dump> <dir>
       python3 -I measure_more.py entries <dir> <more.db>
       python3 -I measure_more.py measure <keyed.db> <more.db> <out dir>     (writes <out dir>/report.txt)
"""
import concurrent.futures, json, os, shutil, sqlite3, sys, time, uuid, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_dump, keyed, measure_extra  # noqa: E402
from measure_extra import Batch  # noqa: E402

MORE_CORE = ['area', 'artist', 'artist_credit', 'l_artist_artist', 'l_artist_recording', 'l_artist_release',
             'l_artist_release_group', 'l_artist_url', 'label', 'link', 'link_attribute', 'link_attribute_type',
             'link_type', 'recording', 'release', 'release_country', 'release_group', 'release_label',
             'release_unknown_country', 'url']
HOMEPAGE = 'official homepage'
KINDS = ['abo', 'lnk', 'crd', 'rcr', 'who', 'rlb', 'pgr']
COMPARE = ['abn', 'per']
TEXT_KINDS = {'who'}                       # keyed by SHA-1 of the text; every other new kind by its gid
FOLD = {'art': (['abo', 'lnk'], 'prf'), 'rel': (['rlb', 'rcr'], 'rlx')}   # today's kind: (appended, the rest's kind)
SWEEP_DOWN, SWEEP_UP = 4, 2
COMMUNITY = [('Wikidata (the way to Wikipedia) and Bandcamp', 'aur', 73.4), ('Artist genres', 'agn', 6.4),
             ('Album genres', 'ggn', 75.5)]
FEATURES = [
    ('Members and bands, with years and instruments', ['lnk']),
    ('Where from, active years, birth and death dates, official homepage', ['abo']),
    ('Album credits: recordings, the release\'s own, the artists named, labels', ['crd', 'rcr', 'who', 'rlb']),
    ('Person pages (whole-record credits, one per release group)', ['pgr']),
]

T0 = time.time()


def log(msg):
    print(f'[{time.time() - T0:7.1f}s] {msg}', flush=True)


def mb(n):
    return f'{n / 1e6:,.1f} MB'


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
    """The second pass: MORE_CORE from the main dump (fetch_dump's own streaming and MD5 check)."""
    fetch_dump.TABLES = list(MORE_CORE)
    fetch_dump.fetch(dump, out)


# --- entries -------------------------------------------------------------------

def entries(src_dir, out):
    """Every record of KINDS and COMPARE, into e(kind, h, k, v) (k: the key's text, a gid as lower-case hex; h: its
    bucket hash), with the names in meta and the counts in stat. A work file beside `out` holds the joins, and goes."""
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
    stat, made = {}, {k: 0 for k in KINDS + COMPARE}

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

    # --- artists, and abo / abn as they are read -------------------------------------
    w.execute('CREATE TABLE a (id INTEGER PRIMARY KEY, gid BLOB NOT NULL, name TEXT NOT NULL)')
    out_a = Batch(w, 'INSERT INTO a VALUES (?,?,?)')
    na = 0

    def abo():
        nonlocal na
        for r in rows('artist', 19, exact=False):
            i, g = int(r[0]), uuid.UUID(r[1]).bytes
            out_a.add((i, g, r[2]))
            na += 1
            base = [date(r[4], r[5], r[6]), date(r[7], r[8], r[9]), 1 if r[16] == 't' else 0,
                    area.get(int(r[11])) if r[11] else None, area.get(int(r[17])) if r[17] else None]
            rec = trim(base + [homes.get(i, [])])
            if rec:
                yield by_gid('abo', g, rec)
            rec = trim(list(base))
            if rec:
                yield by_gid('abn', g, rec)
        out_a.flush()
    put(abo())
    stat['artists'] = na
    del area, homes
    log(f"artists {na:,}; abo {made['abo']:,}, abn {made['abn']:,}")

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

    # --- pgr and per: links to whole releases and release groups ---------------------------
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
    put(by_gid('per', g, rs) for g, rs in grouped(w.execute(
        'SELECT m.gid, 0, l.id, r.gid, g.gid, r.name, r.ac, l.x, t.type, t.attrs FROM lrl l JOIN a m ON m.id = l.a '
        'JOIN rel r ON r.id = l.x JOIN rgt g ON g.id = r.rg JOIN lk t ON t.id = l.link '
        'UNION ALL '
        'SELECT m.gid, 1, l.id, NULL, g.gid, g.name, g.ac, NULL, t.type, t.attrs FROM lrg l JOIN a m ON m.id = l.a '
        'JOIN rgt g ON g.id = l.x JOIN lk t ON t.id = l.link '
        'ORDER BY 1, 2, 3'), lambda r: r[0],
        lambda r: trim([keyed.hx(r[3]) if r[3] is not None else None, keyed.hx(r[4]), r[5],
                        as_int(first[r[7]]) if r[7] in first else None, acname.get(r[6]), r[8], attrs(r[9])])))
    log(f"per {made['per']:,}")

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
    for k in KINDS + COMPARE:
        stat[f'records {k}'] = made[k]
    o.executemany('INSERT INTO stat VALUES (?,?,?)', ((i, k, v) for i, (k, v) in enumerate(stat.items())))
    o.execute('COMMIT')
    o.execute('CREATE INDEX e_bucket ON e (kind, h, k)')
    dup = o.execute('SELECT kind, k FROM e GROUP BY kind, k HAVING count(*) > 1 LIMIT 1').fetchone()
    o.close()
    if dup:
        raise SystemExit(f'two records for one key: {dup}')
    log(f'entries done: {os.path.getsize(out):,} bytes')
    return made


# --- buckets -------------------------------------------------------------------

def _figures():
    return {'records': 0, 'plain': 0, 'rows': 0, 'deflated': 0, 'largest': 0, 'over': []}


def _put_bucket(c, table, f, b, parts):
    v = keyed._deflate(('{' + ','.join(parts) + '}').encode('utf-8'))
    f['rows'] += 1
    f['deflated'] += len(v)
    f['largest'] = max(f['largest'], len(v))
    if len(v) > keyed.CAP:
        f['over'].append((b, len(v)))
    c.execute(f'INSERT INTO {table} VALUES (?,?)', (b.to_bytes(4, 'big'), v))


def bucket_into(c, recs, table, bits):
    """Records (h, key text, value JSON), sorted by h then key, into a new table of c, bucketed as keyed.keyed does;
    returns its figures."""
    c.execute(f'CREATE TABLE {table} (k BLOB PRIMARY KEY, v BLOB NOT NULL) WITHOUT ROWID')
    shift = 63 - bits
    f = _figures()
    b, parts = None, []
    for h, k, v in recs:
        x = h >> shift
        if x != b and parts:
            _put_bucket(c, table, f, b, parts)
            parts = []
        b = x
        parts.append(keyed.jdump(k) + ':' + v)
        f['records'] += 1
        f['plain'] += len(v.encode('utf-8'))
    if parts:
        _put_bucket(c, table, f, b, parts)
    return f


def _kind_rows(src, kind):
    return src.execute('SELECT h, k, v FROM e WHERE kind = ? ORDER BY h, k', (kind,))


def _h_of(key, gid):
    return keyed._h_gid(bytes.fromhex(key)) if gid else keyed._h_text(key)


def rebucket(src, table, frm, to, gid):
    """(bucket, value) for every bucket of `table` (built at `frm` bits) at `to` bits: the bytes keyed.keyed gives
    those records at `to` bits. Fewer bits join neighbouring buckets; more split each by its keys' hashes."""
    cur = src.execute(f'SELECT k, v FROM {table} ORDER BY k')
    if to <= frm:
        d, b, parts = frm - to, None, []
        for k, v in cur:
            x = int.from_bytes(k, 'big') >> d
            if x != b and parts:
                yield b, keyed._deflate(b'{' + b','.join(parts) + b'}')
                parts = []
            b = x
            parts.append(zlib.decompress(v, -15)[1:-1])
        if parts:
            yield b, keyed._deflate(b'{' + b','.join(parts) + b'}')
        return
    shift = 63 - to
    for _, v in cur:
        split = {}
        for key, rec in json.loads(zlib.decompress(v, -15)).items():
            split.setdefault(_h_of(key, gid) >> shift, []).append(keyed.jdump(key) + ':' + keyed.jdump(rec))
        for x in sorted(split):
            yield x, keyed._deflate(('{' + ','.join(split[x]) + '}').encode('utf-8'))


def _new_db(path):
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path, isolation_level=None)
    c.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA page_size=4096;')
    return c


def size_at(job):
    """One table at one bucket count, in a file of its own that is measured and removed: (label, bits, figures)."""
    label, path, table, gid, frm, to, scratch = job
    src = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    c = _new_db(scratch)
    c.execute('CREATE TABLE t (k BLOB PRIMARY KEY, v BLOB NOT NULL) WITHOUT ROWID')
    f = _figures()
    c.execute('BEGIN')
    for b, v in rebucket(src, table, frm, to, gid):
        f['rows'] += 1
        f['deflated'] += len(v)
        f['largest'] = max(f['largest'], len(v))
        if len(v) > keyed.CAP:
            f['over'].append((b, len(v)))
        c.execute('INSERT INTO t VALUES (?,?)', (b.to_bytes(4, 'big'), v))
    c.execute('COMMIT')
    c.execute('VACUUM')
    pages = measure_extra.btrees(c)
    f['stored'] = pages.get('t') if pages else None
    c.close()
    src.close()
    os.remove(scratch)
    return label, to, f


def build_sized(job):
    """A whole file with each table at its own bucket count: (name, bytes, bucket rows)."""
    name, out, meta, tables = job
    c = _new_db(out)
    c.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID')
    c.execute('BEGIN')
    c.executemany('INSERT INTO meta VALUES (?,?)', sorted(meta.items()))
    rows = 0
    for table, path, src_table, gid, frm, to in tables:
        src = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        c.execute(f'CREATE TABLE {table} (k BLOB PRIMARY KEY, v BLOB NOT NULL) WITHOUT ROWID')
        for b, v in rebucket(src, src_table, frm, to, gid):
            c.execute(f'INSERT INTO {table} VALUES (?,?)', (b.to_bytes(4, 'big'), v))
            rows += 1
        src.close()
    c.execute('COMMIT')
    c.execute('VACUUM')
    c.close()
    return name, os.path.getsize(out), rows


def _pool(fn, jobs):
    """fn over jobs, in as many processes as the machine has cores (one: in this process)."""
    n = min(len(jobs), os.cpu_count() or 1)
    if n <= 1:
        return [fn(j) for j in jobs]
    with concurrent.futures.ProcessPoolExecutor(max_workers=n) as ex:
        return list(ex.map(fn, jobs))


# --- folded ----------------------------------------------------------------------

def fold_into(c, src, kind, adds, bits):
    """Today's table `kind` of c, each record that has records of `adds` under its key with them appended (one field
    each, [] for one it lacks; trailing empties dropped). A bucket none of them reaches keeps its bytes. Returns its
    figures and the rest: (h, key, record) for keys `kind` lacks (or holds as a redirect), the record being the adds'
    fields as they would be appended."""
    shift = 63 - bits
    joined = lambda xs: trim([json.loads(x) if x is not None else [] for x in xs])

    def extras(b):
        """{key: [h, [each add's record JSON or None]]} for the records of `adds` in bucket b."""
        got = {}
        for i, a in enumerate(adds):
            for h, k, x in src.execute('SELECT h, k, v FROM e WHERE kind = ? AND h BETWEEN ? AND ? ORDER BY h, k',
                                       (a, b << shift, ((b + 1) << shift) - 1)):     # the top one ends at 2^63 - 1
                got.setdefault(k, [h, [None] * len(adds)])[1][i] = x
        return got
    c.execute(f'CREATE TABLE {kind}_f (k BLOB PRIMARY KEY, v BLOB NOT NULL) WITHOUT ROWID')
    f, rest = _figures(), []
    f['added'] = 0
    have = set()
    for kb, v in c.cursor().execute(f'SELECT k, v FROM {kind} ORDER BY k'):
        b = int.from_bytes(kb, 'big')
        have.add(b)
        extra = extras(b)
        obj = json.loads(zlib.decompress(v, -15))
        f['records'] += len(obj)
        out = v
        if extra:
            parts = []
            for k, rec in obj.items():
                if k in extra and isinstance(rec, list):
                    rec = rec + joined(extra.pop(k)[1])
                    f['added'] += 1
                parts.append(keyed.jdump(k) + ':' + keyed.jdump(rec))
            out = keyed._deflate(('{' + ','.join(parts) + '}').encode('utf-8'))
            rest += [(h, k, keyed.jdump(joined(xs))) for k, (h, xs) in extra.items()]
        f['rows'] += 1
        f['deflated'] += len(out)
        f['largest'] = max(f['largest'], len(out))
        if len(out) > keyed.CAP:
            f['over'].append((b, len(out)))
        c.execute(f'INSERT INTO {kind}_f VALUES (?,?)', (kb, out))
    # Records in a bucket today's kind does not have at all.
    for b in sorted(set(range(1 << bits)) - have):
        rest += [(h, k, keyed.jdump(joined(xs))) for k, (h, xs) in extras(b).items()]
    c.execute(f'DROP TABLE {kind}')
    c.execute(f'ALTER TABLE {kind}_f RENAME TO {kind}')
    return f, sorted(rest)


# --- measure -------------------------------------------------------------------

def measure(keyed_db, more_db, out_dir):
    """The three ways (the module's docstring), each built for real and measured; the report into out_dir."""
    k0 = sqlite3.connect(f'file:{keyed_db}?mode=ro', uri=True)
    meta = dict(k0.execute('SELECT key, value FROM meta'))
    today = [t for (t,) in k0.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'meta' "
                                      "ORDER BY name")]
    rows_today = sum(k0.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in today)
    k0.close()
    bits = int(meta.get('bits') or 0)
    if not 1 <= bits <= 24 or meta.get('layout') != 'keyed':
        raise SystemExit(f'{keyed_db}: not a bucketed keyed file (layout {meta.get("layout")}, bits {meta.get("bits")})')
    rests = [r for _, r in FOLD.values()]
    clash = sorted(set(today) & set(KINDS + COMPARE + rests))
    if clash:
        raise SystemExit(f'{keyed_db} already holds {clash}')
    missing = sorted(set(FOLD) - set(today))
    if missing:
        raise SystemExit(f'{keyed_db} lacks {missing}, which the folded way appends to')
    os.makedirs(out_dir, exist_ok=True)
    sep, comp, fold = (os.path.join(out_dir, n) for n in ('keyed-more.db', 'compare.db', 'folded.db'))
    src = sqlite3.connect(f'file:{more_db}?mode=ro', uri=True)
    names = src.execute("SELECT value FROM meta WHERE key = 'more_names'").fetchone()[0]
    stat = list(src.execute('SELECT key, value FROM stat ORDER BY n'))
    fig, ffig = {}, {}

    # As built: each new kind a table of its own; the comparisons in a file of their own.
    for path, kinds in ((sep, KINDS), (comp, COMPARE)):
        if path == sep:
            if os.path.exists(path):
                os.remove(path)
            shutil.copyfile(keyed_db, path)
            c = sqlite3.connect(path, isolation_level=None)
            c.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;')
        else:
            c = _new_db(path)
        c.execute('BEGIN')
        for kind in kinds:
            fig[kind] = bucket_into(c, _kind_rows(src, kind), kind, bits)
            log(f"{kind}: {fig[kind]['records']:,} records, {fig[kind]['rows']:,} buckets, {mb(fig[kind]['deflated'])}")
        if path == sep:
            c.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('more_names', names))
        c.execute('COMMIT')
        c.execute('VACUUM')
        pages = measure_extra.btrees(c)
        for kind in kinds:
            fig[kind]['stored'] = pages.get(kind) if pages else None
        if path == sep:
            have_pages = pages is not None
            today_pages = {t: pages.get(t) for t in today} if pages else {}
        c.close()

    # Folded: abo and lnk appended to art, rlb and rcr to rel; the rest in kinds of their own; crd, who, pgr as built.
    if os.path.exists(fold):
        os.remove(fold)
    shutil.copyfile(keyed_db, fold)
    c = sqlite3.connect(fold, isolation_level=None)
    c.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;')
    c.execute('BEGIN')
    for kind, (adds, rest_kind) in FOLD.items():
        ffig[kind], rest = fold_into(c, src, kind, adds, bits)
        ffig[rest_kind] = bucket_into(c, rest, rest_kind, bits) if rest else _figures()
        log(f"folded {kind}: {ffig[kind]['added']:,} records took {'+'.join(adds)}; {rest_kind} {len(rest):,}")
    for kind in KINDS:
        if not any(kind in adds for adds, _ in FOLD.values()):
            bucket_into(c, _kind_rows(src, kind), kind, bits)
    c.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('more_names', names))
    c.execute('COMMIT')
    c.execute('VACUUM')
    pages = measure_extra.btrees(c)
    for kind in ffig:
        ffig[kind]['stored'] = pages.get(kind) if pages else None
    fold_tables = [t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'meta' "
                                           "ORDER BY name")]
    c.close()
    src.close()

    # Sized: every table at every bucket count of the sweep.
    levels = list(range(max(1, bits - SWEEP_DOWN), bits + SWEEP_UP + 1))
    gid_of = lambda t: t in keyed.GID_KINDS or (t not in keyed.KINDS and t not in TEXT_KINDS)
    sources = ([(t, keyed_db, t) for t in today] + [(k, sep, k) for k in KINDS] +
               [(t + '+', fold, t) for t in FOLD] + [(r, fold, r) for r in rests if r in fold_tables])
    jobs = [(label, path, table, gid_of(table), bits, to, os.path.join(out_dir, f'size-{label}-{to}.db'))
            for label, path, table in sources for to in levels]
    sweep = {}
    for label, to, f in _pool(size_at, jobs):
        sweep.setdefault(label, {})[to] = f
    limit = max(sweep[t][bits]['deflated'] / sweep[t][bits]['rows'] for t in today if sweep[t][bits]['rows'])
    widest = max((t for t in today if sweep[t][bits]['rows']),
                 key=lambda t: sweep[t][bits]['deflated'] / sweep[t][bits]['rows'])
    picks = {label: pick(sweep[label], limit, bits) for label in sweep}
    log('sized: ' + ', '.join(f'{k} {v}' for k, v in sorted(picks.items())))

    # The three files at the picked counts, built whole.
    src_of = {label: (path, table) for label, path, table in sources}
    other = lambda t: t not in FOLD
    sized_sets = {
        'today': [(t, *src_of[t], gid_of(t), bits, picks[t]) for t in today],
        'separate': [(t, *src_of[t], gid_of(t), bits, picks[t]) for t in today] +
                    [(k, *src_of[k], gid_of(k), bits, picks[k]) for k in KINDS],
        'folded': [(t, *src_of[t + '+'], gid_of(t), bits, picks[t + '+']) for t in FOLD] +
                  [(r, *src_of[r], True, bits, picks[r]) for r in rests if r in src_of] +
                  [(t, *src_of[t], gid_of(t), bits, picks[t]) for t in today if other(t)] +
                  [(k, *src_of[k], gid_of(k), bits, picks[k]) for k in KINDS
                   if not any(k in adds for adds, _ in FOLD.values())],
    }
    built = {}
    jobs = []
    for name, tables in sized_sets.items():
        m = dict(meta, bits_by_kind=keyed.jdump({t: to for t, _, _, _, _, to in tables}))
        if name != 'today':
            m['more_names'] = names
        jobs.append((name, os.path.join(out_dir, f'sized-{name}.db'), m, tables))
    for name, size, rows in _pool(build_sized, jobs):
        built[name] = (size, rows)

    files = {
        'today': (os.path.getsize(keyed_db), rows_today),
        'separate': (os.path.getsize(sep), rows_today + sum(fig[k]['rows'] for k in KINDS)),
        'folded': (os.path.getsize(fold), None),
    }
    c = sqlite3.connect(f'file:{fold}?mode=ro', uri=True)
    files['folded'] = (files['folded'][0], sum(c.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in fold_tables))
    c.close()
    text = report(meta, bits, today, today_pages, have_pages, fig, ffig, sweep, picks, limit, widest, levels, files,
                  built, stat)
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print(text, flush=True)
    for p in [comp] + [os.path.join(out_dir, f'sized-{n}.db') for n in sized_sets]:
        os.remove(p)
    return {'fig': fig, 'ffig': ffig, 'sweep': sweep, 'picks': picks, 'limit': limit, 'files': files, 'built': built}


def pick(by_bits, limit, bits):
    """The bucket count that stores the table smallest among those whose average bucket is within `limit` and none
    over the cap (a tie: fewer buckets); with none such, the one with the smallest average bucket."""
    size = lambda b: by_bits[b]['stored'] if by_bits[b]['stored'] is not None else by_bits[b]['deflated']
    ok = [b for b in sorted(by_bits) if not by_bits[b]['over'] and by_bits[b]['rows']
          and by_bits[b]['deflated'] / by_bits[b]['rows'] <= limit]
    if ok:
        return min(ok, key=lambda b: (size(b), b))
    return min(by_bits, key=lambda b: (by_bits[b]['deflated'] / max(1, by_bits[b]['rows']), -b))


def report(meta, bits, today, today_pages, have_pages, fig, ffig, sweep, picks, limit, widest, levels, files, built,
           stat):
    size = lambda f: f['stored'] if f.get('stored') is not None else f['deflated']
    at = lambda label, b: size(sweep[label][b])
    rows = lambda n: f'{n:,}' if n is not None else '?'
    L = [f"More record kinds for the keyed data file, round 2: dump {meta.get('dump', '?')}, built at {bits} bits "
         f"({1 << bits:,} buckets a kind), measured {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}.",
         '',
         'Left out: the community API answers these by MusicBrainz id (run 44734388638 measured them as kinds):']
    for name, kind, size_mb in COMMUNITY:
        L.append(f'  {name:62} {kind:5} {size_mb:>8,.1f} MB')
    L += ['The official homepage, which the community API lacks, rides in abo.', '',
          ('Stored = the pages SQLite gives a table; deflated = its values; plain = its records\' JSON.'
           if have_pages else 'NO dbstat on this SQLite: stored = the values\' deflated bytes.'),
          '',
          'THE FILE                                                     size          bucket rows (rows a load writes)']
    base = files['today'][0]
    for label, (sz, n) in [('today, as built', files['today']), ('today, sized', built['today']),
                           ('with the new kinds, as built', files['separate']),
                           ('with the new kinds, sized', built['separate']),
                           ('with the new kinds folded in, as built', files['folded']),
                           ('with the new kinds folded in, sized', built['folded'])]:
        L.append(f'  {label:52} {mb(sz):>12} {(sz - base) * 100 / base:+7.1f}%   {rows(n):>10}')
    L += ['',
          f'Sized: each table at the bucket count ({levels[0]} to {levels[-1]} bits) that stores it smallest while its',
          f'average bucket stays within today\'s largest average bucket ({widest}, {limit / 1e3:,.1f} kB deflated), so no',
          'lookup reads more than today\'s do; a bucket over the 2 MB cap rules a count out.',
          '',
          f'THE NEW KINDS, AS BUILT ({bits} bits)',
          f"{'kind':5} {'records':>12} {'buckets':>8} {'stored':>12} {'deflated':>12} {'plain':>12} {'largest':>10}"]
    for k in KINDS + COMPARE:
        x = fig[k]
        L.append(f"{k:5} {x['records']:>12,} {x['rows']:>8,} {mb(size(x)):>12} {mb(x['deflated']):>12} "
                 f"{mb(x['plain']):>12} {x['largest'] / 1e3:>8,.1f} kB" + ('   (compared, not in a total)' if k in COMPARE else ''))
    L += [f"  The official homepages (abo against abn): {mb(size(fig['abo']) - size(fig['abn']))}.",
          f"  Person pages one per group (pgr) against one per release (per): {mb(size(fig['pgr']))} against "
          f"{mb(size(fig['per']))}.",
          '',
          'FOLDED: the new kinds appended to today\'s records of the same key']
    for kind, (adds, rest) in FOLD.items():
        x, r = ffig[kind], ffig[rest]
        sep_sum = sum(size(fig[a]) for a in adds)
        L.append(f"  {kind} + {' + '.join(adds):12} {x['records']:>10,} records, {x['added']:>10,} took them: "
                 f"{mb(today_pages.get(kind) or 0)} -> {mb(size(x))}; as separate kinds {mb(sep_sum)}")
        L.append(f"  {rest} (keys {kind} lacks)  {r['records']:>10,} records, {mb(size(r))}")
    L += ['', f'BUCKET COUNTS: stored at each count (average bucket, deflated); * = the pick',
          f"{'table':6}" + ''.join(f'{b:>22}' for b in levels)]
    for label in sorted(sweep, key=lambda s: (s not in today, s)):
        cells = []
        for b in levels:
            x = sweep[label][b]
            avg = x['deflated'] / x['rows'] / 1e3 if x['rows'] else 0
            mark = '*' if picks[label] == b else ('!' if x['over'] else ' ')
            cells.append(f'{mb(size(x)):>11} ({avg:>5,.1f} kB){mark}')
        L.append(f'{label:6}' + ''.join(f'{c:>22}' for c in cells))
    L += ['  ! = a bucket over the 2 MB cap', '',
          f"{'BY FEATURE':91} {'as built':>12} {'sized':>12}"]
    for name, kinds in FEATURES:
        L.append(f"  {name:74} {'+'.join(kinds):15} {mb(sum(size(fig[k]) for k in kinds)):>12} "
                 f"{mb(sum(at(k, picks[k]) for k in kinds)):>12}")
    L.append(f"  {'All of them':74} {'':15} {mb(sum(size(fig[k]) for k in KINDS)):>12} "
             f"{mb(sum(at(k, picks[k]) for k in KINDS)):>12}")
    over = [(k, b, n) for k in KINDS + COMPARE for b, n in fig[k]['over']] + \
           [(k, b, n) for k in ffig for b, n in ffig[k]['over']]
    L += ['', 'Over the 2 MB cap, as built: ' + ('none.' if not over else '; '.join(f'{k} bucket {b} {n:,} bytes'
                                                                                     for k, b, n in over))]
    L += ['', 'Read from the dump:']
    for k, v in stat:
        L.append(f'  {k:66} {v:>14,}' if isinstance(v, int) else f'  {k}: {v}')
    return '\n'.join(L) + '\n'


def main():
    cmd, a = (sys.argv[1] if len(sys.argv) > 1 else ''), sys.argv[1:]
    if cmd == 'fetch' and len(a) == 3:
        fetch(a[1], a[2])
    elif cmd == 'entries' and len(a) == 3:
        entries(a[1], a[2])
    elif cmd == 'measure' and len(a) == 4:
        measure(a[1], a[2], a[3])
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
