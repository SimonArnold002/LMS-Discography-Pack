"""How much bigger the pack would be with Discography's candidate tables.

Simon, 2026-10-31: "I would need to see how much larger the pack would be with
this extra data, cant be a guess needs to be hard evidence via a build". Runs on
the GitHub runner only (nothing that touches the real dump runs on Simon's Mac)
and publishes nothing. The workflow (measure-extra.yml) builds today's pack with
builder/build_pack.py, unchanged, from the same dump; this script writes each
candidate into a COPY of it, compacts the copy (VACUUM, as the builder does),
gzips it as a release is (gzip -9 -n), and reports the sizes.

The candidates. None is in any format: each is the smallest table that answers
its lookup, keyed by MusicBrainz's own ids, with no secondary index (so each
would also fit format 4's rule):
  lists    Discography's item 3 (2026-10-31): every artist's release groups, so
           a page opened cold draws without ListenBrainz and the community API.
           rg: id, gid, title, first-release date (MusicBrainz's own rule),
           primary type, secondary types, cover flag (the Cover Art Archive has
           a front for one of its releases, ListenBrainz's caa_id), credit (only
           when it is not one artist under their own name: the tile shows none
           then); credit: the printed credits those rows use; artist_rg: one row
           per artist and group for every credit position (what `ngroups`
           counts), the special-purpose artists left out (the plugin never lists
           Various Artists).
  relmap   the community API's release map, whole: every release -> its group
           (today's `release` table maps a shared group's releases only), so an
           owned release tagged by its release id is placed on the first draw.
  barcode  docs/version-matching-plan.md layer A: a release's barcode (8-14
           digits, kept as a number) -> its group.
  links    layer A: a Qobuz / TIDAL / Deezer / Spotify album id, from
           MusicBrainz's release links -> its group.
  range    layer C: per group, the track count and length range of its official
           releases (data tracks left out; a release with a track of unknown
           length counts for the track range only).
barcode and links name their group by id, with an id -> gid table (rg_gid);
range is keyed by the group's gid (the plugin asks by gid). With lists in the
same pack, rg already holds the gid, so it takes a row for each group they name
instead of rg_gid.

Usage: python3 -I measure_extra.py fetch <dump> <dir>
       python3 -I measure_extra.py facts <dir> <facts.db>
       python3 -I measure_extra.py measure <base.db> <facts.db> <out dir> [manifest.json]
"""
import array, hashlib, json, os, re, shutil, sqlite3, subprocess, sys, time, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_dump  # noqa: E402  (BASE, get, CHUNK; its TABLES are today's pack's)
from build_pack import SPECIAL_GIDS, unesc  # noqa: E402

EXTRA_CORE = ['artist_credit', 'release_country', 'release_unknown_country', 'l_release_url', 'medium', 'track']
CAA_TAR = 'mbdump-cover-art-archive.tar.bz2'
CAA_TABLES = ['cover_art_archive.art_type', 'cover_art_archive.cover_art_type', 'cover_art_archive.cover_art']
CANCELLED = 6     # release_status id MusicBrainz's first-release date leaves out (CreateFunctions.sql)

# Service album pages as MusicBrainz stores release links; the svc numbers
# follow the pack's artist_url (1 Qobuz, 2 Deezer, 3 Discogs).
SVC_ALBUM = [
    (1, 'Qobuz', 'qobuz.com', re.compile(
        r'^https?://(?:www\.|open\.|play\.)?qobuz\.com/(?:[a-z]{2}-[a-z]{2}/)?album/(?:[^/?#]+/)?([0-9a-z]+)/?(?:[?#]|$)', re.I)),
    (2, 'Deezer', 'deezer.com', re.compile(
        r'^https?://(?:www\.)?deezer\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?album/(\d+)/?(?:[?#]|$)', re.I)),
    (4, 'TIDAL', 'tidal.com', re.compile(
        r'^https?://(?:www\.|listen\.)?tidal\.com/(?:browse/)?album/(\d+)/?(?:[?#]|$)', re.I)),
    (5, 'Spotify', 'spotify.com', re.compile(
        r'^https?://open\.spotify\.com/album/([0-9A-Za-z]{22})/?(?:[?#]|$)')),
]

TODAY = "today's pack"
T0 = time.time()


def log(msg):
    print(f'[{time.time() - T0:7.1f}s] {msg}', flush=True)


def mb(n):
    return f'{n / 1e6:,.1f} MB'


# --- fetch -------------------------------------------------------------------

def fetch(dump, out):
    # The main dump: today's pack's tables and the candidates', in one pass,
    # through fetch_dump's own streaming and MD5 check.
    fetch_dump.TABLES = fetch_dump.TABLES + [t for t in EXTRA_CORE if t not in fetch_dump.TABLES]
    fetch_dump.fetch(dump, out)
    # The cover flag's tables. Without them the flag is NULL, which costs the
    # same one header byte a row as a 0 or a 1, and the report says so.
    try:
        fetch_tar(dump, CAA_TAR, CAA_TABLES, os.path.join(out, 'caa'))
    except (SystemExit, OSError) as e:
        print(f'::warning::{CAA_TAR}: {e} - the cover flag is not measured', flush=True)


def fetch_tar(dump, tarball, tables, out):
    """fetch_dump.fetch for another tarball of the same dump."""
    want = None
    for line in fetch_dump.get(fetch_dump.BASE + dump + '/MD5SUMS').read().decode().splitlines():
        p = line.split()
        if len(p) >= 2 and p[-1].lstrip('*') == tarball:
            want = p[0].lower()
    if not want:
        raise SystemExit(f'MD5SUMS has no line for {tarball}')
    os.makedirs(out, exist_ok=True)
    bz = shutil.which('lbzip2') or shutil.which('pbzip2') or 'bzip2'
    unzip = subprocess.Popen([bz, '-dc'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    untar = subprocess.Popen(['tar', '--wildcards', '-xf', '-', '-C', out] + ['*mbdump/' + t for t in tables],
                             stdin=unzip.stdout)
    unzip.stdout.close()
    md5, n = hashlib.md5(), 0
    with fetch_dump.get(fetch_dump.BASE + dump + '/' + tarball) as r:
        while True:
            buf = r.read(fetch_dump.CHUNK)
            if not buf:
                break
            md5.update(buf)
            n += len(buf)
            unzip.stdin.write(buf)
    unzip.stdin.close()
    if unzip.wait() != 0 or untar.wait() != 0:
        raise SystemExit(f'{tarball}: {bz} or tar failed ({unzip.returncode}, {untar.returncode})')
    if md5.hexdigest() != want:
        raise SystemExit(f'{tarball} MD5 {md5.hexdigest()}, MD5SUMS says {want}')
    missing = [t for t in tables if not os.path.exists(os.path.join(out, 'mbdump', t))]
    if missing:
        raise SystemExit(f'missing from {tarball}: {missing}')
    print(f'{tarball}: {n / 1e6:,.0f} MB fetched, MD5 ok, {len(tables)} tables in {out}/mbdump', flush=True)


# --- facts: what every candidate needs, read from the dump once ---------------

def reader(src):
    def rows(table, ncols, exact=True):
        """PostgreSQL COPY text: tab separated, \\N = null, backslash escapes.
        exact=False: a table read only by its first ncols columns, which
        MusicBrainz's schema changes leave in place (they add columns at the
        end), so a newer column does not stop the run."""
        with open(os.path.join(src, table), encoding='utf-8', newline='\n') as f:
            for line in f:
                r = line.rstrip('\n').split('\t')
                if len(r) != ncols if exact else len(r) < ncols:
                    raise SystemExit(f'{table}: {len(r)} columns, expected {"" if exact else "at least "}{ncols}: '
                                     f'{line[:200]!r}')
                yield [None if c == '\\N' else (unesc(c) if '\\' in c else c) for c in r]
    return rows


class Batch:
    """Rows for one INSERT, written 100,000 at a time."""
    def __init__(self, c, sql, n=100000):
        self.c, self.sql, self.n, self.rows = c, sql, n, []

    def add(self, row):
        self.rows.append(row)
        if len(self.rows) >= self.n:
            self.flush()

    def flush(self):
        if self.rows:
            self.c.executemany(self.sql, self.rows)
            self.rows = []


def max_id(src, table):
    n = 0
    with open(os.path.join(src, table), encoding='utf-8', newline='\n') as f:
        for line in f:
            n = max(n, int(line.split('\t', 1)[0]))
    return n


def ints(code, n, fill=0):
    return array.array(code, [fill]) * (n + 1)


FACTS_SCHEMA = '''
CREATE TABLE stat (n INTEGER PRIMARY KEY, key TEXT NOT NULL, value);
CREATE TABLE f_rg (id INTEGER PRIMARY KEY, gid BLOB NOT NULL, title TEXT NOT NULL, date INTEGER, type INTEGER,
                   sec INTEGER, cover INTEGER, credit INTEGER, inlist INTEGER NOT NULL);
CREATE TABLE f_credit (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE f_artist_rg (artist INTEGER NOT NULL, rg INTEGER NOT NULL);
CREATE TABLE f_release (gid BLOB NOT NULL, rg INTEGER NOT NULL);
CREATE TABLE f_barcode (code INTEGER NOT NULL, rg INTEGER NOT NULL);
CREATE TABLE f_link (svc INTEGER NOT NULL, ext NOT NULL, rg INTEGER NOT NULL);
CREATE TABLE f_range (rg INTEGER PRIMARY KEY, nmin INTEGER NOT NULL, nmax INTEGER NOT NULL, lmin INTEGER, lmax INTEGER);
'''


def facts(src_dir, out):
    SRC = os.path.join(src_dir, 'mbdump')
    CAA = os.path.join(src_dir, 'caa', 'mbdump')
    rows = reader(SRC)
    stat = {}
    if os.path.exists(out):
        os.remove(out)
    c = sqlite3.connect(out, isolation_level=None)
    c.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;' + FACTS_SCHEMA)
    c.execute('BEGIN')

    nrel, nrg, nmed = max_id(SRC, 'release'), max_id(SRC, 'release_group'), max_id(SRC, 'medium')
    log(f'largest ids: release {nrel:,}, release group {nrg:,}, medium {nmed:,}')

    # --- releases: group, status, barcode; the whole release map --------------
    rel_rg, rel_st = ints('l', nrel), ints('b', nrel)
    status = {int(r[0]): r[1] for r in rows('release_status', 2, exact=False)}
    official = {i for i, n in status.items() if n.lower() == 'official'}
    bar_kept = bar_bad = 0
    out_rel = Batch(c, 'INSERT INTO f_release VALUES (?,?)')
    out_bar = Batch(c, 'INSERT INTO f_barcode VALUES (?,?)')
    for r in rows('release', 14):
        i, g = int(r[0]), int(r[4])
        rel_rg[i] = g
        rel_st[i] = int(r[5]) if r[5] else 0
        out_rel.add((uuid.UUID(r[1]).bytes, g))
        b = (r[9] or '').strip()
        if b:
            if b.isascii() and b.isdigit() and 8 <= len(b) <= 14:
                bar_kept += 1
                out_bar.add((int(b), g))
            else:
                bar_bad += 1
    out_rel.flush()
    out_bar.flush()
    stat['releases'] = c.execute('SELECT count(*) FROM f_release').fetchone()[0]
    stat['releases with a barcode of 8-14 digits'] = bar_kept
    stat['releases with another barcode (letters, too short or long), left out'] = bar_bad
    log(f"releases {stat['releases']:,}; barcodes kept {bar_kept:,}, left out {bar_bad:,}")

    # --- first-release dates, MusicBrainz's rule ------------------------------
    # A release's date: its earliest event, year, month, day each NULLS LAST,
    # a country event only with a date part, a cancelled release none; a
    # group's: the same over its releases (set_release_group_first_release_date).
    NY, NB = 0xFFFF, 0xFF
    ry, rm, rd = ints('H', nrel, NY), ints('B', nrel, NB), ints('B', nrel, NB)

    def event(rel, y, m, d):
        if rel_st[rel] == CANCELLED:
            return
        k = (NY if y is None else int(y), NB if m is None else int(m), NB if d is None else int(d))
        if k < (ry[rel], rm[rel], rd[rel]):
            ry[rel], rm[rel], rd[rel] = k
    for r in rows('release_country', 5, exact=False):
        if r[2] is not None or r[3] is not None or r[4] is not None:
            event(int(r[0]), r[2], r[3], r[4])
    for r in rows('release_unknown_country', 4, exact=False):
        event(int(r[0]), r[1], r[2], r[3])
    gy, gm, gd = ints('H', nrg, NY), ints('B', nrg, NB), ints('B', nrg, NB)
    for i in range(nrel + 1):
        g = rel_rg[i]
        if g and (ry[i], rm[i], rd[i]) < (gy[g], gm[g], gd[g]):
            gy[g], gm[g], gd[g] = ry[i], rm[i], rd[i]
    del ry, rm, rd
    log('first-release dates done')

    # --- cover flags: a release of the group has a Front image -----------------
    if all(os.path.exists(os.path.join(CAA, t)) for t in CAA_TABLES):
        caa = reader(CAA)
        front = {int(r[0]) for r in caa('cover_art_archive.art_type', 2, exact=False) if r[1] == 'Front'}
        fronts = {int(r[0]) for r in caa('cover_art_archive.cover_art_type', 2, exact=False) if int(r[1]) in front}
        cover = bytearray(nrg + 1)
        for r in caa('cover_art_archive.cover_art', 2, exact=False):
            if int(r[0]) in fronts:
                rel = int(r[1])
                if rel <= nrel and rel_rg[rel]:
                    cover[rel_rg[rel]] = 1
        del fronts
        stat['cover flags'] = 'from the Cover Art Archive tables'
    else:
        cover = None
        stat['cover flags'] = 'NOT MEASURED: no Cover Art Archive tables; the column is NULL (same 1 byte a row)'
    log(f"cover flags: {stat['cover flags']}")

    # --- layer C: track count and length per release, then per group ----------
    med_rel = ints('l', nmed)
    for r in rows('medium', 2, exact=False):
        med_rel[int(r[0])] = int(r[1])
    med_n, med_ms, med_unk = ints('l', nmed), ints('q', nmed), bytearray(nmed + 1)
    ntrk = ndata = nolen = 0
    with open(os.path.join(SRC, 'track'), encoding='utf-8', newline='\n') as f:
        for line in f:
            p = line.split('\t')
            if len(p) != 12:
                raise SystemExit(f'track: {len(p)} columns, expected 12: {line[:200]!r}')
            ntrk += 1
            if p[11][0] == 't':          # is_data_track: not audio
                ndata += 1
                continue
            m = int(p[3])
            med_n[m] += 1
            if p[8] == '\\N':
                med_unk[m] = 1
                nolen += 1
            else:
                med_ms[m] += int(p[8])
    stat['tracks'] = ntrk
    stat['  data tracks, left out'] = ndata
    stat['  audio tracks with no length'] = nolen
    log(f'tracks {ntrk:,} (data {ndata:,}, no length {nolen:,})')
    rel_n, rel_ms = ints('l', nrel), ints('q', nrel)
    rel_cnt_unk, rel_len_unk = bytearray(nrel + 1), bytearray(nrel + 1)
    for m in range(nmed + 1):
        rel = med_rel[m]
        if not rel:
            continue
        if med_n[m] == 0:                # a medium with no tracklist: count unknown
            rel_cnt_unk[rel] = 1
        rel_n[rel] += med_n[m]
        rel_ms[rel] += med_ms[m]
        rel_len_unk[rel] |= med_unk[m]
    del med_rel, med_n, med_ms, med_unk
    nmin, nmax = ints('l', nrg), ints('l', nrg)
    lmin, lmax = ints('q', nrg, -1), ints('q', nrg, -1)
    for i in range(nrel + 1):
        g = rel_rg[i]
        if not g or rel_n[i] == 0 or rel_cnt_unk[i] or (rel_st[i] and rel_st[i] not in official):
            continue
        n = rel_n[i]
        if not nmin[g] or n < nmin[g]:
            nmin[g] = n
        if n > nmax[g]:
            nmax[g] = n
        if not rel_len_unk[i]:
            s = (rel_ms[i] + 500) // 1000
            if lmin[g] < 0 or s < lmin[g]:
                lmin[g] = s
            if s > lmax[g]:
                lmax[g] = s
    del rel_n, rel_ms, rel_cnt_unk, rel_len_unk
    c.executemany('INSERT INTO f_range VALUES (?,?,?,?,?)',
                  ((g, nmin[g], nmax[g], lmin[g] if lmin[g] >= 0 else None, lmax[g] if lmax[g] >= 0 else None)
                   for g in range(nrg + 1) if nmin[g]))
    del nmin, nmax, lmin, lmax
    stat['groups with a track range (an official release with a whole tracklist)'] = \
        c.execute('SELECT count(*) FROM f_range').fetchone()[0]
    stat['  of them with a length range too'] = c.execute('SELECT count(*) FROM f_range WHERE lmin IS NOT NULL').fetchone()[0]
    log('track ranges done')

    # --- layer A: service album ids from release links -------------------------
    svc_of, per_svc = {}, {s: 0 for s, *_ in SVC_ALBUM}
    for r in rows('url', 5):
        u = r[2]
        for s, _, host, rx in SVC_ALBUM:
            if host in u:
                m = rx.match(u)
                if m:
                    x = m.group(1) if s == 5 else m.group(1).lower()
                    svc_of[int(r[0])] = (s, int(x) if x.isdigit() and x[0] != '0' else x)
                break
    nlinks = 0
    out_link = Batch(c, 'INSERT INTO f_link VALUES (?,?,?)')
    for r in rows('l_release_url', 4, exact=False):
        x = svc_of.get(int(r[3]))
        if x:
            rel = int(r[2])
            g = rel_rg[rel] if rel <= nrel else 0
            if g:
                out_link.add((x[0], x[1], g))
                per_svc[x[0]] += 1
                nlinks += 1
    out_link.flush()
    del svc_of
    stat['release links to a service album page'] = nlinks
    for s, name, *_ in SVC_ALBUM:
        stat[f'  {name}'] = per_svc[s]
    log(f'service album links {nlinks:,}')
    del rel_rg, rel_st

    # --- groups, credits, lists ------------------------------------------------
    sec = {}
    for r in rows('release_group_secondary_type_join', 3):
        sec[int(r[0])] = sec.get(int(r[0]), 0) | (1 << int(r[1]))
    used = {int(r[3]) for r in rows('release_group', 8)}
    ac_art, ac_first = {}, {}
    for r in rows('artist_credit_name', 5):
        ac = int(r[0])
        if ac in used:
            ac_art.setdefault(ac, []).append(int(r[2]))
            if int(r[1]) == 0:
                ac_first[ac] = (r[3], r[4] or '')
    single = {ac_art[ac][0] for ac in ac_art if len(ac_art[ac]) == 1}
    special, aname = set(), {}
    for r in rows('artist', 19):
        i = int(r[0])
        if r[1] in SPECIAL_GIDS:
            special.add(i)
        if i in single:
            aname[i] = r[2]
    del single
    # One artist under their own name, nothing after it: the tile shows no
    # credit, so the row needs none.
    plain = {ac for ac, a in ac_art.items()
             if len(a) == 1 and ac_first.get(ac) == (aname.get(a[0]), '')}
    del aname, ac_first
    want_credit, pairs, nlisted = set(), [], 0

    def groups():
        nonlocal nlisted
        for r in rows('release_group', 8):
            i, ac = int(r[0]), int(r[3])
            who = set(ac_art.get(ac, ())) - special
            for a in who:
                pairs.append((a, i))
            nlisted += 1 if who else 0
            credit = None if ac in plain else ac
            if credit is not None:       # every group's: `all` also holds groups no list has
                want_credit.add(ac)
            date = None if gy[i] == NY else gy[i] * 10000 + (0 if gm[i] == NB else gm[i]) * 100 + (0 if gd[i] == NB else gd[i])
            yield (i, uuid.UUID(r[1]).bytes, r[2], date, int(r[4]) if r[4] else None, sec.get(i),
                   (cover[i] if cover is not None else None), credit, 1 if who else 0)
    c.executemany('INSERT INTO f_rg VALUES (?,?,?,?,?,?,?,?,?)', groups())
    pairs.sort()
    c.executemany('INSERT INTO f_artist_rg VALUES (?,?)', pairs)
    stat['release groups'] = c.execute('SELECT count(*) FROM f_rg').fetchone()[0]
    stat['  in an artist list (credited to an artist other than a special-purpose one)'] = nlisted
    stat['  with a front cover'] = c.execute('SELECT count(*) FROM f_rg WHERE cover = 1').fetchone()[0]
    stat['  with a credit stored (not one artist under their own name)'] = \
        c.execute('SELECT count(*) FROM f_rg WHERE credit IS NOT NULL AND inlist').fetchone()[0]
    stat['artist-group pairs (every credit position, special-purpose artists left out)'] = len(pairs)
    stat['artists with a list'] = len({a for a, _ in pairs})
    del pairs, ac_art, plain, sec, gy, gm, gd, cover
    # The printed credits the listed groups use.
    c.executemany('INSERT INTO f_credit VALUES (?,?)',
                  ((int(r[0]), r[1]) for r in rows('artist_credit', 2, exact=False) if int(r[0]) in want_credit))
    stat['printed credits the groups use (one artist under their own name left out)'] = \
        c.execute('SELECT count(*) FROM f_credit').fetchone()[0]
    c.executemany('INSERT INTO stat VALUES (?,?,?)', ((i, k, v) for i, (k, v) in enumerate(stat.items())))
    c.execute('COMMIT')
    c.close()
    log(f'facts done: {os.path.getsize(out):,} bytes')
    for k, v in stat.items():
        print(f'  {k:78} {v:>12,}' if isinstance(v, int) else f'  {k}: {v}')


# --- the variants --------------------------------------------------------------

VARIANTS = [
    ('lists', 'group lists (item 3)', ['lists']),
    ('lists+relmap', 'group lists + the whole release map', ['lists', 'relmap']),
    ('barcode', 'barcode map (layer A)', ['barcode']),
    ('links', 'service album ids (layer A)', ['links']),
    ('barcode+links', 'layer A whole (barcodes + album ids)', ['barcode', 'links']),
    ('range', 'track count + length ranges (layer C)', ['range']),
    ('all', 'all of them', ['lists', 'relmap', 'barcode', 'links', 'range']),
]

NEW_TABLES = {
    'lists': ['rg', 'credit', 'artist_rg'],
    'relmap': [],                 # today's `release` table, every release in it
    'barcode': ['barcode'],
    'links': ['svc_album'],
    'range': ['rg_range'],
}


def expected(parts):
    """(the tables a variant adds, the tables of today's pack it grows)."""
    new = {t for p in parts for t in NEW_TABLES[p]}
    if ('barcode' in parts or 'links' in parts) and 'lists' not in parts:
        new.add('rg_gid')
    return new, ({'release'} if 'relmap' in parts else set())


def variant_sql(parts):
    s = []
    if 'lists' in parts:
        s += ['CREATE TABLE rg (id INTEGER PRIMARY KEY, gid BLOB NOT NULL, title TEXT NOT NULL, date INTEGER, '
              'type INTEGER, sec INTEGER, cover INTEGER, credit INTEGER)',
              'CREATE TABLE credit (id INTEGER PRIMARY KEY, name TEXT NOT NULL)',
              'CREATE TABLE artist_rg (artist INTEGER NOT NULL, rg INTEGER NOT NULL, PRIMARY KEY (artist, rg)) WITHOUT ROWID',
              'INSERT INTO rg SELECT id, gid, title, date, type, sec, cover, credit FROM f.f_rg WHERE inlist ORDER BY id',
              'INSERT INTO artist_rg SELECT artist, rg FROM f.f_artist_rg ORDER BY artist, rg']
    if 'relmap' in parts:
        s.append('INSERT OR IGNORE INTO release (gid, rg, type, sec) SELECT r.gid, g.gid, g.type, g.sec '
                 'FROM f.f_release r JOIN f.f_rg g ON g.id = r.rg ORDER BY r.gid')
    if 'barcode' in parts:
        s += ['CREATE TABLE barcode (code INTEGER NOT NULL, rg INTEGER NOT NULL, PRIMARY KEY (code, rg)) WITHOUT ROWID',
              'INSERT OR IGNORE INTO barcode SELECT code, rg FROM f.f_barcode ORDER BY code, rg']
    if 'links' in parts:
        s += ['CREATE TABLE svc_album (svc INTEGER NOT NULL, ext NOT NULL, rg INTEGER NOT NULL, '
              'PRIMARY KEY (svc, ext, rg)) WITHOUT ROWID',
              'INSERT OR IGNORE INTO svc_album SELECT svc, ext, rg FROM f.f_link ORDER BY svc, ext, rg']
    if 'range' in parts:
        s += ['CREATE TABLE rg_range (gid BLOB PRIMARY KEY, nmin INTEGER NOT NULL, nmax INTEGER NOT NULL, '
              'lmin INTEGER, lmax INTEGER) WITHOUT ROWID',
              'INSERT INTO rg_range SELECT g.gid, x.nmin, x.nmax, x.lmin, x.lmax '
              'FROM f.f_range x JOIN f.f_rg g ON g.id = x.rg ORDER BY g.gid']
    named = ' UNION '.join(q for p, q in (('barcode', 'SELECT rg FROM barcode'), ('links', 'SELECT rg FROM svc_album'))
                           if p in parts)
    if named and 'lists' in parts:
        s.append('INSERT OR IGNORE INTO rg SELECT id, gid, title, date, type, sec, cover, credit FROM f.f_rg '
                 f'WHERE id IN ({named}) ORDER BY id')
    elif named:
        s += ['CREATE TABLE rg_gid (id INTEGER PRIMARY KEY, gid BLOB NOT NULL)',
              f'INSERT INTO rg_gid SELECT id, gid FROM f.f_rg WHERE id IN ({named}) ORDER BY id']
    if 'lists' in parts:
        s.append('INSERT INTO credit SELECT id, name FROM f.f_credit WHERE id IN (SELECT credit FROM rg) ORDER BY id')
    return s


def tables(c):
    return {t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite%'")}


def btrees(c):
    """{B-tree name: bytes on disk}, or None without SQLite's dbstat table."""
    try:
        return dict(c.execute('SELECT name, sum(pgsize) FROM dbstat GROUP BY name'))
    except sqlite3.OperationalError:
        return None


def build_variant(base, facts_db, path, parts):
    shutil.copyfile(base, path)
    c = sqlite3.connect(path, isolation_level=None)
    c.execute('PRAGMA journal_mode=OFF')
    c.execute('PRAGMA synchronous=OFF')
    c.execute('ATTACH ? AS f', (facts_db,))
    c.execute('BEGIN')
    for sql in variant_sql(parts):
        c.execute(sql)
    c.execute('COMMIT')
    c.execute('DETACH f')
    c.execute('VACUUM')
    ok = c.execute('PRAGMA quick_check').fetchone()[0]
    if ok != 'ok':
        raise SystemExit(f'{path}: quick_check {ok}')
    info = {'tables': tables(c), 'btrees': btrees(c),
            'rows': {t: c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables(c)}}
    c.close()
    return info


def gzip_start(path):
    out = open(path + '.gz', 'wb')
    return subprocess.Popen(['gzip', '-9', '-n', '-c', path], stdout=out), out


def measure(base, facts_db, outdir, manifest=None, keep=False):
    """Builds every variant; prints the report; returns {name: {size, gz, info, path}}."""
    os.makedirs(outdir, exist_ok=True)
    res = {}
    fc = sqlite3.connect(f'file:{facts_db}?mode=ro', uri=True)
    stats = fc.execute('SELECT key, value FROM stat ORDER BY n').fetchall()
    fc.close()
    bc = sqlite3.connect(f'file:{base}?mode=ro', uri=True)
    meta = dict(bc.execute('SELECT key, value FROM meta'))
    binfo = {'tables': tables(bc), 'btrees': btrees(bc),
             'rows': {t: bc.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables(bc)}}
    bc.close()
    running = []

    def reap(block):
        for job in list(running):
            name, path, p, out = job
            if block or p.poll() is not None:
                if p.wait() != 0:
                    raise SystemExit(f'gzip of {path} failed')
                out.close()
                res[name]['gz'] = os.path.getsize(path + '.gz')
                if not keep and name != 'base':
                    os.remove(path)
                if not keep:
                    os.remove(path + '.gz')
                running.remove(job)

    res['base'] = {'size': os.path.getsize(base), 'info': binfo, 'path': base}
    running.append(('base', base) + gzip_start(base))
    for name, label, parts in VARIANTS:
        path = os.path.join(outdir, f'v-{name}.db')
        log(f'building {name}')
        res[name] = {'size': 0, 'info': build_variant(base, facts_db, path, parts), 'path': path}
        res[name]['size'] = os.path.getsize(path)
        log(f'  {name}: {res[name]["size"]:,} bytes')
        running.append((name, path) + gzip_start(path))
        while len(running) > 3:
            time.sleep(1)
            reap(False)
    reap(True)
    report(res, meta, stats, manifest)
    return res


def report(res, meta, stats, manifest):
    dump = meta.get('dump', '?')
    b = res['base']
    print(f'\n=== What Discography\'s candidate tables add to the pack: dump {dump}')
    print(f"today's pack: builder/build_pack.py format {meta.get('format')}, unchanged, built in this run "
          f'from the same dump; every variant is a copy of it plus the candidate, VACUUMed, gzip -9 -n.')
    pub = None
    if manifest and os.path.exists(manifest):
        with open(manifest) as f:
            m = json.load(f)
        if (m.get('pack') or {}).get('dump') == dump:
            pub = m['pack']
    if pub:
        print(f"published pack-{dump} ({os.path.basename(manifest)}): {pub['db_size']:,} bytes on disk, "
              f"{pub['size']:,} gzipped; this run's: {b['size']:,} and {b['gz']:,}")
    else:
        print(f'(pack-{dump} is not in this checkout\'s manifest: no cross-check with a published pack)')
    print(f"\n{'variant':42} {'on disk':>11} {'gzipped':>11} {'added on disk':>16} {'added gzipped':>16}")
    print(f"{TODAY:42} {mb(b['size']):>11} {mb(b['gz']):>11}")
    for name, label, _ in VARIANTS:
        v = res[name]
        print(f"{label:42} {mb(v['size']):>11} {mb(v['gz']):>11} "
              f"{'+' + mb(v['size'] - b['size']):>11} {100 * (v['size'] - b['size']) / b['size']:3.0f}% "
              f"{'+' + mb(v['gz'] - b['gz']):>11} {100 * (v['gz'] - b['gz']) / b['gz']:3.0f}%")
    print('\nbytes exactly:')
    for name in ['base'] + [n for n, *_ in VARIANTS]:
        print(f"  {name:16} {res[name]['size']:>15,} on disk {res[name]['gz']:>15,} gzipped")

    print('\nthe tables each variant adds or grows (on disk, from dbstat, and rows):')
    bad = 0
    for name, label, parts in VARIANTS:
        info = res[name]['info']
        new = sorted(info['tables'] - b['info']['tables'])
        grown = [t for t in sorted(b['info']['tables']) if info['rows'].get(t) != b['info']['rows'].get(t)]
        print(f'  {label}:')
        for t in new + grown:
            disk = ''
            if info['btrees'] is not None:
                now = info['btrees'].get(t, 0)
                was = (b['info']['btrees'] or {}).get(t, 0)
                disk = f"{mb(now):>11}" + (f' (was {mb(was).strip()})' if t in grown else '')
            rows = f"{info['rows'][t]:,} rows" + (f" (was {b['info']['rows'][t]:,})" if t in grown else '')
            print(f'    {t:12} {disk} {rows}')
        if info['btrees'] is None:
            print('    (no dbstat in this SQLite: table sizes not shown)')
        want_new, want_grown = expected(parts)
        if set(new) != want_new or not set(grown) <= want_grown or (b['info']['tables'] - info['tables']):
            bad += 1
            print(f'    UNEXPECTED: added {new} (expected {sorted(want_new)}), grew {grown} '
                  f'(expected at most {sorted(want_grown)}), lost {sorted(b["info"]["tables"] - info["tables"])}')
    print('  self-check: ' + (f'every variant is {TODAY} plus exactly its own tables' if not bad
                              else f'{bad} variant(s) UNEXPECTED, see above'))

    print('\nwhat the dump holds for them:')
    for k, v in stats:
        print(f'  {k:78} {v:>12,}' if isinstance(v, int) else f'  {k}: {v}')


def main():
    a = sys.argv[1:]
    if len(a) == 3 and a[0] == 'fetch':
        fetch(a[1], a[2])
    elif len(a) == 3 and a[0] == 'facts':
        facts(a[1], a[2])
    elif len(a) in (4, 5) and a[0] == 'measure':
        measure(a[1], a[2], a[3], a[4] if len(a) == 5 else None)
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
