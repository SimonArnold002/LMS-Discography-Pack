"""The hosted pack (Cloudflare D1): today's pack plus the extras that stop
Discography's remaining MusicBrainz calls, measured on the GitHub runner from the
two dumps MusicBrainz serves, and checked so that nothing is lost.

The plan is LMS-Discography's docs/hosted-pack-plan.md (sections 10 and 11).
Simon, 2026-11-01: "fully to spec ... tested as a local pack before we move it to
cloudflare", "We really would not be rebuilding the DB more than once ... we need
to set what goes into it now", "lets look to add everything from the last list
and we test it all to see if we lose anything before mving to cloudflare", "if we
do we go back to MB for the problematic ones". Nothing here publishes anything,
and nothing that reads the dump runs on Simon's Mac (the tests use the made-up
dump in tests/fixture.py).

THE CONTENT, in today's table layout (the local pack's tables, read with Pack.pm's
own SQL; D1 counts a written row per row AND per index entry):
  today's pack           format 3, as published
  group lists            rg, credit, artist_rg (measure_extra.py `lists`): every
                         group an artist is credited on, any position, for the
                         untagged library artist's lookup (C2); NOT the page's
                         first list, which stays ListenBrainz's and the
                         community API's
  whole release map      every release in `release` (`relmap`)
  barcodes, album ids    barcode, svc_album (`barcode`, `links`), for the new matcher
  alias / old-name keys  alias_name (key, artist): every alias whose key (DSC's own
                         _nameKey, builder/namekeys.pl) is not its artist's own
                         name key
Out (Simon, 2026-11-01): layer C, the track ranges (the track match stays on
ListenBrainz); a typo index (the closest names stay MusicBrainz's).

THE CHECKS
  1. today's lookups (artistRead, collaboration, groupCount, sameName, groups,
     releases): the hosted reader (worker/reader.mjs) on the file against
     Pack.pm on today's pack (verify/hosted_pack_answers.pl), exactly; for
     `releases`, every entry Pack.pm gives, the whole map's others counted;
  2. the new lookups (artistList, aliasName, the whole map's releases, barcode,
     svcAlbum): against answers built here from the dump's own facts and today's
     pack, by other SQL (`truth`);
  3. against MusicBrainz itself (`mbcheck`, a sample, asked on the Mac of
     Simon's local mirror, which the runner cannot reach; the local test): the
     two MusicBrainz lookups the extras would replace, the alias search (a name
     that is no act's own name) and the untagged library artist's title search
     (C2), against what the file answers. Where the file loses an answer, that
     lookup stays on MusicBrainz (Simon).

Sub-commands (the workflow runs them in turn and deletes what it is done with):
  aliases  <base.db> <dsc checkout> <aliases.tsv>
  narrow   <base.db> <facts.db> <aliases.tsv> <out.db>
  delta    <old.db> <new.db> <delta.jsonl.gz>     prints a JSON summary; proves old + delta == new
  grow     <old.db> <delta.jsonl.gz> <grown.db>   applies it in place, as the Worker will
  measure  <db> [gz]                               prints a JSON summary of the file
  dumps                                            the two newest dumps MusicBrainz serves
  queries  <hosted.db> <queries.jsonl> [every]    the lookups to check (also the local test's)
  truth    <base.db> <facts.db> <aliases.tsv> <queries.jsonl> <truth.jsonl>
  pages    <hosted.db> <facts.db> <pages.json>    the page sample (its groups from the facts)
  compare  <queries.jsonl> <Pack.pm answers> <truth.jsonl|-> <answers.jsonl>   exit 1 on a difference
  mbcheck  <hosted.db> <aliases.tsv> <dsc checkout> <names> <titles> <server /ws/2/ url> <out.json>
           asks MusicBrainz (the network); prints the readable result
  run      <base A> <facts A> <aliases A> <base B> <facts B> <aliases B> <dsc> <work> <every>
           every step above but mbcheck; prints the report
  report   <results.json>

THE LOCAL TEST (Simon's Mac; built files only, nothing from the dump): the run's
artifact holds dump B's file, its aliases.tsv, pages.json, and the run's
queries.jsonl and truth.jsonl (the dump's own answers, built on the runner);
today's published pack of the same dump is Pack.pm's side. <dsc> holds the
Sources.pm the file was built with (DSC_REF's; mbcheck refuses another):
  perl verify/hosted_pack_answers.pl ../LMS-Discography pack-<B>.db queries.jsonl pack.jsonl
  node worker/answers.mjs hosted-B.db queries.jsonl h.jsonl
  hosted.py compare queries.jsonl pack.jsonl truth.jsonl h.jsonl
  node worker/pages.mjs hosted-B.db pages.json
  hosted.py mbcheck hosted-B.db aliases-B.tsv <dsc> 2000 2000 http://plex:5000/ws/2/ mbcheck.json
"""
import base64, gzip, json, os, shutil, sqlite3, subprocess, sys, tempfile, time, urllib.parse, urllib.request, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_extra  # noqa: E402  (variant_sql: the candidate tables, as measured 2026-10-31)

HOSTED_FORMAT = 'hosted-1'
PARTS = ['lists', 'relmap', 'barcode', 'links']   # measure_extra.py's; today's pack is the rest
# The index entries D1 also writes, per table (format 3's three indexes; the
# extras' tables have none).
NARROW_INDEXES = {'artist': 1, 'rgx': 1, 'artist_rel': 1}
MB_GAP = 1.1             # musicbrainz.org's one request a second, as Discography paces it (a mirror: none)

T0 = time.time()


def log(msg):
    print(f'[{time.time() - T0:7.1f}s] {msg}', file=sys.stderr, flush=True)


def hx(b):
    return b.hex() if b is not None else None


def jdump(x):
    return json.dumps(x, ensure_ascii=False, separators=(',', ':'))


def _keys(dsc, texts):
    """DSC's own _nameKey of each text (builder/namekeys.pl), in order; '' where
    it gives none."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'namekeys.pl')
    clean = [t.replace('\t', ' ').replace('\n', ' ') for t in texts]
    p = subprocess.run(['perl', script, dsc], input=''.join(f'{i}\t{t}\n' for i, t in enumerate(clean)).encode('utf-8'),
                       capture_output=True, check=True)
    out = [''] * len(texts)
    for line in p.stdout.decode('utf-8').splitlines():
        i, k = line.split('\t', 1)
        out[int(i)] = k
    return out


# --- the alias / old-name keys -------------------------------------------------

def aliases(base, dsc, out):
    """(key, artist, name) for every alias whose key is not its artist's own name
    key, keyed by DSC's own _nameKey, sorted by key and artist; `name` is the
    alias's own text (the first by code point when several give the key), for
    asking MusicBrainz in mbcheck."""
    c = sqlite3.connect(f'file:{base}?mode=ro', uri=True)
    rows = c.execute('SELECT DISTINCT artist, name FROM artist_alias ORDER BY artist, name').fetchall()
    own = set(c.execute('SELECT key, artist FROM artist_name'))
    c.close()
    got = {}
    for (a, n), k in zip(rows, _keys(dsc, [n for _, n in rows])):
        if k and (k, a) not in own and (k, a) not in got:
            got[k, a] = n
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        for (k, a), n in sorted(got.items()):
            f.write(f"{a}\t{k}\t{n.replace(chr(9), ' ').replace(chr(10), ' ')}\n")
    log(f'alias keys: {len(rows):,} aliases, {len(got):,} keys that are not the artist\'s own name key')
    return len(got)


def read_aliases(path):
    """(key, artist, name) per line of aliases.tsv."""
    with open(path, encoding='utf-8', newline='\n') as f:
        for line in f:
            a, k, n = line.rstrip('\n').split('\t', 2)
            yield k, int(a), n


# --- the file ------------------------------------------------------------------

def narrow(base, facts_db, alias_tsv, out):
    """Today's pack + the extras (the module's docstring), VACUUMed."""
    if os.path.exists(out):
        os.remove(out)
    shutil.copyfile(base, out)
    c = sqlite3.connect(out, isolation_level=None)
    c.execute('PRAGMA journal_mode=OFF')
    c.execute('PRAGMA synchronous=OFF')
    c.execute('ATTACH ? AS f', (facts_db,))
    c.execute('BEGIN')
    for sql in measure_extra.variant_sql(PARTS):
        c.execute(sql)
    c.execute('CREATE TABLE alias_name (key TEXT NOT NULL, artist INTEGER NOT NULL, '
              'PRIMARY KEY (key, artist)) WITHOUT ROWID')
    c.executemany('INSERT INTO alias_name VALUES (?,?)', ((k, a) for k, a, _ in read_aliases(alias_tsv)))
    c.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)',
                  [('hosted_format', HOSTED_FORMAT), ('hosted_layout', 'narrow')])
    c.execute('COMMIT')
    c.execute('DETACH f')
    c.execute('VACUUM')
    ok = c.execute('PRAGMA quick_check').fetchone()[0]
    c.close()
    if ok != 'ok':
        raise SystemExit(f'{out}: quick_check {ok}')
    log(f'hosted file: {os.path.getsize(out):,} bytes')


# --- the delta, as the Worker will receive it ----------------------------------------------

def _tables(c, schema='main'):
    out = {}
    for (t,) in c.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table' "
                          "AND name NOT LIKE 'sqlite%' ORDER BY name"):
        info = c.execute(f'PRAGMA {schema}.table_info("{t}")').fetchall()
        cols = [r[1] for r in info]
        pk = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
        if not pk:
            raise SystemExit(f'{t}: no primary key')
        out[t] = (cols, pk)
    return out


def _q(n):
    return '"' + n.replace('"', '""') + '"'


def _enc(v):
    return {'b': base64.b64encode(v).decode()} if isinstance(v, bytes) else v


def _dec(v):
    return base64.b64decode(v['b']) if isinstance(v, dict) else v


def delta(old, new, out):
    """Every row of new that old lacks or holds differently (a put, whole), and
    every primary key of old that new lacks (a del), per table, meta included,
    as gzipped JSON lines: {"t": table, "p": [values]} / {"t": table, "d": [key]};
    BLOBs as {"b": base64}. Proven: applied to a copy of old it gives new."""
    c = sqlite3.connect(':memory:')
    c.execute('ATTACH ? AS o', (old,))
    c.execute('ATTACH ? AS n', (new,))
    to, tn = _tables(c, 'o'), _tables(c, 'n')
    if to != tn:
        raise SystemExit('the two files differ in tables or columns: no delta')
    counts = {}
    with gzip.open(out, 'wt', encoding='utf-8', compresslevel=9) as f:
        for t, (cols, pk) in tn.items():
            cl, pl = ', '.join(map(_q, cols)), ', '.join(map(_q, pk))
            np_ = nd = 0
            for r in c.execute(f'SELECT {pl} FROM o.{_q(t)} EXCEPT SELECT {pl} FROM n.{_q(t)} ORDER BY {pl}'):
                f.write(jdump({'t': t, 'd': [_enc(x) for x in r]}) + '\n')
                nd += 1
            for r in c.execute(f'SELECT {cl} FROM n.{_q(t)} EXCEPT SELECT {cl} FROM o.{_q(t)} ORDER BY {pl}'):
                f.write(jdump({'t': t, 'p': [_enc(x) for x in r]}) + '\n')
                np_ += 1
            counts[t] = [np_, nd]
    c.close()
    with tempfile.TemporaryDirectory() as d:
        g = os.path.join(d, 'grown.db')
        info = grow(old, out, g)
        bad = same(g, new)
    if bad:
        raise SystemExit(f'delta {old} -> {new} FAILED its proof: {bad}')
    return {'bytes_gz': os.path.getsize(out), 'tables': counts, 'apply': info}


def grow(old, delta_path, out):
    """Apply a delta to a copy of old IN PLACE, the Worker's way: per line, a
    DELETE by key or an INSERT ... ON CONFLICT DO UPDATE (an upsert), no
    VACUUM. Counts the rows D1 would write: new rows, replaced rows, deleted
    rows, and (narrow) the index entries those touch."""
    shutil.copyfile(old, out)
    c = sqlite3.connect(out, isolation_level=None)
    tabs = _tables(c)
    narrow_file = dict(c.execute('SELECT key, value FROM meta')).get('hosted_layout') == 'narrow'
    stmts = {}
    for t, (cols, pk) in tabs.items():
        rest = [x for x in cols if x not in pk]
        up = ('DO UPDATE SET ' + ', '.join(f'{_q(x)} = excluded.{_q(x)}' for x in rest)) if rest else 'DO NOTHING'
        stmts[t] = (f'INSERT INTO {_q(t)} ({", ".join(map(_q, cols))}) VALUES ({", ".join("?" * len(cols))}) '
                    f'ON CONFLICT ({", ".join(map(_q, pk))}) {up}',
                    f'DELETE FROM {_q(t)} WHERE ' + ' AND '.join(f'{_q(x)} = ?' for x in pk),
                    f'SELECT 1 FROM {_q(t)} WHERE ' + ' AND '.join(f'{_q(x)} = ?' for x in pk),
                    [cols.index(x) for x in pk])
    n = {'new': 0, 'replaced': 0, 'deleted': 0, 'index': 0}
    c.execute('BEGIN')
    with gzip.open(delta_path, 'rt', encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            t = r['t']
            ins, dele, exists, pki = stmts[t]
            idx = NARROW_INDEXES.get(t, 0) if narrow_file else 0
            if 'd' in r:
                c.execute(dele, [_dec(x) for x in r['d']])
                n['deleted'] += 1
            else:
                vals = [_dec(x) for x in r['p']]
                had = c.execute(exists, [vals[i] for i in pki]).fetchone()
                c.execute(ins, vals)
                n['replaced' if had else 'new'] += 1
            n['index'] += idx
    c.execute('COMMIT')
    pages, free, psize = (c.execute(f'PRAGMA {p}').fetchone()[0] for p in ('page_count', 'freelist_count', 'page_size'))
    c.close()
    n['rows_written'] = n['new'] + n['replaced'] + n['deleted'] + n['index']
    n.update({'size': os.path.getsize(out), 'pages': pages, 'free_pages': free, 'page_size': psize})
    return n


def same(a, b):
    """'' when every table of a equals b's row for row."""
    c = sqlite3.connect(':memory:')
    c.execute('ATTACH ? AS a', (a,))
    c.execute('ATTACH ? AS b', (b,))
    ta, tb = _tables(c, 'a'), _tables(c, 'b')
    if ta != tb:
        return f'tables differ: {sorted(set(ta) ^ set(tb))}'
    for t, (cols, _) in ta.items():
        cl = ', '.join(map(_q, cols))
        for x, y in (('a', 'b'), ('b', 'a')):
            k = c.execute(f'SELECT count(*) FROM (SELECT {cl} FROM {x}.{_q(t)} EXCEPT SELECT {cl} FROM {y}.{_q(t)})').fetchone()[0]
            if k:
                return f'{t}: {k} row(s) in {x} not in {y}'
    c.close()
    return ''


# --- what a file holds ------------------------------------------------------------------

def measure(path, gz=False):
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    meta = dict(c.execute('SELECT key, value FROM meta'))
    try:
        disk = dict(c.execute('SELECT name, sum(pgsize) FROM dbstat GROUP BY name'))
    except sqlite3.OperationalError:
        disk = {}
    idx = {}
    for t, i in c.execute("SELECT tbl_name, name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"):
        idx.setdefault(t, []).append(i)
    tables = {}
    for t, (cols, _) in _tables(c).items():
        rows = c.execute(f'SELECT count(*) FROM {_q(t)}').fetchone()[0]
        x = {'rows': rows, 'index_rows': rows * len(idx.get(t, [])),
             'disk': disk.get(t, 0) + sum(disk.get(i, 0) for i in idx.get(t, []))}
        tables[t] = x
    pages, free, psize = (c.execute(f'PRAGMA {p}').fetchone()[0] for p in ('page_count', 'freelist_count', 'page_size'))
    c.close()
    out = {'path': os.path.basename(path), 'size': os.path.getsize(path), 'pages': pages, 'free_pages': free,
           'page_size': psize, 'layout': meta.get('hosted_layout'),
           'dump': meta.get('dump'), 'tables': tables,
           'rows': sum(x['rows'] for x in tables.values()),
           'index_rows': sum(x['index_rows'] for x in tables.values())}
    if gz:
        r = subprocess.run(['gzip', '-9', '-n', '-c', path], stdout=subprocess.PIPE, check=True)
        out['gz'] = len(r.stdout)
    return out


# --- the checks: what to ask, and the answers to hold the file's against ------

def _uu(b):
    return str(uuid.UUID(bytes=b))


def _raw(mbid):
    """An mbid's 16 bytes, or None for anything the reader would not take
    (reader.mjs hexOf: 32 hex digits, dashes optional)."""
    if not isinstance(mbid, str):
        return None
    h = mbid.replace('-', '').lower()
    if len(h) != 32 or any(ch not in '0123456789abcdef' for ch in h):
        return None
    return bytes.fromhex(h)


# The page sample (the plan's named artists, by DSC's name key), each act of the
# name up to three, the most groups first.
NAMED = ['bruce springsteen', 'johann sebastian bach', 'bob dylan', 'madness', 'miles davis', 'radiohead',
         'jandek', 'centro matic']


def queries(hosted_db, out, every=1, named=None):
    """Every lookup to check, as JSON lines {"f": lookup, "a": [args]}: every
    `every`-th artist, redirect, name key, alias key, group, release, barcode
    and service album the file holds, the named artists, and misses."""
    import random
    c = sqlite3.connect(f'file:{hosted_db}?mode=ro', uri=True)
    rnd = random.Random(20261101)

    def miss(n):
        return [str(uuid.UUID(int=rnd.getrandbits(128), version=4)) for _ in range(n)]

    def nth(sql):
        return [r for i, r in enumerate(c.execute(sql)) if i % every == 0]
    named_keys = list(named or NAMED)
    named = [g for (g,) in c.execute(f'SELECT a.gid FROM artist_name n JOIN artist a ON a.id = n.artist '
                                     f'WHERE n.key IN ({",".join("?" * len(named_keys))})', named_keys)]
    arts = [_uu(g) for (g,) in nth('SELECT gid FROM artist ORDER BY id')] + [_uu(g) for g in named] + \
           [_uu(g) for (g,) in nth('SELECT gid FROM artist_redirect ORDER BY gid')]
    arts += miss(max(20, len(arts) // 50))
    keys = [k for (k,) in nth('SELECT DISTINCT key FROM artist_name ORDER BY key')] + named_keys + \
           [f'zz no such name {i}' for i in range(20)]
    akeys = [k for (k,) in nth('SELECT DISTINCT key FROM alias_name ORDER BY key')]
    grp = sorted(_uu(g) for (g,) in nth('SELECT gid FROM rgx ORDER BY id'))
    rel = [_uu(g) for (g,) in nth('SELECT gid FROM release ORDER BY gid')]
    bars = [b for (b,) in nth('SELECT DISTINCT code FROM barcode ORDER BY code')] + \
           [rnd.randrange(10 ** 12, 10 ** 13) for _ in range(20)]
    svcs = [list(r) for r in nth('SELECT DISTINCT svc, ext FROM svc_album ORDER BY svc, ext')] + \
           [[1, 'zz-no-such-album'], [2, 1], [4, 2], [5, 'zzzzzzzzzzzzzzzzzzzzzz']]
    c.close()

    def calls(ids):            # as a page asks: up to 67 known, then 33 unknown
        out_ = []
        for i in range(0, len(ids), 67):
            out_.append(ids[i:i + 67] + miss(33))
        return out_
    n = 0
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        def w(fn, *args):
            nonlocal n
            f.write(jdump({'f': fn, 'a': list(args)}) + '\n')
            n += 1
        for a in arts:
            for fn in ('artistRead', 'collaboration', 'groupCount', 'artistList'):
                w(fn, a)
        for k in keys:
            w('sameName', k)
            w('aliasName', k)
        for k in akeys:
            w('aliasName', k)
            w('sameName', k)
        for ids in calls(grp):
            w('groups', ids)
        for ids in calls(rel):
            w('releases', ids)
        for b in bars:
            w('barcode', b)
        for s, e in svcs:
            w('svcAlbum', s, e)
    return n


NEW_LOOKUPS = ('artistList', 'aliasName', 'releases', 'barcode', 'svcAlbum')


def truth(base, facts_db, alias_tsv, queries_path, out):
    """The new lookups' answers (NEW_LOOKUPS), built from the dump's facts, today's
    pack and the alias keys by other SQL than the file's, in the reader's shapes;
    {"skip": 1} for the others (Pack.pm answers those). The facts have no index,
    so each fact table is read once, for the keys the queries ask."""
    qs = []
    with open(queries_path, encoding='utf-8') as f:
        for line in f:
            qs.append(json.loads(line))
    c = sqlite3.connect(f'file:{base}?mode=ro', uri=True)
    c.execute('ATTACH ? AS f', (f'file:{facts_db}?mode=ro',))
    names = {t: dict(c.execute(f'SELECT id, name FROM {t}')) for t in ('rg_type', 'rg_sec')}
    sec_ids = sorted(names['rg_sec'])

    def artist_id(mbid):
        raw = _raw(mbid)
        if raw is None:
            return None
        r = c.execute('SELECT id FROM artist WHERE gid = ?', (raw,)).fetchone() or \
            c.execute('SELECT artist FROM artist_redirect WHERE gid = ?', (raw,)).fetchone()
        return r[0] if r else None

    ids = {q['a'][0]: artist_id(q['a'][0]) for q in qs if q['f'] == 'artistList'}
    want_a = {i for i in ids.values() if i is not None}
    lists = {i: [] for i in want_a}
    for a, rg in c.execute('SELECT artist, rg FROM f.f_artist_rg'):
        if a in lists:
            lists[a].append(rg)
    grp = {}
    need = sorted({rg for v in lists.values() for rg in v})
    for i in range(0, len(need), 500):
        part = need[i:i + 500]
        for row in c.execute('SELECT g.id, g.gid, g.title, g.date, g.type, g.sec, g.cover, cr.name, g.inlist '
                             'FROM f.f_rg g LEFT JOIN f.f_credit cr ON cr.id = g.credit '
                             f'WHERE g.id IN ({",".join("?" * len(part))})', part):
            grp[row[0]] = row[1:]
    want_r = {_raw(m) for q in qs if q['f'] == 'releases' for m in q['a'][0]} - {None}
    rel = {}
    for g, rg in c.execute('SELECT gid, rg FROM f.f_release'):
        if g in want_r:
            rel[g] = rg
    rgs = {}
    need = sorted(set(rel.values()))
    for i in range(0, len(need), 500):
        part = need[i:i + 500]
        for i_, g, t, s in c.execute(f'SELECT id, gid, type, sec FROM f.f_rg WHERE id IN ({",".join("?" * len(part))})', part):
            rgs[i_] = (g, t, s)
    want_b = {int(q['a'][0]) for q in qs if q['f'] == 'barcode'}
    bars = {}
    for code, rg in c.execute('SELECT code, rg FROM f.f_barcode'):
        if code in want_b:
            bars.setdefault(code, set()).add(rg)
    want_s = {(q['a'][0], q['a'][1]) for q in qs if q['f'] == 'svcAlbum'}
    svcs = {}
    for s, e, rg in c.execute('SELECT svc, ext, rg FROM f.f_link'):
        if (s, e) in want_s:
            svcs.setdefault((s, e), set()).add(rg)
    gid_of = {}
    need = sorted({rg for v in list(bars.values()) + list(svcs.values()) for rg in v})
    for i in range(0, len(need), 500):
        part = need[i:i + 500]
        for i_, g in c.execute(f'SELECT id, gid FROM f.f_rg WHERE id IN ({",".join("?" * len(part))})', part):
            gid_of[i_] = g
    by_key = {}
    for k, a, _ in read_aliases(alias_tsv):
        by_key.setdefault(k, []).append(a)
    art = {}

    def artist_row(i):
        if i not in art:
            art[i] = c.execute('SELECT gid, name, ngroups FROM artist WHERE id = ?', (i,)).fetchone()
        return art[i]

    def answer(fn, a):
        if fn == 'artistList':
            i = ids[a[0]]
            if i is None:
                return None
            rows = sorted((rg, grp[rg]) for rg in lists[i] if rg in grp and grp[rg][7])
            return [[hx(g), t, d, ty, sc, cv, cr] for _, (g, t, d, ty, sc, cv, cr, _) in rows]
        if fn == 'releases':
            out_ = {}
            for m in a[0]:
                raw = _raw(m)
                if raw in rel:
                    g, t, s = rgs[rel[raw]]
                    out_[_uu(raw)] = {'rg': _uu(g), 'type': names['rg_type'].get(t, ''),
                                      'secondary': [names['rg_sec'][i] for i in sec_ids if (s or 0) & (1 << i)]}
            return out_
        if fn == 'barcode':
            return [_uu(g) for g in sorted(gid_of[rg] for rg in bars.get(int(a[0]), ()))]
        if fn == 'svcAlbum':
            return [_uu(g) for g in sorted(gid_of[rg] for rg in svcs.get((a[0], a[1]), ()))]
        if fn == 'aliasName':
            if not isinstance(a[0], str) or not a[0]:
                return None
            rows = [(i,) + tuple(artist_row(i)) for i in by_key.get(a[0], [])]
            rows.sort(key=lambda r: (-r[3], r[2].encode('utf-8'), r[0]))
            return [{'mbid': _uu(g), 'name': nm, 'score': ng} for _, g, nm, ng in rows]
        raise ValueError(fn)
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        for q in qs:
            f.write(jdump({'r': answer(q['f'], q['a'])} if q['f'] in NEW_LOOKUPS else {'skip': 1}) + '\n')
    c.close()
    return len(qs)


def pages(narrow_db, facts_db, out, named=None):
    """The named artists (each act of the name up to three) with the groups a
    page of theirs lists: every group credited to them in any position, from
    the dump's facts (the first list is ListenBrainz's and the community API's,
    not the pack's; measure_extra.py's `lists` holds the same pairs)."""
    c = sqlite3.connect(f'file:{narrow_db}?mode=ro', uri=True)
    c.execute('ATTACH ? AS f', (f'file:{facts_db}?mode=ro',))
    res = []
    for key in (named or NAMED):
        for i, g, name, ng in c.execute('SELECT a.id, a.gid, a.name, a.ngroups FROM artist_name n JOIN artist a '
                                        'ON a.id = n.artist WHERE n.key = ? ORDER BY a.ngroups DESC, a.id LIMIT 3',
                                        (key,)).fetchall():
            gids = [_uu(x) for (x,) in c.execute('SELECT g.gid FROM f.f_artist_rg ar JOIN f.f_rg g ON g.id = ar.rg '
                                                 'WHERE ar.artist = ? AND g.inlist ORDER BY g.id', (i,))]
            res.append({'label': f'{name} ({_uu(g)[:8]}, {ng} groups)', 'mbid': _uu(g), 'key': key, 'groups': gids})
    c.close()
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False)
    return len(res)


def _norm(x):
    """Perl and JavaScript encode a number held as text differently; Discography
    reads neither as a type, so scalars are compared as text."""
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_norm(v) for v in x]
    return None if x is None else str(x)


def compare(queries_path, pack_answers, truth_answers, hosted):
    """hosted: [(label, answers path)]. Today's lookups: every hosted answer equals
    Pack.pm's on today's pack; for `releases`, every entry Pack.pm gives,
    identically (the hosted map is the whole map: the others are counted). The
    new lookups (NEW_LOOKUPS, `releases` too): every hosted answer equals the
    truth's; with no truth (None: the local test) they are counted unchecked."""
    files = [open(pack_answers, encoding='utf-8')] + \
            ([open(truth_answers, encoding='utf-8')] if truth_answers else []) + \
            [open(p, encoding='utf-8') for _, p in hosted]
    labels = [lab for lab, _ in hosted]
    res = {}
    with open(queries_path, encoding='utf-8') as qf:
        for line in qf:
            q = json.loads(line)
            ans = [json.loads(f.readline()) for f in files]
            pack, tru = ans[0], (ans[1] if truth_answers else None)
            got = [_norm(a.get('r')) for a in ans[-len(hosted):]] if hosted else []
            fn = q['f']
            st = res.setdefault(fn, {'calls': 0, 'differ': 0, 'answered': 0, 'unchecked': 0,
                                     'extra_releases': 0, 'examples': []})
            st['calls'] += 1
            bad = set()
            want = None
            if 'skip' not in pack:
                want = _norm(pack['r'])
                for lab, g in zip(labels, got):
                    if fn == 'releases':
                        if any((g or {}).get(k) != v for k, v in (want or {}).items()):
                            bad.add(lab)
                    elif g != want:
                        bad.add(lab)
                if fn == 'releases' and got:
                    st['extra_releases'] += len(got[0] or {}) - len(want or {})
            if tru is not None and 'skip' not in tru:
                want = _norm(tru['r'])
                bad |= {lab for lab, g in zip(labels, got) if g != want}
            elif 'skip' in pack:
                st['unchecked'] += 1
                want = got[0] if got else None
            if want not in (None, [], {}):
                st['answered'] += 1
            if bad:
                st['differ'] += 1
                if len(st['examples']) < 3:
                    st['examples'].append({'query': q, 'differ': sorted(bad),
                                           'answers': {lab: json.dumps(a.get('r'), ensure_ascii=False)[:300]
                                                       for lab, a in zip(['Pack.pm'] + (['truth'] if truth_answers else [])
                                                                         + labels, ans)}})
    for f in files:
        if f.readline():
            raise SystemExit('the answer files hold more lines than the query list')
        f.close()
    return res


# --- against MusicBrainz itself -------------------------------------------------

def _mb_get(url, gap, tries=4):
    """MusicBrainz's JSON for one URL, `gap` seconds after the last, with the
    pack builder's User-Agent (no e-mail address); None after `tries` failures."""
    import fetch_dump
    for n in range(tries):
        time.sleep(gap if n == 0 else 3 * n)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': fetch_dump.UA}),
                                        timeout=30) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:  # noqa: BLE001  (a 503 from the rate limit, a timeout: asked again)
            log(f'MusicBrainz: {e} ({n + 1}/{tries})')
    return None


def _lucene_esc(s):
    """API.pm's _luceneEsc: Lucene's reserved characters escaped."""
    out = []
    i = 0
    while i < len(s):
        if s[i:i + 2] in ('&&', '||'):
            out.append('\\' + s[i:i + 2])
            i += 2
            continue
        out.append('\\' + s[i] if s[i] in '+-!(){}[]^"~*?:\\/' else s[i])
        i += 1
    return ''.join(out)


def mbcheck(hosted_db, alias_tsv, dsc, n_alias, n_title, server, fetch=None):
    """The two MusicBrainz lookups the extras would replace, asked of MusicBrainz
    (`server`, its /ws/2/ address) for a sample, n_alias names and n_title
    titles, and held against the file. Data, not the plugin's whole rules:
    MusicBrainz's answers are what its search returns today, newer than the dump
    (an edit since then reads as a difference too). Asked of Simon's local
    mirror (Simon, 2026-11-01: "use the local MB mirror not public MB please to
    expediate tests on the mb comparisions"; it replicates daily), as fast as it
    answers; musicbrainz.org at its rate, MB_GAP apart.
      alias  a name that is NO act's own name but an alias of some (the names
             Discography resolves with MusicBrainz's alias pass today):
             `alias:"<name>"`, limit 8, as API.pm's alias pass asks; MusicBrainz's
             top artist against the file's aliasName answer (most groups first).
      title  an untagged library artist's lookup (C2): for a group of the file and
             one act credited on it, `releasegroup:"<title>" AND artist:"<name>"`,
             limit 12, as API.pm's _rgQueryUrl asks; the acts credited on the hits
             whose title is that title (DSC's _nameKey) against the acts the file's
             group lists give: named that name, with a group of that title.
    Every difference where the file lacks MusicBrainz's act, or MusicBrainz finds
    nothing, is then looked up by id (`artist/<mbid>`, MusicBrainz's database,
    not its search) and given its reason (`why`), so an edit since the dump, a
    merged act and the search's own matching are told apart from a loss. The
    mirror needs it most: its search index is loaded once and does not follow
    its daily updates (measured 2026-11-01: none of the 20 newest acts in pack
    20261028 found by `arid:`, all in its database).
    Refuses a `dsc` whose name keys are not the ones the file was built with
    (namekey_probe): the file's keys and MusicBrainz's names would not meet."""
    gap = MB_GAP if urllib.parse.urlsplit(server).hostname == 'musicbrainz.org' else 0
    fetch = fetch or (lambda u: _mb_get(u, gap))
    c = sqlite3.connect(f'file:{hosted_db}?mode=ro', uri=True)
    probe = c.execute('SELECT name, key FROM namekey_probe ORDER BY name').fetchall()
    bad = [(n, k, k2) for (n, k), k2 in zip(probe, _keys(dsc, [n for n, _ in probe])) if k != k2]
    if not probe or bad:
        raise SystemExit(f'{dsc}: its name keys are not the ones {hosted_db} was built with '
                         f'({len(bad)} of {len(probe)} probe names differ, e.g. {bad[:3]})')
    own = {k for (k,) in c.execute('SELECT DISTINCT key FROM artist_name')}
    first = {}
    for k, a, n in read_aliases(alias_tsv):
        if k not in own and k not in first:
            first[k] = n
    ak = sorted(first)
    step = max(1, len(ak) // n_alias) if n_alias else 0
    alias_keys = ak[::step][:n_alias] if step else []
    pairs = []
    if n_title:
        total = c.execute('SELECT count(*) FROM artist_rg').fetchone()[0]
        st_ = max(1, total // n_title)
        for i, (a, rg) in enumerate(c.execute('SELECT artist, rg FROM artist_rg ORDER BY artist, rg')):
            if i % st_ == 0 and len(pairs) < n_title:
                pairs.append((a, rg))
    pairs = [(a, rg, c.execute('SELECT name FROM artist WHERE id = ?', (a,)).fetchone()[0],
              c.execute('SELECT title FROM rg WHERE id = ?', (rg,)).fetchone()[0]) for a, rg in pairs]
    log(f'mbcheck: {len(alias_keys)} alias names, {len(pairs)} title lookups, asked of {server}, '
        f'{gap} s apart')
    t0 = time.time()
    res = {'server': server, 'dump': dict(c.execute("SELECT key, value FROM meta")).get('dump'),
           'alias': {'asked': 0, 'same_first': 0, 'mb_first_lower': 0, 'mb_first_missing': 0, 'mb_none': 0,
                     'failed': 0, 'examples': {}},
           'title': {'asked': 0, 'same': 0, 'mb_more': 0, 'file_more': 0, 'mb_none_exact': 0, 'mb_other_names': 0,
                     'failed': 0, 'examples': {}}}

    def ex(part, kind, x):
        e = res[part]['examples'].setdefault(kind, [])
        if len(e) < 10:
            e.append(x)

    looked = {}

    def look(mbid):
        """MusicBrainz's database on an act: (its name and alias texts, current), or
        None when the id is gone (deleted, or merged into another id)."""
        if mbid not in looked:
            d = fetch(server + f'artist/{mbid}?inc=aliases&fmt=json')
            ok = isinstance(d, dict) and d.get('id') == mbid
            looked[mbid] = ([d.get('name') or ''] + [t for al in d.get('aliases') or [] if isinstance(al, dict)
                                                       for t in (al.get('name') or '', al.get('sort-name') or '')]
                            if ok else None)
        return looked[mbid]

    def held(mbid):
        try:
            return c.execute('SELECT 1 FROM artist WHERE gid = ?', (uuid.UUID(mbid).bytes,)).fetchone() is not None
        except ValueError:
            return False

    why = []                             # (part, kind of difference, the example, key, mbids looked up)
    for k in alias_keys:
        name = first[k]
        rows = c.execute('SELECT a.gid, a.name, a.ngroups FROM alias_name n JOIN artist a ON a.id = n.artist '
                         'WHERE n.key = ? ORDER BY a.ngroups DESC, a.name, a.id', (k,)).fetchall()
        mine = [_uu(g) for g, _, _ in rows]
        d = fetch(server + 'artist?query=' + urllib.parse.quote(f'alias:"{name}"', safe='') + '&fmt=json&limit=8')
        r = res['alias']
        r['asked'] += 1
        if not isinstance(d, dict) or not isinstance(d.get('artists'), list):
            r['failed'] += 1
            continue
        if not d['artists']:
            r['mb_none'] += 1
            x = {'name': name, 'file': [x[1] for x in rows][:3]}
            ex('alias', 'MusicBrainz found none', x)
            why.append(('alias', 'none', x, k, mine[:1]))
            continue
        top = d['artists'][0]
        x = {'name': name, 'musicbrainz': f"{top.get('name')} ({top.get('id')})",
             'file': [f'{nm} ({ng} groups)' for _, nm, ng in rows][:3]}
        if mine and top.get('id') == mine[0]:
            r['same_first'] += 1
        elif top.get('id') in mine:
            r['mb_first_lower'] += 1
            ex('alias', 'the file has it, not first', x)
        else:
            r['mb_first_missing'] += 1
            ex('alias', 'the file lacks it', x)
            why.append(('alias', 'lacks', x, k, [top.get('id') or '']))

    got = []
    for a, rg, name, title in pairs:
        q = f'releasegroup:"{_lucene_esc(title)}" AND artist:"{_lucene_esc(name)}"'
        got.append(fetch(server + 'release-group?query=' + urllib.parse.quote(q, safe='') + '&fmt=json&limit=12'))
    texts = []
    for (a, rg, name, title), d in zip(pairs, got):
        texts += [name, title]
        for h in (d or {}).get('release-groups', []) if isinstance(d, dict) else []:
            texts.append(h.get('title') or '')
            for cr in h.get('artist-credit') or []:
                if isinstance(cr, dict) and isinstance(cr.get('artist'), dict):
                    texts.append(cr['artist'].get('name') or '')
    kmap = dict(zip(texts, _keys(dsc, texts))) if texts else {}
    # The acts the file names for each pair: called that name, with a group of
    # that title; their titles keyed in one go.
    acts = {}
    for a, rg, name, title in pairs:
        acts[name] = [i for (i,) in c.execute('SELECT artist FROM artist_name WHERE key = ?', (kmap.get(name, ''),))]
    titles = {}
    for ids in acts.values():
        for i in ids:
            if i not in titles:
                titles[i] = [t for (t,) in c.execute('SELECT g.title FROM artist_rg ar JOIN rg g ON g.id = ar.rg '
                                                     'WHERE ar.artist = ?', (i,))]
    every = sorted({t for ts in titles.values() for t in ts} - set(kmap))
    kmap.update(zip(every, _keys(dsc, every)) if every else [])
    for (a, rg, name, title), d in zip(pairs, got):
        r = res['title']
        r['asked'] += 1
        if not isinstance(d, dict) or not isinstance(d.get('release-groups'), list):
            r['failed'] += 1
            continue
        nk, tk = kmap.get(name, ''), kmap.get(title, '')
        exact, other = set(), set()
        for h in d['release-groups']:
            if not tk or kmap.get(h.get('title') or '') != tk:
                continue
            for cr in h.get('artist-credit') or []:
                art = cr.get('artist') if isinstance(cr, dict) else None
                if not isinstance(art, dict) or not art.get('id'):
                    continue
                (exact if kmap.get(art.get('name') or '') == nk else other).add(art['id'])
        mine = {_uu(c.execute('SELECT gid FROM artist WHERE id = ?', (i,)).fetchone()[0])
                for i in acts[name] if tk and any(kmap.get(t) == tk for t in titles[i])}
        x = {'artist': name, 'title': title, 'musicbrainz': sorted(exact), 'file': sorted(mine),
             'musicbrainz_other_names': len(other)}
        if other:
            r['mb_other_names'] += 1
            ex('title', 'MusicBrainz also names acts whose name only holds it', x)
        if not exact:
            r['mb_none_exact'] += 1
        if exact == mine:
            r['same'] += 1
        elif exact - mine:
            r['mb_more'] += 1
            ex('title', 'MusicBrainz names an act the file does not', x)
            why.append(('title', 'lacks', x, nk, sorted(exact - mine)))
        else:
            r['file_more'] += 1
            ex('title', 'the file names an act MusicBrainz does not', x)

    # Each difference's reason, from MusicBrainz's database (one lookup per act).
    for part, kind, x, k, ids in why:
        for i in ids:
            look(i)
    texts = sorted({t for v in looked.values() if v for t in v})
    km = dict(zip(texts, _keys(dsc, texts))) if texts else {}
    for part, kind, x, k, ids in why:
        now = [(i, looked[i]) for i in ids if looked[i] is not None]
        if not now:
            w = 'file gone' if kind == 'none' else 'gone'
        elif kind == 'none':
            w = 'unsearched' if any(km.get(t) == k for t in now[0][1]) else 'removed'
        elif any(not held(i) for i, _ in now):
            w = 'not held'
        elif part == 'title' or any(km.get(t) == k for _, v in now for t in v):
            w = 'edit'
        else:
            w = 'longer'
        w = WHY[w]
        x['why'] = w
        ws = res[part].setdefault('why', {})
        ws[w] = ws.get(w, 0) + 1
    c.close()
    res['seconds'] = round(time.time() - t0, 1)
    return res


# mbcheck's reasons for a difference, from MusicBrainz's database on each act.
WHY = {
    'gone': 'merged or deleted in MusicBrainz since the dump (the file is right)',
    'file gone': "the file's act is merged or deleted in MusicBrainz since the dump",
    'not held': 'an act the file does not hold (newer than the dump, or with nothing the pack keeps)',
    'edit': 'the file holds the act, not with this name or group: an edit since the dump',
    'longer': 'MusicBrainz matched a longer name that holds it; the file matches the whole name',
    'unsearched': "the act has the name in MusicBrainz's database; its search does not find it",
    'removed': 'the name is gone from the act since the dump',
}


# --- the whole measurement, from two dumps' base packs and facts -------------------------

def _node(script, *args):
    exe = os.environ.get('NODE', 'node')
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'worker', script)
    r = subprocess.run([exe, here, *args], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f'{script} failed:\n{r.stdout}\n{r.stderr}')
    return json.loads(r.stdout.strip().splitlines()[-1])


def run(base_a, facts_a, alias_a, base_b, facts_b, alias_b, dsc, work, every=1, named=None):
    """Every step but mbcheck (the runner cannot reach Simon's mirror: the local
    test asks it); returns the results (also in <work>/results.json). Deletes
    the files it is done with as it goes; keeps hosted-B.db, pages.json,
    queries.jsonl and truth.jsonl (the local test's)."""
    os.makedirs(work, exist_ok=True)
    res = {'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    old, new = os.path.join(work, 'hosted-A.db'), os.path.join(work, 'hosted-B.db')
    narrow(base_a, facts_a, alias_a, old)
    narrow(base_b, facts_b, alias_b, new)
    qpath, ppath = os.path.join(work, 'queries.jsonl'), os.path.join(work, 'pages.json')
    tpath, pack_ans = os.path.join(work, 'truth.jsonl'), os.path.join(work, 'answers-pack.jsonl')
    res['queries'] = queries(new, qpath, every, named)
    res['pages'] = pages(new, facts_b, ppath, named)
    log('truth')
    truth(base_b, facts_b, alias_b, qpath, tpath)
    r = subprocess.run(['perl', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'verify',
                                             'hosted_pack_answers.pl'), dsc, base_b, qpath, pack_ans],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f'hosted_pack_answers.pl failed:\n{r.stdout}\n{r.stderr}')
    res['pack_answers'] = r.stdout.strip()
    res['base'] = {'A': measure(base_a), 'B': measure(base_b, gz=True)}
    log('measure, delta')
    h = {'fresh': measure(new, gz=True), 'old': measure(old)}
    d = os.path.join(work, 'hosted.jsonl.gz')
    h['delta'] = delta(old, new, d)
    os.remove(d)
    os.remove(old)
    h['pages'] = _node('pages.mjs', new, ppath)
    ans = os.path.join(work, 'answers-hosted.jsonl')
    h['answers_run'] = _node('answers.mjs', new, qpath, ans)
    res['hosted'] = h
    log('comparing the answers')
    res['compare'] = compare(qpath, pack_ans, tpath, [('hosted', ans)])
    for p_ in (ans, pack_ans):           # queries.jsonl and truth.jsonl go with the file, for the local test
        os.remove(p_)
    res['finished'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    with open(os.path.join(work, 'results.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return res


# --- the report ----------------------------------------------------------------------------

WRITES_MONTH = 50_000_000       # D1, Workers Paid, included (Cloudflare's pricing page, 2026-10-30)
READS_MONTH = 25_000_000_000
STORAGE = 5_000_000_000
DELTAS_MONTH = 2 * 52 / 12      # MusicBrainz publishes two dumps a week


def _mb(n):
    return f'{n / 1e6:,.1f} MB'


def _pct(n, of):
    return f'{100 * n / of:.2f}%'


EXTRA_TABLES = {'rg': 'group lists', 'credit': 'group lists', 'artist_rg': 'group lists',
                'release': 'whole release map', 'barcode': 'barcodes', 'svc_album': 'album ids',
                'alias_name': 'alias / old-name keys'}


def report(res):
    L = []
    p = L.append
    h = res['hosted']
    dump_a, dump_b = h['old']['dump'], h['fresh']['dump']
    p(f"=== The hosted pack: today's pack + group lists + whole release map + barcodes + album ids + alias keys, "
      f"dump {dump_a} -> {dump_b}")
    p('Plan: LMS-Discography docs/hosted-pack-plan.md (sections 10, 11). Measured, nothing estimated; the shares are '
      'the measured counts against D1\'s Workers Paid allowance')
    p(f'(50,000,000 rows written, 25,000,000,000 rows read, 5 GB a month; two dumps a week = {DELTAS_MONTH:.1f} deltas a month).')
    b, f = res['base']['B'], h['fresh']
    p('\n--- 1. the one load into D1 (dump B, freshly built): rows written and size')
    for lab, x in (("today's pack", b), ('the hosted file', f)):
        w = x['rows'] + x['index_rows']
        p(f"{lab:16} {x['rows']:>12,} rows + {x['index_rows']:>10,} index entries = {w:>12,} written "
          f"({_pct(w, WRITES_MONTH)} of 50M); {_mb(x['size'])} on disk ({_pct(x['size'], STORAGE)} of 5 GB), "
          f"{_mb(x.get('gz', 0))} gzipped")
    p('what each extra adds (rows, on disk):')
    by = {}
    for t, s_ in f['tables'].items():
        base_t = b['tables'].get(t, {'rows': 0, 'disk': 0})
        if t in EXTRA_TABLES:
            k = by.setdefault(EXTRA_TABLES[t], [0, 0])
            k[0] += s_['rows'] - base_t['rows']
            k[1] += s_['disk'] - base_t['disk']
    for k, (rows, disk) in by.items():
        p(f'   {k:24} +{rows:>11,} rows  +{_mb(disk)}')

    p('\n--- 2. one delta (dump A -> dump B) applied IN PLACE, as the Worker will (upsert / delete per row, no VACUUM)')
    d = h['delta']['apply']
    g = d['size'] - f['size']
    p(f"rows written: {d['new']:,} new + {d['replaced']:,} replaced + {d['deleted']:,} deleted + {d['index']:,} index "
      f"entries (an upper bound) = {d['rows_written']:,}; x {DELTAS_MONTH:.1f} a month = "
      f"{_pct(d['rows_written'] * DELTAS_MONTH, WRITES_MONTH)} of 50M")
    p('per table (puts, dels): ' + ', '.join(f'{t} {pt:,}/{dl:,}' for t, (pt, dl) in sorted(h['delta']['tables'].items())
                                            if pt or dl))
    p(f"delta {_mb(h['delta']['bytes_gz'])} gzipped; the grown file {_mb(d['size'])} against {_mb(f['size'])} fresh "
      f"({('+' if g >= 0 else '') + _mb(g).strip()}), {d['free_pages']:,} free pages")
    p('(growth is ONE delta from a freshly built file; later deltas are measured by later runs)')

    p('\n--- 3. one cold artist page: rows read (rows returned, a LOWER bound of D1\'s rows scanned) and queries')
    for q in h['pages']['pages']:
        p(f"   {q['label'][:60]:60} {q['rows_read']:>9,} rows {q['queries']:>6,} queries {q['ms']:>8.1f} ms (Node)")

    p('\n--- 4. per table (dump B): rows / on disk')
    p(', '.join(f"{t} {s_['rows']:,}/{_mb(s_['disk']).strip()}" for t, s_ in sorted(f['tables'].items())))

    p('\n--- 5. the answers: today\'s lookups against Pack.pm on today\'s pack; the new ones against answers built '
      'from the dump\'s facts')
    p(f"queries {res['queries']:,}; {res['pack_answers']}")
    bad = 0
    for fn, st in res['compare'].items():
        extra = f", {st['extra_releases']:,} releases more than today's map" if fn == 'releases' else ''
        unch = f", {st['unchecked']:,} UNCHECKED" if st['unchecked'] else ''
        p(f"  {fn:14} {st['calls']:>9,} calls, {st['answered']:>9,} answered, {st['differ']:,} differ{extra}{unch}")
        bad += st['differ']
        for e in st['examples']:
            p(f"     {json.dumps(e['query'], ensure_ascii=False)[:200]}  differ: {e['differ']}")
            for lab, a in e['answers'].items():
                p(f'       {lab}: {a}')
    p('  ' + ('SAME: every answer as Pack.pm gives on today\'s pack, and every new one as the dump\'s facts give'
              if not bad else f'DIFFERENT: {bad} call(s) differ, see above'))

    p('\n--- 6. against MusicBrainz itself: not on the runner. The sample is asked of the local mirror on the Mac '
      '(hosted.py mbcheck, THE LOCAL TEST), with this run\'s file')

    p('\n--- 7. not measured by this run')
    p('- D1 itself: how it counts an upsert of an existing key, an index entry, rows read by a join; whether it '
      'gives back the space of a deleted or replaced row (VACUUM is not documented). To be checked on real D1 with '
      'a few hundred rows before any big load.')
    p('- Growth over more than one delta: the next runs, one per dump.')
    p('- The Worker\'s own time and CPU: section 3 times Node on this runner.')
    p(f"\nrun {res.get('started')} -> {res.get('finished')}")
    return '\n'.join(L)


def mb_report(m):
    """mbcheck's results, readable."""
    a, t = m['alias'], m['title']
    return '\n'.join([
        f"=== The hosted file (dump {m['dump']}) against MusicBrainz itself, asked of {m['server']} in "
        f"{m['seconds']:,} s; a sample. MusicBrainz is newer than the dump, so an edit since reads as a difference too.",
        f"alias names (no act's own name; today MusicBrainz's alias search): {a['asked']:,} asked; MusicBrainz's top "
        f"artist is the file's first {a['same_first']:,}, in the file's list lower down {a['mb_first_lower']:,}, NOT in "
        f"the file {a['mb_first_missing']:,}; MusicBrainz found none {a['mb_none']:,}; failed {a['failed']:,}",
        f"untagged library artist (C2; today MusicBrainz's title search): {t['asked']:,} asked; the same acts "
        f"{t['same']:,}, MusicBrainz names an act the file does not {t['mb_more']:,}, the file names one MusicBrainz "
        f"does not {t['file_more']:,}; MusicBrainz also names acts whose name only holds the name "
        f"{t['mb_other_names']:,} (the file cannot); no exact-name act in MusicBrainz's hits {t['mb_none_exact']:,}; "
        f"failed {t['failed']:,}",
    ] + [f'   {part}, why (MusicBrainz\'s database on each act): {n:,} {w}'
         for part in ('alias', 'title') for w, n in sorted(m[part].get('why', {}).items(), key=lambda y: -y[1])
         ] + [line for part in ('alias', 'title') for kind, xs in m[part]['examples'].items()
              for line in [f'   {part}: {kind}'] + [f'      {json.dumps(x, ensure_ascii=False)[:400]}' for x in xs]])


def _has_md5(fetch_dump, name):
    try:
        fetch_dump.expected_md5(name)
        return True
    except (SystemExit, OSError):
        return False


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else ''
    if cmd == 'aliases' and len(a) == 4:
        aliases(a[1], a[2], a[3])
    elif cmd == 'narrow' and len(a) == 5:
        narrow(a[1], a[2], a[3], a[4])
    elif cmd == 'delta' and len(a) == 4:
        print(jdump(delta(a[1], a[2], a[3])))
    elif cmd == 'grow' and len(a) == 4:
        print(jdump(grow(a[1], a[2], a[3])))
    elif cmd == 'measure' and len(a) in (2, 3):
        print(jdump(measure(a[1], len(a) == 3)))
    elif cmd == 'dumps' and len(a) == 1:
        # The two newest full dumps MusicBrainz serves (it keeps two), each with
        # its MD5SUMS published (a dump still being written has none).
        import re
        import fetch_dump
        idx = fetch_dump.get(fetch_dump.BASE).read().decode()
        names = sorted(set(re.findall(r'href="(\d{8}-\d{6})/"', idx)))
        ready = [n for n in names if _has_md5(fetch_dump, n)]
        if len(ready) < 2:
            raise SystemExit(f'need two dumps, MusicBrainz serves {ready}')
        print(' '.join(ready[-2:]))
    elif cmd == 'queries' and len(a) in (3, 4):
        print(queries(a[1], a[2], int(a[3]) if len(a) == 4 else 1))
    elif cmd == 'truth' and len(a) == 6:
        print(truth(a[1], a[2], a[3], a[4], a[5]))
    elif cmd == 'pages' and len(a) == 4:
        print(pages(a[1], a[2], a[3]))
    elif cmd == 'compare' and len(a) == 5:
        res = compare(a[1], a[2], None if a[3] == '-' else a[3], [('hosted', a[4])])
        print(json.dumps(res, ensure_ascii=False, indent=1))
        bad = sum(st['differ'] for st in res.values())
        print(f'{"SAME" if not bad else "DIFFERENT"}: {bad} call(s) differ', file=sys.stderr)
        sys.exit(1 if bad else 0)
    elif cmd == 'mbcheck' and len(a) == 8:
        m = mbcheck(a[1], a[2], a[3], int(a[4]), int(a[5]), a[6])
        with open(a[7], 'w', encoding='utf-8') as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        print(mb_report(m))
    elif cmd == 'run' and len(a) == 10:
        print(report(run(*a[1:9], every=int(a[9]))))
    elif cmd == 'report' and len(a) == 2:
        with open(a[1], encoding='utf-8') as f:
            print(report(json.load(f)))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
