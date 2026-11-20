"""The keyed data file, built from MusicBrainz's free (CC0) full dump.

The steps (the workflow runs them in turn and deletes what it is done with):
  aliases  <base.db> <dsc checkout> <aliases.tsv>
           every alias whose name key (Discography's own _nameKey, builder/namekeys.pl) is not its artist's own
  narrow   <base.db> <facts.db> <aliases.tsv> <out.db>
           the tables file: build_pack.py's tables plus measure_extra.py's (every group an artist is credited on,
           the whole release map, barcodes, streaming album ids) and the alias keys
  entries  <tables.db> <entries.db> [<more.db>]
           the same content as records: art (an artist and its parts, or a merged-away gid's redirect), lst (its
           group list), grp (a group's extras), nam (a name key's acts), ali (an alias key's acts), rel (a release),
           bar, svc. With more.db (builder/more.py) the file is FILE_FORMAT_MORE: an artist's abo and lnk are
           appended to its art record and a release's rlb and rcr to its rel record (FOLD, trailing fields), and
           MORE_KINDS are kinds of their own (prf: the abo and lnk of an artist art lacks; crd, who, pgr; rol: the
           names of the link types and attributes the new records give by id, two records, `link_type` and
           `attribute`, each {id: name})
  keyed    <entries.db> <tables.db> <bits | sized> <out.db>
           the keyed file: every kind a table (k BLOB PRIMARY KEY, v BLOB) WITHOUT ROWID, 2^bits buckets a kind
           (0: one row a record), a bucket's records one deflated JSON object. The same records give the same bytes.
           `sized` (FILE_FORMAT_MORE only): each kind at its own count, SIZED; the counts ride in meta
           (bits_by_kind) in place of the one `bits`.

Nothing that reads the real dump runs anywhere but the build's runner; the tests use the made-up dump in
tests/fixture.py.
"""
import hashlib, json, os, shutil, sqlite3, subprocess, sys, time, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_extra  # noqa: E402  (variant_sql: the extra tables)

FILE_FORMAT = 'data-1'                # the tables file, and a keyed file of KINDS
FILE_FORMAT_MORE = 'data-2'           # a keyed file with builder/more.py's records: KINDS + MORE_KINDS
PARTS = ['lists', 'relmap', 'barcode', 'links']   # measure_extra.py's; build_pack.py's tables are the rest
KINDS = ['art', 'lst', 'grp', 'nam', 'ali', 'rel', 'bar', 'svc']
MORE_KINDS = ['prf', 'crd', 'who', 'pgr', 'rol']
GID_KINDS = {'art', 'lst', 'grp', 'rel', 'prf', 'crd', 'pgr'}
# Appended to today's record of the same key, in this order, as trailing fields (a reader of today's fields reads
# them as before): art [.., abo, lnk], rel [.., rlb, rcr]. An artist art lacks has them in prf, as [abo, lnk].
FOLD = {'art': ['abo', 'lnk'], 'rel': ['rlb', 'rcr']}
# Each kind's bucket count (bits) in a sized file: the count that stored it smallest while its average bucket stayed
# within today's largest average bucket (lst, 17.0 kB deflated), none over the cap; measured on dump 20261118-002316
# (measure-more run 44750228998). A table that grows keeps its count: the build logs each table's average and
# largest bucket, and the cap is checked every build.
SIZED = {'art': 14, 'lst': 14, 'grp': 11, 'nam': 13, 'ali': 11, 'rel': 15, 'bar': 14, 'svc': 13,
         'prf': 10, 'crd': 14, 'who': 11, 'pgr': 12, 'rol': 1}   # rol: two records, kept out of the meta (and so
                                                                  # out of a release's manifest and a reader's meta read)
CAP = 2_000_000                       # the largest value a row may hold

T0 = time.time()


def log(msg):
    print(f'[{time.time() - T0:7.1f}s] {msg}', file=sys.stderr, flush=True)


def hx(b):
    return b.hex() if b is not None else None


def jdump(x):
    return json.dumps(x, ensure_ascii=False, separators=(',', ':'))


def kinds_of(file_format):
    """The kinds a keyed file of that format holds, in their order (a part's kind index)."""
    if file_format == FILE_FORMAT_MORE:
        return KINDS + MORE_KINDS
    if file_format == FILE_FORMAT:
        return list(KINDS)
    raise SystemExit(f'not a keyed file format: {file_format!r}')


def trim(x):
    """The record without its trailing empty fields (null, 0, [], ''): builder/more.py's records' own rule."""
    while x and (x[-1] is None or x[-1] == [] or x[-1] == '' or (type(x[-1]) is int and x[-1] == 0)):
        x.pop()
    return x


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
                  [('file_format', FILE_FORMAT), ('layout', 'narrow')])
    c.execute('COMMIT')
    c.execute('DETACH f')
    c.execute('VACUUM')
    ok = c.execute('PRAGMA quick_check').fetchone()[0]
    c.close()
    if ok != 'ok':
        raise SystemExit(f'{out}: quick_check {ok}')
    log(f'tables file: {os.path.getsize(out):,} bytes')


# --- the keyed layout: entries, then buckets -------------------------------------------

def _h_gid(raw):
    return int.from_bytes(raw[:8], 'big') >> 1


def _h_text(s):
    return int.from_bytes(hashlib.sha1(s.encode('utf-8')).digest()[:8], 'big') >> 1


def _merge(streams):
    """streams: {name: iterator of (id, row)} each sorted by id. Yields (id,
    {name: [rows]}) in id order."""
    heads = {n: next(it, None) for n, it in streams.items()}
    while True:
        live = [h[0] for h in heads.values() if h is not None]
        if not live:
            return
        i = min(live)
        got = {}
        for n, it in streams.items():
            rows = []
            while heads[n] is not None and heads[n][0] == i:
                rows.append(heads[n][1])
                heads[n] = next(it, None)
            got[n] = rows
        yield i, got


def _by(c, sql):
    for r in c.execute(sql):
        yield r[0], r[1:]


def _grouped(cur):
    """(r[0], [r[1:] ...]) for runs of rows with the same first field, in the cursor's order."""
    cur_k, acc = None, []
    for r in cur:
        if r[0] != cur_k:
            if acc:
                yield cur_k, acc
            cur_k, acc = r[0], []
        acc.append(r[1:])
    if acc:
        yield cur_k, acc


def entries(narrow_db, out, more_db=None):
    """Every entry of the keyed layout, from the narrow file (so both layouts
    hold the same data by construction), into e(kind, h, k, v); v is the JSON
    a lookup's answer is made from. With more_db (builder/more.py entries), its
    records too: folded into art and rel (FOLD), the rest as MORE_KINDS (the
    names of link types and attributes as kind rol); the file's format in
    em(key, value)."""
    if os.path.exists(out):
        os.remove(out)
    src = sqlite3.connect(f'file:{narrow_db}?mode=ro', uri=True)
    more = sqlite3.connect(f'file:{more_db}?mode=ro', uri=True) if more_db else None
    o = sqlite3.connect(out, isolation_level=None)
    o.execute('PRAGMA journal_mode=OFF')
    o.execute('PRAGMA synchronous=OFF')
    o.execute('CREATE TABLE e (kind TEXT NOT NULL, h INTEGER NOT NULL, k TEXT NOT NULL, v TEXT NOT NULL, '
              'PRIMARY KEY (kind, k)) WITHOUT ROWID')
    o.execute('CREATE TABLE em (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID')
    o.execute('BEGIN')
    n = {}
    took = {a: 0 for adds in FOLD.values() for a in adds}

    def folded(kind, h, k):
        """The fields FOLD appends to `kind`'s record under k: each add's record, [] for one it lacks, trailing empties
        dropped ([] when it has none of them)."""
        if more is None:
            return []
        got = []
        for a in FOLD[kind]:
            r = more.execute('SELECT v FROM e WHERE kind = ? AND h = ? AND k = ?', (a, h, k)).fetchone()
            if r:
                took[a] += 1
            got.append(json.loads(r[0]) if r else [])
        return trim(got)

    def put(kind, rows):
        before = o.total_changes
        o.executemany('INSERT INTO e VALUES (?,?,?,?)', rows)
        n[kind] = n.get(kind, 0) + o.total_changes - before

    # art: one artist with its parts, merged by artist id from sorted streams.
    streams = {
        'a': _by(src.cursor(), 'SELECT a.id, a.gid, a.name, a.ngroups, i.comment, i.type, i.country '
                               'FROM artist a LEFT JOIN artist_info i ON i.artist = a.id ORDER BY a.id'),
        'al': _by(src.cursor(), 'SELECT artist, name, locale, prim FROM artist_alias ORDER BY artist'),
        'f': _by(src.cursor(), 'SELECT r.a, r.kind, r.epon, t.gid, t.name, r.named, r.skey FROM artist_rel r '
                               'JOIN artist t ON t.id = r.b ORDER BY r.a, r.kind, r.skey'),
        'b': _by(src.cursor(), 'SELECT r.b, r.kind, r.epon, t.gid, t.name, r.named, r.skey FROM artist_rel r '
                               'JOIN artist t ON t.id = r.a ORDER BY r.b, r.kind, r.skey'),
        'u': _by(src.cursor(), 'SELECT artist, svc, ext FROM artist_url ORDER BY artist, svc, ext'),
    }

    def art():
        for _, p in _merge(streams):
            if not p['a']:
                continue          # parts of an artist the table lacks (none are written that way)
            gid, name, ng, cmt, typ, cty = p['a'][0]
            al = sorted(([nm, loc, pr] for nm, loc, pr in p['al']), key=lambda x: (x[0], x[1]))
            # A link whose band is named after its member (build_pack.py `named`) carries a 6th field, 1; no other has one.
            one = lambda d, k, ep, g, nm, named: [d, k, ep, hx(g), nm] + ([1] if named else [])
            rels = [one(0, k, ep, g, nm, na) for k, ep, g, nm, na, _ in p['f']] + \
                   [one(1, k, ep, g, nm, na) for k, ep, g, nm, na, _ in p['b']]
            doc = [name, ng, cmt, typ, cty, al, rels, [[s_, e] for s_, e in p['u']]]
            yield 'art', _h_gid(gid), hx(gid), jdump(doc + folded('art', _h_gid(gid), hx(gid)))
        for g, a in src.cursor().execute('SELECT r.gid, t.gid FROM artist_redirect r JOIN artist t ON t.id = r.artist'):
            yield 'art', _h_gid(g), hx(g), jdump(hx(a))
    put('art', art())
    log(f"entries art {n['art']:,}")

    # prf: the abo and lnk of an artist art holds no record for (a redirect counts as none), as [abo, lnk].
    if more is not None:
        def prf():
            for k, rs in _grouped(more.execute("SELECT k, h, kind, v FROM e WHERE kind IN ('abo', 'lnk') "
                                               "ORDER BY k, kind")):
                r = o.execute("SELECT v FROM e WHERE kind = 'art' AND k = ?", (k,)).fetchone()
                if r and r[0].startswith('['):
                    continue
                by = {kind: v for _, kind, v in rs}
                yield 'prf', rs[0][0], k, jdump(trim([json.loads(by[a]) if a in by else [] for a in FOLD['art']]))
        put('prf', prf())
        log(f"entries prf {n['prf']:,} (art took abo {took['abo']:,}, lnk {took['lnk']:,})")

    # grp: the extras (today's rgx and its three tables), by group id.
    ext = {}
    for i, g, boot, dg in src.execute('SELECT id, gid, boot, discogs FROM rgx'):
        ext[i] = [g, [boot, dg, [], [], []]]
    for i, nm in src.execute('SELECT rg, name FROM rg_alias ORDER BY rg, name'):
        ext[i][1][2].append(nm)
    for i, t in src.execute('SELECT rg, title FROM rg_edition ORDER BY rg, title'):
        ext[i][1][3].append(t)
    for i, k, u in src.execute('SELECT rg, kind, url FROM rg_url ORDER BY rg, kind'):
        ext[i][1][4].append([k, u])

    # lst: an artist's group list, in the tables file's own order,
    # each group carrying its extras as an 8th field when it has any (a group
    # shared by several artists is held once per artist).
    def lst():
        cur, rows = None, []
        for a, i, g, title, date, typ, sec, cover, credit in src.execute(
                'SELECT a.gid, g.id, g.gid, g.title, g.date, g.type, g.sec, g.cover, c.name FROM artist_rg ar '
                'JOIN artist a ON a.id = ar.artist JOIN rg g ON g.id = ar.rg LEFT JOIN credit c ON c.id = g.credit '
                'ORDER BY ar.artist, ar.rg'):
            if a != cur:
                if rows:
                    yield 'lst', _h_gid(cur), hx(cur), jdump(rows)
                cur, rows = a, []
            rows.append([hx(g), title, date, typ, sec, cover, credit] + ([ext[i][1]] if i in ext else []))
        if rows:
            yield 'lst', _h_gid(cur), hx(cur), jdump(rows)
    put('lst', lst())
    log(f"entries lst {n['lst']:,}")

    put('grp', (('grp', _h_gid(g), hx(g), jdump(x)) for g, x in ext.values()))
    del ext
    log(f"entries grp {n['grp']:,}")

    def names(kind, sql, row):
        cur, rows = None, []
        for r in src.execute(sql):
            if not isinstance(r[0], str):           # a barcode: its digits are the key text
                r = (str(r[0]),) + tuple(r[1:])
            if r[0] != cur:
                if rows:
                    yield kind, _h_text(cur), cur, jdump(rows)
                cur, rows = r[0], []
            rows.append(row(r))
        if rows:
            yield kind, _h_text(cur), cur, jdump(rows)
    put('nam', names('nam',
        'SELECT n.key, a.gid, a.name, a.ngroups, i.comment, i.type, i.country, '
        "(SELECT al.name FROM artist_alias al WHERE al.artist = a.id AND al.locale = 'en' AND al.prim = 1 "
        ' ORDER BY al.name LIMIT 1) '
        'FROM artist_name n JOIN artist a ON a.id = n.artist LEFT JOIN artist_info i ON i.artist = a.id '
        'ORDER BY n.key, a.ngroups DESC, a.name, a.id',
        lambda r: [hx(r[1]), r[2], r[3], r[4], r[5], r[6], r[7]]))
    put('ali', names('ali', 'SELECT n.key, a.gid, a.name, a.ngroups FROM alias_name n JOIN artist a ON a.id = n.artist '
                            'ORDER BY n.key, a.ngroups DESC, a.name, a.id', lambda r: [hx(r[1]), r[2], r[3]]))
    log(f"entries nam {n['nam']:,}, ali {n['ali']:,}")

    put('rel', (('rel', _h_gid(g), hx(g), jdump([hx(rg), t, s_] + folded('rel', _h_gid(g), hx(g))))
                for g, rg, t, s_ in src.execute('SELECT gid, rg, type, sec FROM release')))
    put('bar', names('bar', 'SELECT b.code, g.gid FROM barcode b JOIN rg g ON g.id = b.rg '
                            'ORDER BY b.code, g.gid', lambda r: hx(r[1])))
    # The key text: a number as digits, an id kept as text (Spotify's, Qobuz's
    # with letters) as it is.
    put('svc', names('svc', "SELECT s.svc || ':' || s.ext, g.gid FROM svc_album s JOIN rg g ON g.id = s.rg "
                            "ORDER BY s.svc || ':' || s.ext, g.gid", lambda r: hx(r[1])))
    log(f"entries rel {n['rel']:,}, bar {n['bar']:,}, svc {n['svc']:,}")

    if more is not None:
        # A release's labels and credits whose release the file lacks have no record to ride on: counted, left out.
        for a in FOLD['rel']:
            (have,) = more.execute('SELECT count(*) FROM e WHERE kind = ?', (a,)).fetchone()
            if have != took[a]:
                print(f'::warning::{have - took[a]:,} {a} record(s) name a release the file lacks: left out',
                      file=sys.stderr, flush=True)
        for kind in ('crd', 'who', 'pgr'):
            put(kind, more.execute('SELECT kind, h, k, v FROM e WHERE kind = ? ORDER BY h, k', (kind,)))
        log(f"entries rel took rlb {took['rlb']:,}, rcr {took['rcr']:,}; crd {n['crd']:,}, who {n['who']:,}, "
            f"pgr {n['pgr']:,}")
        (names,) = more.execute("SELECT value FROM meta WHERE key = 'more_names'").fetchone()
        put('rol', (('rol', _h_text(k), k, jdump(v)) for k, v in sorted(json.loads(names).items())))
        o.execute('INSERT INTO em VALUES (?,?)', ('file_format', FILE_FORMAT_MORE))
        more.close()
    else:
        o.execute('INSERT INTO em VALUES (?,?)', ('file_format', FILE_FORMAT))
    o.execute('COMMIT')
    o.execute('CREATE INDEX e_bucket ON e (kind, h, k)')
    src.close()
    o.close()
    return n


def _deflate(b):
    c = zlib.compressobj(9, zlib.DEFLATED, -15, 9)
    return c.compress(b) + c.flush()


def _key_bytes(kind, k):
    return bytes.fromhex(k) if kind in GID_KINDS else k.encode('utf-8')


def bits_of(file_format, bits):
    """{kind: bucket count} for a file of that format: `bits` (a whole number) for every kind, or 'sized' (the more
    file only): SIZED."""
    kinds = kinds_of(file_format)
    if bits == 'sized':
        if file_format != FILE_FORMAT_MORE:
            raise SystemExit(f'sized: a {FILE_FORMAT_MORE} file only (this is {file_format})')
        return {k: SIZED[k] for k in kinds}
    if isinstance(bits, bool) or not isinstance(bits, int) or not 0 <= bits <= 24:
        raise SystemExit(f'bits: a whole number 0 to 24, or sized; not {bits!r}')
    return {k: bits for k in kinds}


def keyed(entries_db, narrow_db, bits, out):
    """The keyed file (bits 0 = one row per entry; 'sized' = each kind at its
    SIZED count, a more file only): every kind a table (k BLOB PRIMARY KEY, v
    BLOB) WITHOUT ROWID, no other index; a bucket's v is its entries as one JSON
    object {key: entry}, deflated. The same entries give the same bytes, so a
    delta holds only the buckets that changed. The format is the entries'
    (FILE_FORMAT, or FILE_FORMAT_MORE with MORE_KINDS, the counts in meta's
    bits_by_kind, never a `bits` row a reader could take for every kind).
    Returns the values over the 2 MB cap (kind, bucket, bytes)."""
    if os.path.exists(out):
        os.remove(out)
    src = sqlite3.connect(f'file:{entries_db}?mode=ro', uri=True)
    em = dict(src.execute('SELECT key, value FROM em'))
    fmt = em['file_format']
    kinds = kinds_of(fmt)
    by_kind = bits_of(fmt, bits)
    nar = sqlite3.connect(f'file:{narrow_db}?mode=ro', uri=True)
    meta = dict(nar.execute('SELECT key, value FROM meta'))
    names = {t: dict(nar.execute(f'SELECT id, name FROM {t}')) for t in ('artist_type', 'rg_type', 'rg_sec')}
    probe = dict(nar.execute('SELECT name, key FROM namekey_probe'))
    nar.close()
    meta.update({'layout': 'keyed', 'file_format': fmt,
                 'names': jdump({t: {str(i): v for i, v in d.items()} for t, d in names.items()}),
                 'namekey_probe': jdump(probe)})
    if fmt == FILE_FORMAT_MORE:
        meta.pop('bits', None)
        meta['bits_by_kind'] = jdump(by_kind)
    else:
        meta['bits'] = str(bits)
    o = sqlite3.connect(out, isolation_level=None)
    o.execute('PRAGMA journal_mode=OFF')
    o.execute('PRAGMA synchronous=OFF')
    o.execute('PRAGMA page_size=4096')
    o.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID')
    for kind in kinds:
        o.execute(f'CREATE TABLE {kind} (k BLOB PRIMARY KEY, v BLOB NOT NULL) WITHOUT ROWID')
    o.execute('BEGIN')
    o.executemany('INSERT INTO meta VALUES (?,?)', sorted(meta.items()))
    over = []
    for kind in kinds:
        kb = by_kind[kind]
        if kb == 0:
            # Key order: lower-case hex sorts as its bytes, and SQLite's TEXT
            # order is the UTF-8 bytes' order, so no sort is needed here.
            def rows():
                for k, v in src.execute('SELECT k, v FROM e WHERE kind = ? ORDER BY k', (kind,)):
                    b = v.encode('utf-8')
                    if len(b) > CAP:
                        over.append((kind, k, len(b)))
                    yield _key_bytes(kind, k), b
            o.executemany(f'INSERT INTO {kind} VALUES (?,?)', rows())
            continue
        cur = src.execute('SELECT h, k, v FROM e WHERE kind = ? ORDER BY h, k', (kind,))
        shift = 63 - kb

        def buckets():
            b, parts = None, []
            for h, k, v in cur:
                x = h >> shift
                if x != b and parts:
                    yield b, parts
                    parts = []
                b = x
                parts.append(jdump(k) + ':' + v)
            if parts:
                yield b, parts
        nb = size = top = 0
        for b, parts in buckets():
            v = _deflate(('{' + ','.join(parts) + '}').encode('utf-8'))
            if len(v) > CAP:
                over.append((kind, b, len(v)))
            o.execute(f'INSERT INTO {kind} VALUES (?,?)', (b.to_bytes(4, 'big'), v))
            nb, size, top = nb + 1, size + len(v), max(top, len(v))
        log(f'{kind} at {kb} bits: {nb:,} buckets, {size:,} bytes, average {size // max(1, nb):,}, largest {top:,}')
    o.execute('COMMIT')
    o.execute('VACUUM')
    o.close()
    src.close()
    log(f'keyed {fmt} bits {bits}: {os.path.getsize(out):,} bytes'
        + (f'; {len(over)} value(s) OVER THE 2 MB CAP' if over else ''))
    return over



def main():
    cmd, a = (sys.argv[1] if len(sys.argv) > 1 else ''), sys.argv[1:]
    if cmd == 'aliases' and len(a) == 4:
        aliases(a[1], a[2], a[3])
    elif cmd == 'narrow' and len(a) == 5:
        narrow(a[1], a[2], a[3], a[4])
    elif cmd == 'entries' and len(a) in (3, 4):
        entries(a[1], a[2], a[3] if len(a) == 4 else None)
    elif cmd == 'keyed' and len(a) == 5:
        if a[3] != 'sized' and not a[3].isdigit():
            raise SystemExit(f'bits: a whole number, or sized; not {a[3]!r}')
        over = keyed(a[1], a[2], a[3] if a[3] == 'sized' else int(a[3]), a[4])
        print(json.dumps({'over_cap': over}))
        if over:
            raise SystemExit(f'{len(over)} value(s) over the cap')
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
