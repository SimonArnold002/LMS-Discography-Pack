"""Build Discography's MusicBrainz pack from the MusicBrainz dump (CC0).

The page keeps its first list (ListenBrainz + the community API). The pack holds
only what MusicBrainz alone gives Discography:
  artist side: name, aliases (locale, primary), Qobuz/Deezer/Discogs ids,
               member-of-band and collaboration links (eponymous flag), and the
               number of release groups the artist is credited on (any position).
               A row for every artist with any of those or any release group.
  group side:  a row only for a group with an alias, an official edition title
               (not its own title), one of the 5 release-page links, a Discogs
               master/release, or a bootleg-only verdict.
  redirects:   merged-away artist gids -> the current artist.
  same-name:   DSC's _nameKey for every artist row (namekeys.pl runs the
               plugin's own _norm), with disambiguation, type and country,
               ranked by group count on the device.
  release map: a release -> its group's gid and types, for every release of
               a SHARED group (2+ credited artists or a special-purpose
               one), and for a release whose own credit names an artist its
               group's credit does not (Centro-Matic's "South San Gabriel
               Songs/Music": the release credits Centro-Matic, the group South
               San Gabriel, so no list of Centro-Matic's places it; 26,865 such
               releases in the 2026-10-14 dump, counted by the DSC session).
               A release credit that only spells the group's artists
               differently is left out: their own lists place it.

FORMAT 3 (2026-10-16, for updates by delta):
  * artist_rel carries each relation's OWN sort key (`skey`, a byte string
    that sorts as MusicBrainz orders relations), where format 2 carried a rank
    over every relation in the database: one new link renumbered the whole
    table, so a delta would have carried all 612k rows.
  * meta holds `dump` (the fullexport directory name, e.g. 20261014-150514),
    the version a delta is built from and applied to.
  * namekey_probe: names and the keys this build's _norm gave them. The device
    computes the same keys with ITS _norm and turns the same-name list off if
    they differ (a plugin and a pack built with different matcher code).
FORMAT 4 (2026-10-24, so a device can compact its pack after a delta; the
plugin's ledger, A2 `THE PACK GROWS IN PLACE`): the same rows with NO secondary
index, because a delta applied in place splits B-tree pages and only a copy of
each table in ONE statement packs them full again, which an index (rebuilt in
one go) would block:
  * artist_gk (gk = the gid's first 8 bytes, id) replaces the unique artist
    gid index, and rgx is KEYED by (gk, id) itself (nothing reads a group by
    its id), so a group is one probe; the plugin checks the full gid;
  * the table artist_rel_b (b, a, kind) replaces the index on artist_rel(b);
  * artist_info's comment, type and country are columns of artist.
  * no ANALYZE: a fresh pack and a device's rewritten one plan alike.
Measured on the 2026-10-21 dump: 378.2 MB against format 3's 415.3 MB.
Keys are MusicBrainz's own integer row ids (stable dump to dump).

Tables read from mbdump/: artist, artist_alias, artist_credit_name,
artist_gid_redirect, artist_type, iso_3166_1, l_artist_artist, l_artist_url,
l_release_group_url, link, link_attribute, link_attribute_type, link_type,
release, release_group, release_group_alias, release_group_primary_type,
release_group_secondary_type, release_group_secondary_type_join,
release_status, url; plus TIMESTAMP, SCHEMA_SEQUENCE, REPLICATION_SEQUENCE.

Usage: python3 -I build_pack.py <dir holding mbdump/> <out.db> --dsc <LMS-Discography checkout> [--dump ID]
       python3 -I build_pack.py --format      (prints the format number)
"""
import argparse, os, re, sqlite3, struct, subprocess, sys, time, uuid

FORMAT = 4

# Names whose keys the device recomputes to prove its matcher folds names as
# this build's did: accents, stroke and ligature letters, marks inside and
# outside words, ampersands, non-Latin scripts.
PROBE_NAMES = [
    'Björk', 'Sigur Rós', 'Motörhead', 'Mötley Crüe', 'Hüsker Dü', 'Røyksopp',
    'Ólafur Arnalds', 'Sinéad O’Connor', 'Łona', 'Đorđe Balašević', 'Æsop Rock',
    'The B‐52’s', "Guns N' Roses", "Jane's Addiction", 'Rag’n’Bone Man',
    'AC/DC', 'P!nk', 'Ke$ha', '$uicideboy$', '!!!', 'Panic! at the Disco',
    'Simon & Garfunkel', 'Florence + the Machine', 'Layo & Bushwacka!',
    'The Beatles', 'Belle and Sebastian', 'Yo La Tengo', '米津玄師', 'Кино',
    '王菲', 'Земфира', 'Daryl Hall & John Oates (Hall and Oates)',
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src', nargs='?')
    ap.add_argument('out', nargs='?')
    ap.add_argument('--dsc', help='LMS-Discography checkout (its Discography/Sources.pm gives the name keys)')
    ap.add_argument('--dump', default='', help='the fullexport directory name, stored as meta.dump')
    ap.add_argument('--format', action='store_true', help='print the format number and exit')
    args = ap.parse_args()
    if args.format:
        print(FORMAT)
        return
    if not (args.src and args.out and args.dsc):
        ap.error('src, out and --dsc are required')
    build(args.src.rstrip('/') + '/mbdump', args.out, args.dsc, args.dump)


T0 = time.time()


def log(msg):
    print(f'[{time.time() - T0:7.1f}s] {msg}', flush=True)


ESC = {'t': '\t', 'n': '\n', 'r': '\r', '\\': '\\', 'b': '\b', 'f': '\f', 'v': '\v'}


def unesc(s):
    return re.sub(r'\\(.)', lambda m: ESC.get(m.group(1), m.group(1)), s)


def gid(s):
    return uuid.UUID(s).bytes


# A relation's sort key, as MusicBrainz orders relations of one type (measured
# against the mirror's replies, 2026-10-16: Bowie's dated "David Bowie & The
# Buzz" before his undated bands, Miles Davis's ended "Charlie Parker Septet"
# after the rest): link_order, begin date, end date, ended, then the link id.
# Packed big-endian with a missing date part as the field's largest value, so
# the bytes sort as the tuple (PostgreSQL's NULLS LAST) and SQLite's ORDER BY
# on the BLOB is that order.
SKEY = struct.Struct('>IHBBHBBBI')
YMAX, BMAX = 0xFFFF, 0xFF


def _part(v, top):
    if v is None:
        return top
    v = int(v)
    return min(max(v, 0), top - 1)


def skey(link_order, when, link_id):
    by, bm, bd, ey, em, ed, ended = when
    return SKEY.pack(min(max(int(link_order or 0), 0), 0xFFFFFFFF),
                     _part(by, YMAX), _part(bm, BMAX), _part(bd, BMAX),
                     _part(ey, YMAX), _part(em, BMAX), _part(ed, BMAX),
                     1 if ended else 0, int(link_id) & 0xFFFFFFFF)


NO_WHEN = (None, None, None, None, None, None, 0)

AA_KIND = {'member of band': 1, 'collaboration': 2}
RG_URL_KINDS = {'allmusic': 1, 'discogs': 2, 'wikipedia': 3, 'review': 4, 'official homepage': 5}
SVC_RE = [
    (1, re.compile(r'^https?://(?:www\.|open\.|play\.)?qobuz\.com/(?:[a-z]{2}-[a-z]{2}/)?(?:artist/(\d+)|interpreter/[^/]+/(\d+))(?:[/?#]|$)', re.I)),
    (2, re.compile(r'^https?://(?:www\.)?deezer\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?artist/(\d+)(?:[/?#]|$)', re.I)),
    (3, re.compile(r'^https?://(?:www\.)?discogs\.com/(?:[a-z]{2}/)?artist/(\d+)(?:[/?#-]|$)', re.I)),
]
DG_RE = re.compile(r'^https?://(?:www\.)?discogs\.com/(?:[a-z]{2}/)?(master|release)/(\d+)(?:[/?#-]|$)', re.I)

# The special-purpose artists (API.pm %MB_SPECIAL_ARTIST).
SPECIAL_GIDS = {'89ad4ac3-39f7-470e-963a-56509c546377', '125ec42a-7229-4250-afc5-e057484327fe',
                'eec63d3c-3b81-4ad4-b1e4-7c147d4d2b61', 'f731ccc4-e22a-43af-a747-64213329e088',
                '9be7f096-97ec-4615-8957-8d40b5dcbc41'}

SCHEMA = '''
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


def build(SRC, OUT, dsc, dump_id):
    def rows(table, ncols=None):
        """PostgreSQL COPY text: tab separated, \\N = null, backslash escapes."""
        with open(f'{SRC}/{table}', encoding='utf-8', newline='\n') as f:
            for line in f:
                r = line.rstrip('\n').split('\t')
                if ncols and len(r) != ncols:
                    raise SystemExit(f'{table}: {len(r)} columns, expected {ncols}: {line[:200]!r}')
                yield [None if c == '\\N' else (unesc(c) if '\\' in c else c) for c in r]

    # --- link types, attributes, links --------------------------------------
    lt = {int(r[0]): (r[4], r[5], r[6]) for r in rows('link_type')}   # entity_type0, entity_type1, name
    attr = {int(r[0]): r[5] for r in rows('link_attribute_type')}
    EPON = [k for k, v in attr.items() if v == 'eponymous']
    assert EPON, 'no eponymous attribute type'
    link_type, link_when = {}, {}
    for r in rows('link'):
        i = int(r[0])
        link_type[i] = int(r[1])
        link_when[i] = tuple(int(x) if x is not None else None for x in r[2:8]) + (1 if r[10] == 't' else 0,)
    epon_links = {int(r[0]) for r in rows('link_attribute') if int(r[1]) in EPON}
    log(f'link types {len(lt)}, links {len(link_type)}, eponymous links {len(epon_links)}')

    # --- artist-artist links --------------------------------------------------
    # Discography keeps a target's FIRST link, so a (a, b, kind) row takes its
    # smallest key.
    rel = {}            # (a, b, kind) -> [epon, skey]
    for r in rows('l_artist_artist'):
        lk = int(r[1])
        t = lt.get(link_type.get(lk))
        if not t or t[0] != 'artist' or t[1] != 'artist':
            continue
        kind = AA_KIND.get(t[2])
        if not kind:
            continue
        a, b, lid = int(r[2]), int(r[3]), int(r[0])
        ep = 1 if lk in epon_links else 0
        key = skey(r[6], link_when.get(lk, NO_WHEN), lid)
        k = (a, b, kind)
        if k in rel:
            rel[k][0] |= ep
            rel[k][1] = min(rel[k][1], key)
        else:
            rel[k] = [ep, key]
    log(f'artist links kept {len(rel)}')

    # --- url rels ---------------------------------------------------------------
    a_url = []          # (artist, url id)
    for r in rows('l_artist_url'):
        t = lt.get(link_type.get(int(r[1])))
        if t and t[0] == 'artist' and t[1] == 'url':
            a_url.append((int(r[2]), int(r[3])))
    # Discography takes a group's FIRST link of a kind. MusicBrainz orders them by
    # link_order, dates, ended, then the URL itself (measured against the mirror,
    # 2026-10-16: 79 of 85 differing links were the alphabetically first URL, the
    # other 6 the first that had not ended), so the URL text completes the key.
    rg_url = []         # (key, rg, kind, url id)
    for r in rows('l_release_group_url'):
        lk = int(r[1])
        t = lt.get(link_type.get(lk))
        if t and t[0] == 'release_group' and t[1] == 'url' and t[2] in RG_URL_KINDS:
            rg_url.append((skey(r[6], link_when.get(lk, NO_WHEN), 0)[:-4], int(r[2]), RG_URL_KINDS[t[2]], int(r[3])))
    del link_when
    need_a = {u for _, u in a_url}
    need_rg = {u for *_, u in rg_url}
    url_txt = {}
    for r in rows('url'):
        i = int(r[0])
        if i in need_rg or (i in need_a and ('qobuz' in r[2] or 'deezer' in r[2] or 'discogs' in r[2])):
            url_txt[i] = r[2]
    log(f'artist url links {len(a_url)}, group url links {len(rg_url)}, url texts kept {len(url_txt)}')
    del need_a, need_rg

    a_svc = {}          # artist -> set((svc, ext))
    for a, u in a_url:
        s = url_txt.get(u)
        if not s:
            continue
        for k, rx in SVC_RE:
            m = rx.match(s)
            if m:
                ext = next((g for g in m.groups() if g), None)
                if ext:
                    a_svc.setdefault(a, set()).add((k, int(ext)))
                break
    del a_url

    # --- aliases ----------------------------------------------------------------
    a_alias = {}        # artist -> {(name, locale): prim}
    for r in rows('artist_alias', 16):
        a = int(r[1])
        key = (r[2], r[3] or '')
        d = a_alias.setdefault(a, {})
        d[key] = max(d.get(key, 0), 1 if r[14] == 't' else 0)
    log(f'artists with aliases {len(a_alias)}')

    special = {int(r[0]) for r in rows('artist', 19) if r[1] in SPECIAL_GIDS}
    log(f'special-purpose artists {len(special)}')

    # --- credits -> group counts -------------------------------------------------
    # SHARED groups: credited to 2+ artists or to a special-purpose one. Their
    # releases make the release map, with the releases whose own credit names
    # an artist the group's lacks (so `credit` lives on to the release pass).
    credit = {}
    for r in rows('artist_credit_name', 5):
        credit.setdefault(int(r[0]), []).append(int(r[2]))
    sec = {}
    for r in rows('release_group_secondary_type_join', 3):
        sec[int(r[0])] = sec.get(int(r[0]), 0) | (1 << int(r[1]))
    ngroups, rg_meta, shared = {}, {}, set()
    for r in rows('release_group', 8):
        rid = int(r[0])
        # gid, name, artist credit, primary type, secondary types
        rg_meta[rid] = (r[1], r[2], int(r[3]), int(r[4]) if r[4] else None, sec.get(rid))
        who = set(credit.get(int(r[3]), ()))
        for a in who:
            ngroups[a] = ngroups.get(a, 0) + 1
        if len(who) >= 2 or who & special:
            shared.add(rid)
    del sec
    log(f'release groups {len(rg_meta)}, credited artists {len(ngroups)}, shared groups {len(shared)}')

    # --- releases: official verdict + edition titles ----------------------------
    status = {int(r[0]): r[1] for r in rows('release_status')}
    has_rel, any_off, eds, relmap, own_credit = set(), set(), {}, [], 0
    for r in rows('release', 14):
        g = int(r[4])
        has_rel.add(g)
        if g in shared:
            relmap.append((gid(r[1]), g))
        elif r[3] and int(r[3]) != rg_meta[g][2] \
                and not set(credit.get(int(r[3]), ())) <= set(credit.get(rg_meta[g][2], ())):
            relmap.append((gid(r[1]), g))
            own_credit += 1
        st = status.get(int(r[5])) if r[5] else None
        if st is None or st.lower() == 'official':
            any_off.add(g)
            t = r[2]
            if t and t != rg_meta[g][1]:
                eds.setdefault(g, set()).add(t)
    boot = has_rel - any_off
    log(f'groups with releases {len(has_rel)}, bootleg-only {len(boot)}, with edition titles {len(eds)}')
    log(f'release map {len(relmap)}: {len(relmap) - own_credit} of shared groups, '
        f'{own_credit} whose own credit names an artist their group\'s does not')
    del has_rel, any_off, credit

    rg_alias = {}
    for r in rows('release_group_alias', 16):
        g, n = int(r[1]), r[2]
        if n and n != rg_meta[g][1]:
            rg_alias.setdefault(g, set()).add(n)

    rg_links, rg_dg = {}, {}
    for key, g, kind, u in sorted(rg_url, key=lambda x: (x[0], url_txt.get(x[3]) or '')):
        s = url_txt.get(u)
        if not s:
            continue
        rg_links.setdefault(g, {}).setdefault(kind, s)
        if kind == 2 and g not in rg_dg:
            m = DG_RE.match(s)
            if m:
                rg_dg[g] = int(m.group(2)) * (1 if m.group(1).lower() == 'master' else -1)
    del rg_url, url_txt

    # --- which artists get a row ------------------------------------------------
    linked = set()
    for a, b, _ in rel:
        linked.add(a)
        linked.add(b)
    keep = linked | set(a_alias) | set(a_svc) | set(ngroups)
    iso = {int(r[0]): r[1] for r in rows('iso_3166_1', 2)}
    art, ainfo = {}, {}
    for r in rows('artist', 19):
        i = int(r[0])
        if i in keep:
            art[i] = (r[1], r[2])
            cmt, typ = r[13] or None, int(r[10]) if r[10] else None
            cty = iso.get(int(r[11])) if r[11] else None
            if cmt or typ or cty:
                ainfo[i] = (cmt, typ, cty)
    redir = [(r[0], int(r[1])) for r in rows('artist_gid_redirect', 3) if int(r[1]) in art]
    log(f'artist rows {len(art)}, redirects {len(redir)}')

    # --- the same-name key: DSC's own _nameKey, by the real Perl -----------------
    aname, probe = namekeys(dsc, art)
    log(f'same-name keys {len(aname)} for {len(art)} artists; probe {len(probe)} names')

    # --- write ------------------------------------------------------------------
    if os.path.exists(OUT):
        os.remove(OUT)
    c = sqlite3.connect(OUT)
    c.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA page_size=4096;' + SCHEMA)
    c.execute('BEGIN')
    c.executemany('INSERT INTO artist VALUES (?,?,?,?,?,?,?)',
                  ((i, gid(g), n, ngroups.get(i, 0)) + ainfo.get(i, (None, None, None))
                   for i, (g, n) in sorted(art.items())))
    c.executemany('INSERT INTO artist_gk VALUES (?,?)',
                  sorted((gid(g)[:8], i) for i, (g, n) in art.items()))
    c.executemany('INSERT INTO artist_alias VALUES (?,?,?,?)',
                  ((a, n, loc, p) for a in sorted(a_alias) if a in art for (n, loc), p in a_alias[a].items()))
    c.executemany('INSERT INTO artist_url VALUES (?,?,?)',
                  ((a, k, e) for a in sorted(a_svc) if a in art for k, e in a_svc[a]))
    c.executemany('INSERT INTO artist_rel VALUES (?,?,?,?,?)',
                  ((a, b, k, ep, sk) for (a, b, k), (ep, sk) in sorted(rel.items()) if a in art and b in art))
    c.executemany('INSERT INTO artist_rel_b VALUES (?,?,?)',
                  sorted((b, a, k) for (a, b, k) in rel if a in art and b in art))
    c.executemany('INSERT INTO artist_redirect VALUES (?,?)', ((gid(g), a) for g, a in redir))
    groups = sorted(set(rg_alias) | set(eds) | set(rg_links) | set(rg_dg) | boot)
    c.executemany('INSERT INTO rgx VALUES (?,?,?,?,?)',
                  sorted((gid(rg_meta[g][0])[:8], g, gid(rg_meta[g][0]), 1 if g in boot else 0, rg_dg.get(g))
                         for g in groups))
    c.executemany('INSERT INTO rg_alias VALUES (?,?)', ((g, n) for g in sorted(rg_alias) for n in rg_alias[g]))
    c.executemany('INSERT INTO rg_edition VALUES (?,?)', ((g, t) for g in sorted(eds) for t in eds[g]))
    c.executemany('INSERT INTO rg_url VALUES (?,?,?)',
                  ((g, k, u) for g in sorted(rg_links) for k, u in sorted(rg_links[g].items())))
    c.executemany('INSERT INTO artist_name VALUES (?,?)', sorted(aname))
    c.executemany('INSERT INTO artist_type VALUES (?,?)', [(int(r[0]), r[1]) for r in rows('artist_type', 6)])
    relmap.sort()
    c.executemany('INSERT INTO release VALUES (?,?,?,?)',
                  ((rb, gid(rg_meta[g][0]), rg_meta[g][3], rg_meta[g][4]) for rb, g in relmap))
    c.executemany('INSERT INTO rg_type VALUES (?,?)', [(int(r[0]), r[1]) for r in rows('release_group_primary_type', 6)])
    c.executemany('INSERT INTO rg_sec VALUES (?,?)', [(int(r[0]), r[1]) for r in rows('release_group_secondary_type', 6)])
    c.executemany('INSERT INTO namekey_probe VALUES (?,?)', sorted(probe.items()))
    meta = [('format', str(FORMAT)),
            ('dump_timestamp', open(f'{SRC}/TIMESTAMP').read().strip()),
            ('schema_sequence', open(f'{SRC}/SCHEMA_SEQUENCE').read().strip()),
            ('replication_sequence', open(f'{SRC}/REPLICATION_SEQUENCE').read().strip()),
            ('built', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))]
    if dump_id:
        meta.append(('dump', dump_id))
    c.executemany('INSERT INTO meta VALUES (?,?)', meta)
    c.execute('COMMIT')
    log('rows written; compacting')
    c.execute('VACUUM')
    ok = c.execute('PRAGMA integrity_check').fetchone()[0]
    if ok != 'ok':
        raise SystemExit(f'integrity_check: {ok}')
    counts = {t: c.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for (t,) in
              c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite%' ORDER BY name")}
    c.close()
    log(f'done: {os.path.getsize(OUT):,} bytes, integrity ok')
    for k, v in counts.items():
        print(f'  rows {k:16} {v:>10,}')


def namekeys(dsc, art):
    """(artist_name rows, {probe name: key}) from namekeys.pl, which runs the
    plugin's own _norm. Probe lines go in with ids p0..pN."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'namekeys.pl')
    lines = [f'{i}\t{n}\n' for i, (_, n) in sorted(art.items()) if '\t' not in n and '\n' not in n]
    lines += [f'p{j}\t{n}\n' for j, n in enumerate(PROBE_NAMES)]
    p = subprocess.run(['perl', script, dsc], input=''.join(lines).encode('utf-8'),
                       capture_output=True, check=True)
    aname, probe = set(), {}
    for line in p.stdout.decode('utf-8').splitlines():
        a, k = line.split('\t', 1)
        if a.startswith('p'):
            probe[PROBE_NAMES[int(a[1:])]] = k
        else:
            aname.add((k, int(a)))
    return aname, probe


if __name__ == '__main__':
    main()
