"""More record kinds for the keyed data file, measured on one MusicBrainz dump: what each holds, and its size.

Runs on the build's runner only: nothing that reads the real dump runs anywhere else (the tests use made-up tables).
The build's own steps make today's keyed file. `fetch` takes a second pass of the dump (the tables below only),
`entries` makes the records, and `measure` adds them to a COPY of today's keyed file, bucketed the build's way
(keyed.py), and writes a report: each kind's records and size, the file before and after, and any bucket over the
2 MB cap. Nothing is published.

The kinds. A date is y*10000 + m*100 + d, a part not known 0 (the lists' own form). A record drops its trailing empty
fields (null, 0, [], ''), so a reader takes a missing field as empty. Each is keyed as the build keys its own (a gid
kind by the gid's first bits, a text kind by SHA-1 of the text):
  abo  an artist: [begin, end, ended, area, begin area] (area names)                               artist gid
  aur  an artist: [Wikidata number (after the Q), [official homepages], [Bandcamp pages]]            artist gid
  lnk  an artist's links to other artists, every type, both ways: [way (0 from this artist, 1 to it), link type,
       the other's gid, the other's name, begin, end, ended, [attribute ids]]; by way, type, MusicBrainz's link
       order, begin, end (a missing date last)                                                     artist gid
  agn  an artist's genres: [genre id, votes], most votes first, GENRES at most                       artist gid
  ggn  a release group's genres, the same way                                                        group gid
  crd  a recording's credits: [artist id, link type, [attribute ids]], in MusicBrainz's link order    recording gid
  rcr  a release's own credits (links to the release itself), as crd                                release gid
  who  every artist crd and rcr name: [gid, name]                                                    artist id, as text
  rlb  a release's labels: [label name, catalog number]                                              release gid
  per  an artist's links to whole releases and release groups: [release gid (null for a group's link), group gid,
       title, date (the release's first), printed credit, link type, [attribute ids]]                artist gid
  crx  crd with each artist written in, [gid, name, link type, [attribute ids]]: measured beside crd + who to show
       what naming artists by id saves; never in a total.
The names of link types, attributes and genres: one meta row, `more_names`.

Genres are the MusicBrainz genres among an entity's tags, matched by name (case ignored), votes above 0. The genre
list is in the main dump (CC0); the tags are in mbdump-derived.tar.bz2, which MusicBrainz publishes under
CC BY-NC-SA 3.0.

Usage: python3 -I measure_more.py fetch <dump> <dir>
       python3 -I measure_more.py entries <dir> <more.db>
       python3 -I measure_more.py measure <keyed.db> <more.db> <out dir>     (writes <out dir>/report.txt)
"""
import json, os, re, shutil, sqlite3, sys, time, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_dump, keyed, measure_extra  # noqa: E402
from measure_extra import Batch  # noqa: E402

MORE_CORE = ['area', 'artist', 'artist_credit', 'genre', 'l_artist_artist', 'l_artist_recording', 'l_artist_release',
             'l_artist_release_group', 'l_artist_url', 'label', 'link', 'link_attribute', 'link_attribute_type',
             'link_type', 'recording', 'release', 'release_country', 'release_group', 'release_label',
             'release_unknown_country', 'url']
DERIVED_TAR = 'mbdump-derived.tar.bz2'
DERIVED_TABLES = ['tag', 'artist_tag', 'release_group_tag']
URL_KINDS = {'wikidata': 0, 'official homepage': 1, 'bandcamp': 2}
WIKIDATA = re.compile(r'^https?://(?:www\.)?wikidata\.org/wiki/Q(\d+)/?$', re.I)
GENRES = 10
KINDS = ['abo', 'aur', 'lnk', 'agn', 'ggn', 'crd', 'rcr', 'who', 'rlb', 'per']
COMPARE = ['crx']
FEATURES = [
    ('Members and bands, with years and instruments', ['lnk']),
    ('Where from and active years; birth and death dates', ['abo']),
    ('Wikidata (the way to Wikipedia), official homepage, Bandcamp', ['aur']),
    ('Artist genres (from the tags: CC BY-NC-SA 3.0)', ['agn']),
    ('Album genres (from the tags: CC BY-NC-SA 3.0)', ['ggn']),
    ('Album credits: recordings, the release\'s own, the artists named, labels', ['crd', 'rcr', 'who', 'rlb']),
    ('Person pages (credits on whole releases and release groups)', ['per']),
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
    """The second pass: MORE_CORE from the main dump (fetch_dump's own streaming and MD5 check), the tags from the
    derived dump into <out>/derived/mbdump."""
    fetch_dump.TABLES = list(MORE_CORE)
    fetch_dump.fetch(dump, out)
    measure_extra.fetch_tar(dump, DERIVED_TAR, DERIVED_TABLES, os.path.join(out, 'derived'))


# --- entries -------------------------------------------------------------------

def entries(src_dir, out):
    """Every record of KINDS and COMPARE, into e(kind, h, k, v) (k: the key's text, a gid as lower-case hex; h: its
    bucket hash), with the names in meta and the counts in stat. A work file beside `out` holds the joins, and goes."""
    SRC = os.path.join(src_dir, 'mbdump')
    DER = os.path.join(src_dir, 'derived', 'mbdump')
    rows, drows = measure_extra.reader(SRC), measure_extra.reader(DER)
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
    genre = {int(r[0]): r[2] for r in rows('genre', 3, exact=False)}
    area = {int(r[0]): r[2] for r in rows('area', 3, exact=False)}
    label = {int(r[0]): r[2] for r in rows('label', 3, exact=False)}
    o.execute('INSERT INTO meta VALUES (?,?)', ('more_names', keyed.jdump({
        'link_type': {str(i): t[2] for i, t in sorted(ltype.items())},
        'attribute': {str(i): n for i, n in sorted(attr.items())},
        'genre': {str(i): n for i, n in sorted(genre.items())}})))
    stat['link types'], stat['link attribute types'], stat['genres'] = len(ltype), len(attr), len(genre)
    stat['areas'], stat['labels'] = len(area), len(label)

    # --- artists, and abo as they are read -----------------------------------------
    w.execute('CREATE TABLE a (id INTEGER PRIMARY KEY, gid BLOB NOT NULL, name TEXT NOT NULL)')
    out_a = Batch(w, 'INSERT INTO a VALUES (?,?,?)')
    na = 0

    def abo():
        nonlocal na
        for r in rows('artist', 19, exact=False):
            g = uuid.UUID(r[1]).bytes
            out_a.add((int(r[0]), g, r[2]))
            na += 1
            rec = trim([date(r[4], r[5], r[6]), date(r[7], r[8], r[9]), 1 if r[16] == 't' else 0,
                        area.get(int(r[11])) if r[11] else None, area.get(int(r[17])) if r[17] else None])
            if rec:
                yield by_gid('abo', g, rec)
        out_a.flush()
    put(abo())
    stat['artists'] = na
    del area
    log(f"artists {na:,}; abo {made['abo']:,}")

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
    log(f"lnk {made['lnk']:,}")

    # --- aur: Wikidata, official homepages, Bandcamp -------------------------------
    wanted = {i for i, (e0, e1, nm) in ltype.items() if e0 == 'artist' and e1 == 'url' and nm in URL_KINDS}
    l_table('lau', 'l_artist_url')
    w.execute('CREATE TABLE uk (link INTEGER PRIMARY KEY, kind INTEGER NOT NULL)')
    w.executemany('INSERT INTO uk SELECT id, ? FROM lk WHERE type = ?', ((URL_KINDS[ltype[i][2]], i) for i in wanted))
    want_url = {u for (u,) in w.execute('SELECT x FROM lau JOIN uk ON uk.link = lau.link')}
    text = {}
    for r in rows('url', 3, exact=False):
        if int(r[0]) in want_url:
            text[int(r[0])] = r[2]
    del want_url
    per_kind = {k: 0 for k in URL_KINDS}

    def aur():
        names = {v: k for k, v in URL_KINDS.items()}
        for g, recs in grouped(w.execute(
                'SELECT m.gid, uk.kind, lau.x FROM lau JOIN uk ON uk.link = lau.link JOIN a m ON m.id = lau.a '
                'ORDER BY lau.a, uk.kind, lau.ord, lau.id'), lambda r: r[0], lambda r: r):
            wd, home, band = None, [], []
            for _, kind, u in recs:
                t = text.get(u)
                if t is None:
                    continue
                per_kind[names[kind]] += 1
                if kind == 0:
                    m = WIKIDATA.match(t)
                    if m and wd is None:
                        wd = int(m.group(1))
                elif kind == 1:
                    home.append(t)
                else:
                    band.append(t)
            rec = trim([wd, home, band])
            if rec:
                yield by_gid('aur', g, rec)
    put(aur())
    for k, v in per_kind.items():
        stat[f'  artist links to {k}'] = v
    del text
    w.execute('DROP TABLE lau')
    log(f"aur {made['aur']:,}")

    # --- crd, crx: recordings' credits -------------------------------------------------
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

    def crd():
        for g, rs in grouped(w.execute(
                'SELECT r.gid, l.a, t.type, t.attrs, p.gid, p.name FROM lar l JOIN rec r ON r.id = l.x '
                'JOIN lk t ON t.id = l.link JOIN a p ON p.id = l.a ORDER BY l.x, l.ord, l.id'),
                lambda r: r[0], lambda r: r):
            yield by_gid('crd', g, [trim([a, ty, attrs(at)]) for _, a, ty, at, _, _ in rs])
            yield by_gid('crx', g, [trim([keyed.hx(pg), pn, ty, attrs(at)]) for _, _, ty, at, pg, pn in rs])
    put(crd())
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

    # --- per: links to whole releases and release groups ---------------------------------
    l_table('lrg', 'l_artist_release_group')
    want_rel = {r for (r,) in w.execute('SELECT DISTINCT x FROM lrl')}
    NY, NB = 0xFFFF, 0xFF
    first = {}

    def event(rel, y, m, d):
        if rel in want_rel and y is not None:
            k = (int(y), NB if m is None else int(m), NB if d is None else int(d))
            if rel not in first or k < first[rel]:
                first[rel] = k
    for r in rows('release_country', 5, exact=False):
        event(int(r[0]), r[2], r[3], r[4])
    for r in rows('release_unknown_country', 4, exact=False):
        event(int(r[0]), r[1], r[2], r[3])
    rdate = {rel: y * 10000 + (0 if m == NB else m) * 100 + (0 if d == NB else d) for rel, (y, m, d) in first.items()}
    del first, want_rel
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
                        rdate.get(r[7]) if r[7] is not None else None, acname.get(r[6]), r[8], attrs(r[9])])))
    del rdate, acname
    log(f"per {made['per']:,}")

    # --- agn, ggn: genres among the tags -----------------------------------------------
    by_name = {}
    for i, n in sorted(genre.items()):
        by_name.setdefault(n.lower(), i)
    tag_genre = {}
    for r in drows('tag', 2, exact=False):
        gi = by_name.get(r[1].lower())
        if gi is not None:
            tag_genre[int(r[0])] = gi
    stat['tags that are a genre'] = len(tag_genre)

    def genres(kind, table, ent_sql, tname):
        w.execute(f'CREATE TABLE {tname} (ent INTEGER NOT NULL, g INTEGER NOT NULL, n INTEGER NOT NULL)')
        w.executemany(f'INSERT INTO {tname} VALUES (?,?,?)',
                      ((int(p[0]), tag_genre[int(p[1])], int(p[2])) for p in heads(DER, table, 3)
                       if int(p[1]) in tag_genre and int(p[2]) > 0))
        stat[f'{table} rows kept (a genre, votes above 0)'] = w.execute(f'SELECT count(*) FROM {tname}').fetchone()[0]
        put(by_gid(kind, g, rs[:GENRES]) for g, rs in grouped(w.execute(
            f'SELECT m.gid, x.g, sum(x.n) AS n FROM {tname} x JOIN {ent_sql} m ON m.id = x.ent '
            f'GROUP BY x.ent, x.g ORDER BY x.ent, n DESC, x.g'), lambda r: r[0], lambda r: [r[1], r[2]]))
    genres('agn', 'artist_tag', 'a', 'at')
    genres('ggn', 'release_group_tag', 'rgt', 'gt')
    log(f"agn {made['agn']:,}, ggn {made['ggn']:,}")

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


# --- measure -------------------------------------------------------------------

def bucket_into(c, src, kind, bits):
    """A kind's records into table `kind` of c, bucketed as keyed.keyed does; returns its figures."""
    c.execute(f'CREATE TABLE {kind} (k BLOB PRIMARY KEY, v BLOB NOT NULL) WITHOUT ROWID')
    shift = 63 - bits
    f = {'records': 0, 'plain': 0, 'rows': 0, 'deflated': 0, 'largest': 0, 'over': []}

    def put(b, parts):
        v = keyed._deflate(('{' + ','.join(parts) + '}').encode('utf-8'))
        f['rows'] += 1
        f['deflated'] += len(v)
        f['largest'] = max(f['largest'], len(v))
        if len(v) > keyed.CAP:
            f['over'].append((b, len(v)))
        c.execute(f'INSERT INTO {kind} VALUES (?,?)', (b.to_bytes(4, 'big'), v))
    b, parts = None, []
    for h, k, v in src.execute('SELECT h, k, v FROM e WHERE kind = ? ORDER BY h, k', (kind,)):
        x = h >> shift
        if x != b and parts:
            put(b, parts)
            parts = []
        b = x
        parts.append(keyed.jdump(k) + ':' + v)
        f['records'] += 1
        f['plain'] += len(v.encode('utf-8'))
    if parts:
        put(b, parts)
    return f


def measure(keyed_db, more_db, out_dir):
    """KINDS added to a copy of keyed_db (same bits), COMPARE into a file of its own; the report into out_dir."""
    k0 = sqlite3.connect(f'file:{keyed_db}?mode=ro', uri=True)
    meta = dict(k0.execute('SELECT key, value FROM meta'))
    today = [t for (t,) in k0.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'meta' "
                                      "ORDER BY name")]
    k0.close()
    bits = int(meta.get('bits') or 0)
    if not 1 <= bits <= 24 or meta.get('layout') != 'keyed':
        raise SystemExit(f'{keyed_db}: not a bucketed keyed file (layout {meta.get("layout")}, bits {meta.get("bits")})')
    clash = sorted(set(today) & set(KINDS + COMPARE))
    if clash:
        raise SystemExit(f'{keyed_db} already holds {clash}')
    os.makedirs(out_dir, exist_ok=True)
    comb, comp = os.path.join(out_dir, 'keyed-more.db'), os.path.join(out_dir, 'compare.db')
    for p in (comb, comp):
        if os.path.exists(p):
            os.remove(p)
    shutil.copyfile(keyed_db, comb)
    src = sqlite3.connect(f'file:{more_db}?mode=ro', uri=True)
    names = src.execute("SELECT value FROM meta WHERE key = 'more_names'").fetchone()[0]
    stat = list(src.execute('SELECT key, value FROM stat ORDER BY n'))
    fig = {}
    for path, kinds in ((comb, KINDS), (comp, COMPARE)):
        c = sqlite3.connect(path, isolation_level=None)
        c.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA page_size=4096;')
        c.execute('BEGIN')
        for kind in kinds:
            fig[kind] = bucket_into(c, src, kind, bits)
            log(f"{kind}: {fig[kind]['records']:,} records, {fig[kind]['rows']:,} buckets, {mb(fig[kind]['deflated'])}")
        if path == comb:
            c.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('more_names', names))
        c.execute('COMMIT')
        c.execute('VACUUM')
        pages = measure_extra.btrees(c)
        for kind in kinds:
            fig[kind]['stored'] = pages.get(kind) if pages else None
        if path == comb:
            have_pages = pages is not None
            today_pages = {t: pages.get(t) for t in today} if pages else {}
            meta_bytes = pages.get('meta') if pages else None
            rows_today = sum(c.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in today)
        c.close()
    src.close()
    before, after = os.path.getsize(keyed_db), os.path.getsize(comb)
    text = report(meta, bits, before, after, today, today_pages, meta_bytes, rows_today, fig, stat, have_pages)
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print(text, flush=True)
    os.remove(comp)
    return fig


def report(meta, bits, before, after, today, today_pages, meta_bytes, rows_today, fig, stat, have_pages):
    size = lambda k: fig[k]['stored'] if fig[k]['stored'] is not None else fig[k]['deflated']
    L = [f"More record kinds for the keyed data file: dump {meta.get('dump', '?')}, {bits} bits "
         f"({1 << bits:,} buckets a kind), measured {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}.",
         '',
         f'Today\'s keyed file: {mb(before)} ({before:,} bytes), {len(today)} kinds.',
         f'With the kinds below: {mb(after)} ({after:,} bytes): {mb(after - before)} more, '
         f'{(after - before) * 100 / before:+.1f}%.',
         f'Bucket rows a whole copy holds: today {rows_today:,}; with them '
         f'{rows_today + sum(fig[k]["rows"] for k in KINDS):,}.',
         '',
         ('Stored = the pages SQLite gives the kind\'s table; deflated = its values; plain = its records\' JSON.'
          if have_pages else 'NO dbstat on this SQLite: stored = the values\' deflated bytes.'),
         f"{'kind':5} {'records':>12} {'buckets':>8} {'stored':>12} {'deflated':>12} {'plain':>12} {'largest':>10}"]
    for k in KINDS + COMPARE:
        x = fig[k]
        L.append(f"{k:5} {x['records']:>12,} {x['rows']:>8,} {mb(size(k)):>12} {mb(x['deflated']):>12} "
                 f"{mb(x['plain']):>12} {x['largest'] / 1e3:>8,.1f} kB" + ('   (compared, not in a total)' if k in COMPARE else ''))
    if have_pages:
        L += ['', 'Today\'s kinds, for comparison: ' + ', '.join(f'{t} {mb(today_pages.get(t) or 0)}' for t in today)
              + (f'; meta {mb(meta_bytes)}' if meta_bytes else '')]
    L += ['', 'By feature:']
    for name, kinds in FEATURES:
        L.append(f"  {name:76} {'+'.join(kinds):18} {mb(sum(size(k) for k in kinds)):>12}")
    L.append(f"  {'All of them':76} {'':18} {mb(sum(size(k) for k in KINDS)):>12}   (the file grew {mb(after - before)})")
    L += ['', f"Credits naming artists by id (crd + who) against writing them in (crx): "
              f"{mb(size('crd'))} + {mb(size('who'))} = {mb(size('crd') + size('who'))} against {mb(size('crx'))}."]
    over = [(k, b, n) for k in KINDS + COMPARE for b, n in fig[k]['over']]
    L.append('Over the 2 MB cap: ' + ('none.' if not over else '; '.join(f'{k} bucket {b} {n:,} bytes' for k, b, n in over)))
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
