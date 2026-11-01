"""builder/hosted.py and worker/reader.mjs on the made-up dump (tests/fixture.py
plus test_measure_extra's candidate tables): what the hosted pack (today's pack
+ group lists, whole release map, barcodes, album ids, alias keys) holds, that a
delta is proven and applies in place, that the hosted reader gives Pack.pm's
answers on today's pack and the dump's own answers for the new lookups, and that
the comparison with MusicBrainz sorts its answers right and asks the server it is
given at that server's pace (MusicBrainz stubbed). Sizes of a few rows prove nothing; the
runner's run on the real dumps is the measurement.

Needs perl and an LMS-Discography checkout ($DSC_DIR, else ../LMS-Discography)
for the name keys and Pack.pm; the reader's checks need Node 22.5+ ($NODE, else
`node` on the PATH) and are skipped without it. Nothing here asks MusicBrainz.
Run: python3 -m unittest discover -s tests
"""
import contextlib, gzip, io, json, os, re, shutil, sqlite3, subprocess, sys, tempfile, unittest, urllib.parse, uuid
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'builder'))
sys.path.insert(0, HERE)
import fixture, hosted, measure_extra  # noqa: E402
from test_measure_extra import add_candidates  # noqa: E402
from test_pack import DSC, build  # noqa: E402

N, TS = fixture.N, fixture.TS
MIRROR = 'http://mirror.test/ws/2/'      # mbcheck's server; the stubs answer for it


def node():
    exe = os.environ.get('NODE') or shutil.which('node')
    if not exe:
        return None
    r = subprocess.run([exe, '-e', "require('node:sqlite')"],
                       capture_output=True)
    return exe if r.returncode == 0 else None


NODE = node()
# Pack.pm (today's answers) needs perl's DBD::SQLite; the Tests workflow's runner may lack it.
DBD = subprocess.run(['perl', '-MDBD::SQLite', '-e', '1'], capture_output=True).returncode == 0


def plugin_dir(tmp):
    """The plugin code today's answers run on, as the hosted workflow pairs it:
    DSC's Sources.pm (the name keys) with this repo's copy of Pack.pm
    (verify/plugin), so the suite never needs Pack.pm in the DSC checkout."""
    d = os.path.join(tmp, 'dscp')
    os.makedirs(os.path.join(d, 'Discography'), exist_ok=True)
    shutil.copy(os.path.join(DSC, 'Discography', 'Sources.pm'), os.path.join(d, 'Discography'))
    shutil.copy(os.path.join(HERE, '..', 'verify', 'plugin', 'Pack.pm'), os.path.join(d, 'Discography'))
    return d


def add_extras(root, version):
    """What the made-up dump lacks for these checks:
    - aliases: The Sugarcubes' Icelandic name and a Japanese one (by name and
      by locale they sort in opposite orders), one of Björk's with no locale,
      and (version 2) one for Kukl, so the delta carries an alias;
    - a collaboration of Sigur Rós with The Sugarcubes, so The Sugarcubes have
      backward links of both kinds;
    - a group with no primary type, shared by Björk and Sigur Rós, with one
      release, so today's map holds a release whose type is unknown;
    - a second act called Kukl, with one group: the same-name list puts it
      before the first (no groups), so its order is checked;
    - an alias both Kukls carry ("Kukl Group"), so an alias names two acts, the
      one with more groups first; and Björk's alias "Kukl", which is another
      act's own name, so the alias check leaves it to the name keys."""
    d = os.path.join(root, 'mbdump')

    def add(table, rows):
        with open(os.path.join(d, table), 'a', encoding='utf-8', newline='\n') as f:
            for r in rows:
                f.write('\t'.join(N if v is None else str(v) for v in r) + '\n')
    al = lambda i, a, name, loc: [i, a, name, loc, 0, TS, 1, name, N, N, N, N, N, N, 'f', 'f']
    add('artist_alias', [al(10, 2, 'Sykurmolarnir', 'is'), al(12, 2, 'Sugarcubes, The', 'ja'),
                         al(13, 1, 'Björk Guðmundsdóttir', None), al(14, 3, 'Kukl Group', None),
                         al(15, 9, 'Kukl Group', None), al(16, 1, 'Kukl', None)]
                        + ([al(11, 3, 'Kukl Collective', 'is')] if version >= 2 else []))
    add('l_artist_artist', [[105, 13, 6, 2, 0, TS, 0, '', '']])
    add('release_group', [[1005, fixture.g(2005), 'Untyped', 501, None, '', 0, TS]])
    add('release', [[2008, fixture.g(4008), 'Untyped', 501, 1005, 1, N, N, N, N, '', 0, -1, TS],
                    [2009, fixture.g(4009), 'Holidays in Europe', 505, 1006, 1, N, N, N, N, '', 0, -1, TS]])
    add('artist', [[9, fixture.g(9), 'Kukl', 'Kukl', N, N, N, N, N, N, 2, N, N, 'a later band', 0, TS, 'f', N, N]])
    add('artist_credit_name', [[505, 0, 9, 'Kukl', '']])
    add('release_group', [[1006, fixture.g(2006), 'Holidays in Europe', 505, 1, '', 0, TS]])
    return root


def q(db, sql, *args):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def tables(db):
    return {t for (t,) in q(db, "SELECT name FROM sqlite_master WHERE type = 'table'")}


class Hosted(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.f = {}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for v, dump in ((1, '20260101-000000'), (2, '20260102-000000')):
                src = add_extras(add_candidates(fixture.make_dump(os.path.join(cls.tmp, f'd{v}'), v)), v)
                base = build(src, os.path.join(cls.tmp, f'base{v}.db'), dump)
                facts = os.path.join(cls.tmp, f'facts{v}.db')
                measure_extra.facts(src, facts)
                al = os.path.join(cls.tmp, f'aliases{v}.tsv')
                hosted.aliases(base, DSC, al)
                out = os.path.join(cls.tmp, f'hosted-{v}.db')
                hosted.narrow(base, facts, al, out)
                cls.f[v] = {'base': base, 'facts': facts, 'aliases': al, 'hosted': out}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def quiet(self, fn, *a, **k):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fn(*a, **k)


class StubMusicBrainz:
    """MusicBrainz for mbcheck, from the made-up dump: an alias search answers
    the act the alias belongs to, someone else (an id it has no record of) for
    the names in `wrong`, or the act `top` gives (None: nothing found); a title
    search answers the title credited to every act of that name the dump's facts
    credit on a group of that title (two acts are called Kukl), plus one act
    whose name only holds it; a lookup by id (`artist/<mbid>`) answers the dump's
    act with its aliases, or what `lookups` gives (None: no such id)."""
    def __init__(self, base, facts, alias_tsv, wrong=(), top=None, lookups=None):
        self.by_alias = {}
        for _, a, n in hosted.read_aliases(alias_tsv):     # the first act: for "Kukl Group", the one with fewer groups
            self.by_alias.setdefault(n, a)
        self.base, self.facts, self.wrong, self.urls = base, facts, set(wrong), []
        self.top, self.lookups = top or {}, lookups or {}

    def lookup(self, mbid):
        if mbid in self.lookups:
            return self.lookups[mbid]
        try:
            row = q(self.base, 'SELECT id, name FROM artist WHERE gid = ?', uuid.UUID(mbid).bytes)
        except ValueError:
            return None
        if not row:
            return None
        als = [n for (n,) in q(self.base, 'SELECT DISTINCT name FROM artist_alias WHERE artist = ?', row[0][0])]
        return {'id': mbid, 'name': row[0][1], 'aliases': [{'name': n, 'sort-name': n} for n in als]}

    def __call__(self, url):
        self.urls.append(url)
        m = re.search(r'/artist/([^?/]+)\?inc=aliases&fmt=json$', url)
        if m:
            return self.lookup(m.group(1))
        qs = urllib.parse.unquote(url.split('query=', 1)[1].split('&', 1)[0])
        m = re.fullmatch(r'alias:"(.*)"', qs)
        if m:
            name = m.group(1)
            if name in self.top:
                return {'artists': [{'id': self.top[name], 'name': '?'}] if self.top[name] else []}
            if name in self.wrong:
                return {'artists': [{'id': str(uuid.uuid4()), 'name': 'Someone Else'}]}
            a = self.by_alias.get(name)
            if a is None:
                return {'artists': []}
            g = q(self.base, 'SELECT gid FROM artist WHERE id = ?', a)[0][0]
            return {'artists': [{'id': str(uuid.UUID(bytes=g)), 'name': name}]}
        m = re.fullmatch(r'releasegroup:"(.*)" AND artist:"(.*)"', qs)
        unesc = lambda x: re.sub(r'\\(.)', r'\1', x)
        title, name = unesc(m.group(1)), unesc(m.group(2))
        c = sqlite3.connect(self.base)
        c.execute('ATTACH ? AS f', (self.facts,))
        acts = c.execute('SELECT DISTINCT a.gid FROM f.f_artist_rg ar JOIN f.f_rg g ON g.id = ar.rg '
                         'JOIN artist a ON a.id = ar.artist WHERE a.name = ? AND g.title = ?', (name, title)).fetchall()
        c.close()
        return {'release-groups': [{'title': title, 'artist-credit': [
            {'artist': {'id': str(uuid.UUID(bytes=g)), 'name': name}} for (g,) in acts] + [
            {'artist': {'id': str(uuid.uuid4()), 'name': name + ' Road'}}]}]}


class TestFile(Hosted):
    def test_it_is_todays_pack_plus_the_extras(self):
        base = tables(self.f[1]['base'])
        self.assertEqual(tables(self.f[1]['hosted']) - base,
                         {'rg', 'credit', 'artist_rg', 'barcode', 'svc_album', 'alias_name'})
        self.assertEqual(base - tables(self.f[1]['hosted']), set())
        meta = dict(q(self.f[1]['hosted'], 'SELECT key, value FROM meta'))
        self.assertEqual((meta['hosted_layout'], meta['hosted_format']), ('narrow', 'hosted-1'))

    def test_todays_tables_are_unchanged_but_the_release_map_is_whole(self):
        for t in sorted(tables(self.f[1]['base']) - {'meta', 'sqlite_stat1', 'release'}):
            self.assertEqual(q(self.f[1]['hosted'], f'SELECT * FROM {t} ORDER BY 1, 2'),
                             q(self.f[1]['base'], f'SELECT * FROM {t} ORDER BY 1, 2'), t)
        today = set(q(self.f[1]['base'], 'SELECT * FROM release'))
        whole = set(q(self.f[1]['hosted'], 'SELECT * FROM release'))
        self.assertLess(len(today), 10)
        self.assertEqual(len(whole), 10)       # every release of the made-up dump
        self.assertLessEqual(today, whole)
        # The untyped shared group's release is in today's map, type unknown.
        self.assertEqual(q(self.f[1]['hosted'], 'SELECT type FROM release WHERE gid = ?',
                           uuid.UUID(fixture.g(4008)).bytes), [(None,)])

    def test_alias_keys_leave_out_the_artists_own_name(self):
        keys = set(q(self.f[1]['hosted'], 'SELECT key, artist FROM alias_name'))
        self.assertIn(('sykurmolarnir', 2), keys)
        self.assertIn(('bjork gudmundsdottir', 1), keys)
        # Björk's alias "Bjork" keys as her own name does: the name keys answer it.
        self.assertNotIn(('bjork', 1), keys)
        self.assertIn(('kukl collective', 3), set(q(self.f[2]['hosted'], 'SELECT key, artist FROM alias_name')))
        tsv = {(k, a): n for k, a, n in hosted.read_aliases(self.f[1]['aliases'])}
        self.assertEqual(set(tsv), keys)
        self.assertEqual(tsv['sykurmolarnir', 2], 'Sykurmolarnir')    # the alias's own text, for MusicBrainz

    def test_measure_counts_index_entries(self):
        m = hosted.measure(self.f[1]['hosted'])
        self.assertEqual(m['tables']['artist']['index_rows'], m['tables']['artist']['rows'])
        self.assertEqual(m['tables']['rgx']['index_rows'], m['tables']['rgx']['rows'])
        for t in ('barcode', 'artist_rg', 'alias_name', 'release'):
            self.assertEqual(m['tables'][t]['index_rows'], 0, t)
        self.assertEqual(m['layout'], 'narrow')

    def test_index_entries_are_counted_for_upserts_only_as_d1_counts(self):
        # Real D1, 2026-11-01: an upsert into a table with one index wrote 2 rows, new or
        # existing, changed or not; a delete by key wrote 1.
        src = self.f[1]['hosted']
        c = sqlite3.connect(src)
        (rel_cols, rel_pk), rel = hosted._tables(c)['artist_rel'], c.execute('SELECT * FROM artist_rel').fetchone()
        art = c.execute('SELECT * FROM artist').fetchone()
        c.close()
        d = self.path('one.jsonl.gz')
        with gzip.open(d, 'wt', encoding='utf-8') as f:
            f.write(json.dumps({'t': 'artist_rel', 'd': [hosted._enc(rel[rel_cols.index(x)]) for x in rel_pk]}) + '\n')
            f.write(json.dumps({'t': 'artist', 'p': [hosted._enc(v) for v in art]}) + '\n')
        n = self.quiet(hosted.grow, src, d, self.path('one.db'))
        self.assertEqual((n['deleted'], n['replaced'], n['new'], n['index'], n['rows_written']), (1, 1, 0, 1, 3))

    def test_a_delta_is_proven_and_grows_in_place(self):
        a, b = self.f[1]['hosted'], self.f[2]['hosted']
        d = self.path('d.jsonl.gz')
        res = self.quiet(hosted.delta, a, b, d)
        ap = res['apply']
        self.assertGreater(ap['new'] + ap['replaced'], 0)
        self.assertEqual(ap['rows_written'], ap['new'] + ap['replaced'] + ap['deleted'] + ap['index'])
        self.assertGreater(ap['deleted'], 0)        # the artist url gone in version 2
        self.assertGreater(ap['index'], 0)
        # D1 (measured): an index entry per index for every upsert, none for a delete.
        self.assertEqual(ap['index'], sum(pt * hosted.NARROW_INDEXES.get(t, 0) for t, (pt, dl) in res['tables'].items()))
        for t in ('alias_name', 'artist_rg', 'rg'):  # Kukl's new alias; Björk's "Post"
            self.assertGreater(res['tables'][t][0], 0, t)
        # A damaged delta does not give the new file.
        with gzip.open(d, 'rt', encoding='utf-8') as f:
            lines = f.readlines()
        bad = self.path('bad.jsonl.gz')
        with gzip.open(bad, 'wt', encoding='utf-8') as f:
            f.writelines(line for line in lines if '"t":"meta"' in line)
        g = self.path('g.db')
        self.quiet(hosted.grow, a, bad, g)
        self.assertNotEqual(hosted.same(g, b), '')


class TestKeyed(Hosted):
    """The keyed layout: the same records as the narrow file, bucketed."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ent = {}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for v in (1, 2):
                cls.ent[v] = os.path.join(cls.tmp, f'entries-{v}.db')
                hosted.entries(cls.f[v]['hosted'], cls.ent[v])

    def entry(self, v, kind, k):
        r = q(self.ent[v], 'SELECT v FROM e WHERE kind = ? AND k = ?', kind, k)
        return json.loads(r[0][0]) if r else None

    def test_every_kind_one_entry_per_key(self):
        h = self.f[2]['hosted']
        self.assertEqual({k for (k,) in q(self.ent[2], 'SELECT DISTINCT kind FROM e')}, set(hosted.KINDS))
        n = lambda kind: q(self.ent[2], 'SELECT count(*) FROM e WHERE kind = ?', kind)[0][0]
        self.assertEqual(n('grp'), q(h, 'SELECT count(*) FROM rgx')[0][0])
        self.assertEqual(n('rel'), q(h, 'SELECT count(*) FROM release')[0][0])           # the whole map
        self.assertEqual(n('lst'), q(h, 'SELECT count(DISTINCT artist) FROM artist_rg')[0][0])
        self.assertEqual(n('ali'), q(h, 'SELECT count(DISTINCT key) FROM alias_name')[0][0])
        self.assertEqual(n('nam'), q(h, 'SELECT count(DISTINCT key) FROM artist_name')[0][0])
        self.assertEqual(n('art'), q(h, 'SELECT count(*) FROM artist')[0][0] + q(h, 'SELECT count(*) FROM artist_redirect')[0][0])

    def test_the_artist_and_redirect_documents(self):
        bjork = self.entry(2, 'art', fixture.g(1).replace('-', ''))
        self.assertEqual((len(bjork), bjork[0]), (8, 'Björk'))
        self.assertTrue({'Björk Guðmundsdóttir', 'Kukl'} <= {a[0] for a in bjork[5]})
        # A merged-away gid points at the current artist.
        self.assertEqual(self.entry(2, 'art', fixture.g(99).replace('-', '')), fixture.g(1).replace('-', ''))

    def test_the_list_and_alias_documents_are_the_narrow_answers(self):
        h = self.f[2]['hosted']
        for (a,) in q(h, 'SELECT DISTINCT a.gid FROM artist_rg ar JOIN artist a ON a.id = ar.artist'):
            want = [[g.hex(), t, d, ty, sc, cv, cr] for g, t, d, ty, sc, cv, cr in q(
                h, 'SELECT g.gid, g.title, g.date, g.type, g.sec, g.cover, c.name FROM artist_rg ar JOIN artist x '
                   'ON x.id = ar.artist JOIN rg g ON g.id = ar.rg LEFT JOIN credit c ON c.id = g.credit '
                   'WHERE x.gid = ? ORDER BY g.id', a)]
            self.assertEqual(self.entry(2, 'lst', a.hex()), want)
        self.assertEqual([x[1] for x in self.entry(2, 'lst', fixture.g(1).replace('-', ''))],
                         ['Debut', 'Joint', 'Bootleg Live', 'Post', 'Untyped'])
        # "Kukl Group": the Kukl with a group first.
        self.assertEqual([x[0] for x in self.entry(2, 'ali', 'kukl group')],
                         [fixture.g(9).replace('-', ''), fixture.g(3).replace('-', '')])

    def test_keyed_files_are_deterministic_and_complete(self):
        for bits in (0, 1, 3):
            a, b = self.path(f'k{bits}a.db'), self.path(f'k{bits}b.db')
            self.quiet(hosted.keyed, self.ent[1], self.f[1]['hosted'], bits, a)
            self.quiet(hosted.keyed, self.ent[1], self.f[1]['hosted'], bits, b)
            with open(a, 'rb') as x, open(b, 'rb') as y:
                self.assertEqual(x.read(), y.read(), f'bits {bits}')
            n_entries = q(self.ent[1], 'SELECT count(*) FROM e')[0][0]
            rows = sum(q(a, f'SELECT count(*) FROM {k}')[0][0] for k in hosted.KINDS)
            if bits == 0:
                self.assertEqual(rows, n_entries)
            else:
                self.assertLessEqual(rows, len(hosted.KINDS) * 2 ** bits)
                self.assertLess(rows, n_entries)          # buckets hold several records
            m = hosted.measure(a)
            self.assertEqual((m['layout'], m['bits'], m['index_rows']), ('keyed', str(bits), 0))

    def test_a_keyed_delta_is_proven_and_writes_no_index_entries(self):
        a, b = self.path('dka.db'), self.path('dkb.db')
        self.quiet(hosted.keyed, self.ent[1], self.f[1]['hosted'], 2, a)
        self.quiet(hosted.keyed, self.ent[2], self.f[2]['hosted'], 2, b)
        d = self.path('d-keyed.jsonl.gz')
        res = self.quiet(hosted.delta, a, b, d)
        ap = res['apply']
        self.assertGreater(ap['new'] + ap['replaced'], 0)
        self.assertEqual(ap['index'], 0)
        self.assertEqual(ap['rows_written'], ap['new'] + ap['replaced'] + ap['deleted'])
        # A damaged delta does not give the new file.
        with gzip.open(d, 'rt', encoding='utf-8') as f:
            lines = f.readlines()
        bad = self.path('bad-keyed.jsonl.gz')
        with gzip.open(bad, 'wt', encoding='utf-8') as f:
            f.writelines(line for line in lines if '"t":"meta"' in line)
        g = self.path('g-keyed.db')
        self.quiet(hosted.grow, a, bad, g)
        self.assertNotEqual(hosted.same(g, b), '')


class TestTruth(Hosted):
    def test_the_dumps_own_answers(self):
        f2 = self.f[2]
        qs, tr = self.path('tq.jsonl'), self.path('tt.jsonl')
        self.quiet(hosted.queries, f2['hosted'], qs, 1, ['bjork', 'sugarcubes'])
        hosted.truth(f2['base'], f2['facts'], f2['aliases'], qs, tr)
        with open(qs) as a, open(tr) as b:
            pairs = [(json.loads(x), json.loads(y)) for x, y in zip(a, b)]
        got = {(x['f'], json.dumps(x['a'])): y for x, y in pairs}
        bjork = fixture.g(1)
        self.assertEqual([r[1] for r in got['artistList', json.dumps([bjork])]['r']],
                         ['Debut', 'Joint', 'Bootleg Live', 'Post', 'Untyped'])
        self.assertEqual(got['artistRead', json.dumps([bjork])], {'skip': 1})
        # The second Kukl (1 group) before the first (none).
        self.assertEqual([x['mbid'] for x in got['aliasName', json.dumps(['kukl group'])]['r']],
                         [fixture.g(9), fixture.g(3)])
        self.assertEqual(got['aliasName', json.dumps(['sykurmolarnir'])]['r'],
                         [{'mbid': fixture.g(2), 'name': 'The Sugarcubes', 'score': q(f2['base'],
                           'SELECT ngroups FROM artist WHERE id = 2')[0][0]}])
        self.assertTrue(any(y['r'] for x, y in pairs if x['f'] == 'barcode'))
        self.assertTrue(any(y['r'] for x, y in pairs if x['f'] == 'svcAlbum'))
        self.assertTrue(any(y['r'] for x, y in pairs if x['f'] == 'releases'))


class TestMbcheck(Hosted):
    def test_answers_sorted_right(self):
        f2 = self.f[2]
        mb = StubMusicBrainz(f2['base'], f2['facts'], f2['aliases'], wrong={'Sykurmolarnir'})
        res = self.quiet(hosted.mbcheck, f2['hosted'], f2['aliases'], DSC, 1000, 1000, MIRROR, mb)
        a, t = res['alias'], res['title']
        # Only the alias keys that are no act's own name ("Kukl" is one: the name keys answer it).
        self.assertEqual(a['asked'], q(f2['hosted'], 'SELECT count(DISTINCT key) FROM alias_name '
                                                     'WHERE key NOT IN (SELECT key FROM artist_name)')[0][0])
        self.assertEqual(a['mb_first_missing'], 1)           # the one answered wrongly
        self.assertEqual(a['examples']['the file lacks it'][0]['name'], 'Sykurmolarnir')
        self.assertEqual(a['mb_first_lower'], 1)             # "Kukl Group": MusicBrainz's act is the file's second
        self.assertEqual(a['examples']['the file has it, not first'][0]['name'], 'Kukl Group')
        self.assertEqual(a['same_first'], a['asked'] - 2)
        self.assertEqual(t['asked'], q(f2['hosted'], 'SELECT count(*) FROM artist_rg')[0][0])
        self.assertEqual(t['failed'], 0)
        self.assertEqual(t['same'], t['asked'])              # the group lists name the act MusicBrainz does
        self.assertEqual(t['mb_other_names'], t['asked'])    # "... Road": a name the file cannot match
        self.assertTrue(all(u.startswith(MIRROR) for u in mb.urls))
        self.assertEqual(len([u for u in mb.urls if 'query=' in u]), a['asked'] + t['asked'])
        # The one difference looked up: Someone Else is no act MusicBrainz has.
        self.assertEqual(a['why'], {hosted.WHY['gone']: 1})
        self.assertEqual(a['examples']['the file lacks it'][0]['why'], hosted.WHY['gone'])
        self.assertNotIn('why', t)
        self.assertEqual((res['server'], res['dump']), (MIRROR, q(f2['hosted'], "SELECT value FROM meta WHERE key = 'dump'")[0][0]))
        text = hosted.mb_report(res)
        self.assertIn('asked of ' + MIRROR, text)
        self.assertIn('Sykurmolarnir', text)
        self.assertIn(f"NOT in the file {a['mb_first_missing']}", text)

    def test_the_readable_result_shows_each_count_in_its_place(self):
        names = ['asked', 'same_first', 'mb_first_lower', 'mb_first_missing', 'mb_none', 'failed']
        titles = ['asked', 'same', 'mb_more', 'file_more', 'mb_other_names', 'mb_none_exact', 'failed']
        m = {'server': MIRROR, 'dump': 'd', 'seconds': 1.5,
             'alias': dict(zip(names, range(1001, 1007)), examples={}, why={hosted.WHY['longer']: 3001}),
             'title': dict(zip(titles, range(2001, 2008)), examples={}, why={hosted.WHY['edit']: 3002})}
        text = hosted.mb_report(m)
        self.assertIn(f"alias, why (MusicBrainz's database on each act): 3,001 {hosted.WHY['longer']}", text)
        self.assertIn(f"title, why (MusicBrainz's database on each act): 3,002 {hosted.WHY['edit']}", text)
        for part, keys in (('alias', names), ('title', titles)):
            for k in keys:
                self.assertEqual(text.count(f'{m[part][k]:,}'), 1, (part, k))
        self.assertIn("1,001 asked; MusicBrainz's top artist is the file's first 1,002, in the file's list lower down "
                      "1,003, NOT in the file 1,004; MusicBrainz found none 1,005; failed 1,006", text)
        self.assertIn('2,001 asked; the same acts 2,002, MusicBrainz names an act the file does not 2,003, the file '
                      'names one MusicBrainz does not 2,004; MusicBrainz also names acts whose name only holds the '
                      "name 2,005 (the file cannot); no exact-name act in MusicBrainz's hits 2,006; failed 2,007", text)

    def test_the_mirror_is_asked_at_once_musicbrainz_org_at_its_rate(self):
        f2 = self.f[2]
        for server, gap in ((MIRROR, 0), ('https://musicbrainz.org/ws/2/', hosted.MB_GAP)):
            with mock.patch.object(hosted, '_mb_get', return_value={'artists': []}) as get:
                res = self.quiet(hosted.mbcheck, f2['hosted'], f2['aliases'], DSC, 2, 0, server)
            self.assertEqual(res['alias']['asked'], 2)
            self.assertEqual(res['title']['asked'], 0)               # 0 titles: none asked
            # Two searches found nothing, so each act the file names is looked up: all at the server's pace.
            self.assertEqual([c.args[1] for c in get.call_args_list], [gap] * 4)
            self.assertEqual(sum(c.args[0].startswith(server + 'artist?query=alias%3A') for c in get.call_args_list), 2)
            self.assertEqual(sum(c.args[0].startswith(server + 'artist/') for c in get.call_args_list), 2)
        with mock.patch.object(hosted, '_mb_get') as get:
            res = self.quiet(hosted.mbcheck, f2['hosted'], f2['aliases'], DSC, 0, 1, MIRROR)
        self.assertEqual((res['alias']['asked'], res['title']['asked'], get.call_count), (0, 1, 1))

    def test_name_keys_not_the_files_are_refused_before_asking(self):
        f2 = self.f[2]
        other = self.path('other-keys.db')
        shutil.copy(f2['hosted'], other)
        c = sqlite3.connect(other)
        c.execute("UPDATE namekey_probe SET key = key || 'x' WHERE name = (SELECT min(name) FROM namekey_probe)")
        c.commit()
        c.close()
        asked = []
        with self.assertRaises(SystemExit) as e:
            self.quiet(hosted.mbcheck, other, f2['aliases'], DSC, 1000, 1000, MIRROR, asked.append)
        self.assertIn('1 of', str(e.exception))
        self.assertEqual(asked, [])

    def test_titles_are_escaped_as_api_pm_escapes_them(self):
        # API.pm's _luceneEsc: s/([+\-!(){}\[\]^"~*?:\\\/]|&&|\|\|)/\\$1/g
        self.assertEqual(hosted._lucene_esc('AC/DC: "Live" (1992) [Deluxe] +1 -2 !? ~*^ {x} a&&b c||d e&f \\'),
                         'AC\\/DC\\: \\"Live\\" \\(1992\\) \\[Deluxe\\] \\+1 \\-2 \\!\\? \\~\\*\\^ '
                         '\\{x\\} a\\&&b c\\||d e&f \\\\')

    def test_an_act_the_file_lacks_is_caught(self):
        f2 = self.f[2]

        kukl = fixture.g(3)          # an act the file holds, with no group
        for how in ('gone', 'not held', 'edit'):
            def mb(url):
                m = re.search(r'/artist/([^?/]+)\?', url)
                if m:                                        # the act exists now, or not
                    return None if how == 'gone' else {'id': m.group(1), 'name': 'X', 'aliases': []}
                qs = urllib.parse.unquote(url.split('query=', 1)[1].split('&', 1)[0])
                if qs.startswith('alias:'):
                    return None                              # a failed request
                m = re.match(r'releasegroup:"(.*)" AND artist:"(.*)"', qs)
                unesc = lambda x: re.sub(r'\\(.)', r'\1', x)
                return {'release-groups': [{'title': unesc(m.group(1)), 'artist-credit': [
                    {'artist': {'id': kukl if how == 'edit' else str(uuid.uuid4()), 'name': unesc(m.group(2))}}]}]}
            looked = []
            res = self.quiet(hosted.mbcheck, f2['hosted'], f2['aliases'], DSC, 1000, 1000, MIRROR,
                             lambda u: (looked.append(u) if 'query=' not in u else None) or mb(u))
            self.assertEqual(res['alias']['failed'], res['alias']['asked'])
            self.assertNotIn('why', res['alias'])            # a failed request has no reason to look up
            self.assertEqual(res['title']['mb_more'], res['title']['asked'])
            self.assertEqual(res['title']['same'], 0)
            # An act MusicBrainz names that the file does not hold, one gone since, or one the file
            # holds without that name or group (an edit since the dump), each looked up once.
            self.assertEqual(res['title']['why'], {hosted.WHY[how]: res['title']['asked']})
            self.assertEqual(len(looked), 1 if how == 'edit' else res['title']['asked'])

    def test_each_difference_gets_its_reason_from_musicbrainzs_database(self):
        f2 = self.f[2]
        asked = {n for k, a, n in hosted.read_aliases(f2['aliases'])
                 if not q(f2['hosted'], 'SELECT 1 FROM artist_name WHERE key = ?', k)}
        self.assertTrue({'Sykurmolarnir', 'Björk Guðmundsdóttir', 'Kukl Group', 'Kukl Collective'} <= asked, asked)
        sugar, bjork, kukl2, new = fixture.g(2), fixture.g(1), fixture.g(9), str(uuid.uuid4())
        no_name = StubMusicBrainz(f2['base'], f2['facts'], f2['aliases']).lookup(sugar)
        no_name['aliases'] = [x for x in no_name['aliases'] if x['name'] != 'Sykurmolarnir']
        runs = [
            # MusicBrainz's top act: one the file holds whose names do not give the key (a longer
            # name matched); one the file does not hold; nothing found while the act has the name.
            ({'Björk Guðmundsdóttir': sugar, 'Kukl Collective': new, 'Kukl Group': None}, {new: {'id': new, 'name': 'Kukl Collective'}},
             {'Björk Guðmundsdóttir': 'longer', 'Kukl Collective': 'not held', 'Kukl Group': 'unsearched'}),
            # The file's act lost the name since; the file's act is gone; MusicBrainz's act merged into another id.
            ({'Sykurmolarnir': None, 'Kukl Group': None, 'Björk Guðmundsdóttir': new}, {sugar: no_name, kukl2: None, new: {'id': str(uuid.uuid4())}},
             {'Sykurmolarnir': 'removed', 'Kukl Group': 'file gone', 'Björk Guðmundsdóttir': 'gone'}),
            # An act the file holds that has the name now: an edit since the dump.
            ({'Sykurmolarnir': bjork}, {bjork: {'id': bjork, 'name': 'Björk', 'aliases': [{'name': 'Sykurmolarnir'}]}},
             {'Sykurmolarnir': 'edit'}),
        ]
        for top, lookups, want in runs:
            mb = StubMusicBrainz(f2['base'], f2['facts'], f2['aliases'], top=top, lookups=lookups)
            res = self.quiet(hosted.mbcheck, f2['hosted'], f2['aliases'], DSC, 1000, 0, MIRROR, mb)
            got = {x['name']: x['why'] for xs in res['alias']['examples'].values() for x in xs if 'why' in x}
            self.assertEqual(got, {n: hosted.WHY[w] for n, w in want.items()})
            self.assertEqual(sum(res['alias']['why'].values()), len(want))
            # One lookup per act, the searches as before.
            looked = [u for u in mb.urls if 'query=' not in u]
            self.assertEqual(len(looked), len(set(looked)))
            self.assertEqual(len(mb.urls) - len(looked), res['alias']['asked'])


@unittest.skipUnless(NODE and DBD, 'Node 22.5+ (node:sqlite) or perl DBD::SQLite not found')
class TestReader(Hosted):
    def test_the_whole_run_answers_as_pack_pm_and_the_dump(self):
        os.environ['NODE'] = NODE
        work = self.path('run')
        f1, f2 = self.f[1], self.f[2]
        named = sorted({k for (k,) in q(f2['base'], 'SELECT key FROM artist_name WHERE artist IN (1, 2, 6)')})
        res = self.quiet(hosted.run, f1['base'], f1['facts'], f1['aliases'], f2['base'], f2['facts'], f2['aliases'],
                         plugin_dir(self.tmp), work, every=1, named=named, bits_list=[0, 1, 3], keep_bits=1, jobs=2)
        self.assertEqual(res['pages'], 3)
        # Every file of the same content, held to the same answers.
        self.assertEqual(res['labels'], ['narrow', 'keyed 0', 'keyed 1', 'keyed 3'])
        cmp = res['compare']
        self.assertEqual(set(cmp), {'artistRead', 'collaboration', 'groupCount', 'artistList', 'sameName',
                                    'aliasName', 'groups', 'releases', 'barcode', 'svcAlbum'})
        for fn, st in cmp.items():
            self.assertEqual(st['differ'], 0, f"{fn}: {st['examples']}")
            self.assertEqual(st['unchecked'], 0, fn)
            # Every lookup was really answered somewhere, not only agreed on as empty.
            self.assertGreater(st['answered'], 0, fn)
        self.assertGreater(cmp['releases']['extra_releases'], 0)
        self.assertNotIn('mbcheck', res)                 # the runner cannot reach the mirror
        text = hosted.report(res)
        self.assertIn('against MusicBrainz itself: not on the runner', text)
        self.assertIn('SAME: every file answers as Pack.pm does', text)
        self.assertIn('whole release map', text)
        self.assertIn('THE KEYED LAYOUT', text)
        self.assertIn('one a record', text)
        self.assertTrue(os.path.exists(os.path.join(work, 'hosted-B.db')))
        self.assertTrue(os.path.exists(os.path.join(work, 'hosted-B-keyed1.db')))     # the kept bucket size
        for gone in ('hosted-A.db', 'hosted-B-keyed3.db', 'hosted-A-keyed1.db', 'entries-A.db', 'entries-B.db'):
            self.assertFalse(os.path.exists(os.path.join(work, gone)), gone)
        for k in res['keyed']:
            self.assertEqual(k['delta']['apply']['index'], 0)
            self.assertTrue(all(pg['rows_read'] > 0 for pg in k['pages']['pages']), k['bits'])
            self.assertEqual(k['over_cap'], [])
        for kept in ('pages.json', 'queries.jsonl', 'truth.jsonl', 'results.json'):
            self.assertTrue(os.path.exists(os.path.join(work, kept)), kept)
        # The page sample: the groups from the dump's facts, every one asked.
        got = {pg['label'].split(' (')[0]: pg for pg in res['hosted']['pages']['pages']}
        # Dump B (version 2): Debut, Joint, Bootleg Live, Untyped and Post.
        self.assertEqual(got['Björk']['groups'], 5)
        for pg in got.values():
            self.assertGreater(pg['rows_read'], 0)

    def test_a_wrong_answer_is_caught(self):
        # The comparison itself: one hosted answer changed is a difference.
        work = self.path('cmp')
        os.makedirs(work, exist_ok=True)
        lines = {
            'q': [{'f': 'groupCount', 'a': [fixture.g(1)]}, {'f': 'barcode', 'a': [5016958997028]},
                  {'f': 'releases', 'a': [['r1', 'r2']]}, {'f': 'aliasName', 'a': ['x']}],
            'p': [{'r': 4}, {'skip': 1}, {'r': {'r1': {'rg': 'a', 'type': ''}}}, {'skip': 1}],
            't': [{'skip': 1}, {'r': ['x']}, {'r': {'r1': {'rg': 'a', 'type': ''}, 'r2': {'rg': 'b', 'type': ''}}},
                  {'r': [{'mbid': 'm', 'name': 'X', 'score': 1}]}],
            'good': [{'r': '4'}, {'r': ['x']}, {'r': {'r1': {'rg': 'a', 'type': ''}, 'r2': {'rg': 'b', 'type': ''}}},
                     {'r': [{'mbid': 'm', 'name': 'X', 'score': '1'}]}],
            'bad': [{'r': 5}, {'r': ['y']}, {'r': {'r1': {'rg': 'a', 'type': ''}}}, {'r': []}],
        }
        path = {}
        for k, rows in lines.items():
            path[k] = os.path.join(work, f'{k}.jsonl')
            with open(path[k], 'w') as f:
                f.writelines(json.dumps(r) + '\n' for r in rows)
        good = hosted.compare(path['q'], path['p'], path['t'], [('hosted', path['good'])])
        self.assertEqual(sum(st['differ'] for st in good.values()), 0)
        self.assertEqual(good['releases']['extra_releases'], 1)
        bad = hosted.compare(path['q'], path['p'], path['t'], [('hosted', path['bad'])])
        for fn in ('groupCount', 'barcode', 'releases', 'aliasName'):
            self.assertEqual(bad[fn]['differ'], 1, fn)
        # With no truth (the local test), the new lookups are counted unchecked, never passed.
        local = hosted.compare(path['q'], path['p'], None, [('hosted', path['bad'])])
        self.assertEqual((local['barcode']['unchecked'], local['barcode']['differ']), (1, 0))
        self.assertEqual(local['releases']['differ'], 0)     # today's entry is there; the whole map unchecked
        # ... but a release today's map places, placed differently, is caught there too.
        moved = os.path.join(work, 'moved.jsonl')
        with open(moved, 'w') as f:
            f.writelines(json.dumps(r) + '\n' for r in lines['good'][:2] + [{'r': {'r1': {'rg': 'z', 'type': ''}}}]
                         + lines['good'][3:])
        self.assertEqual(hosted.compare(path['q'], path['p'], None, [('hosted', moved)])['releases']['differ'], 1)
        # Two files with no truth: an unchecked lookup is still held file against file.
        two = hosted.compare(path['q'], path['p'], None, [('narrow', path['good']), ('keyed', path['bad'])])
        self.assertEqual((two['barcode']['unchecked'], two['barcode']['differ']), (1, 1))
        self.assertEqual(two['barcode']['examples'][0]['differ'], ['keyed'])
        same = hosted.compare(path['q'], path['p'], None, [('narrow', path['good']), ('keyed', path['good'])])
        self.assertEqual(sum(st['differ'] for st in same.values()), 0)


if __name__ == '__main__':
    unittest.main()
