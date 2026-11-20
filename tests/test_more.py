"""builder/more.py and the more file (keyed.py with more.db) on the made-up dump: what each record holds, record by
record; that the build's one pass of the dump takes every table more.py reads; that the more file is today's file with
the new records appended to its artist and release records and nothing else of today's changed; that a sized file
puts each kind at its own count and says so in its meta; and that the parts carry every kind. Sizes of a few rows
prove nothing; the runner's measurement on the real dump is the measurement.
Needs perl and an LMS-Discography checkout ($DSC_DIR, else ../LMS-Discography) for the name keys.
Run: python3 -m unittest discover -s tests
"""
import contextlib, io, json, os, shutil, sqlite3, sys, tempfile, unittest, uuid, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'builder'))
sys.path.insert(0, HERE)
import fixture, keyed, load_parts, measure_extra, more  # noqa: E402
from test_keyed import add_extras, records as part_records  # noqa: E402
from test_measure_extra import add_candidates  # noqa: E402
from test_pack import DSC, build  # noqa: E402

N, TS, g = fixture.N, fixture.TS, fixture.g
BITS = 3
hx = lambda n: uuid.UUID(g(n)).hex


def add_more(root):
    """The tables more.py reads that the made-up dump lacks, and the rows it reads that the others lack, written into
    the same folder (the build's one pass)."""
    d = os.path.join(root, 'mbdump')
    w = lambda t, rows: fixture.write(d, t, rows)

    def add(table, rows):
        with open(os.path.join(d, table), 'a', encoding='utf-8', newline='\n') as f:
            for r in rows:
                f.write('\t'.join(N if v is None else str(v) for v in r) + '\n')

    lt = lambda i, e0, e1, name: [i, N, 0, g(900 + i), e0, e1, name, '', '', '', '', TS, 'f', 't', 0, 0]
    add('link_type', [lt(20, 'artist', 'recording', 'instrument'), lt(21, 'artist', 'recording', 'vocal'),
                      lt(22, 'artist', 'recording', 'producer'), lt(23, 'artist', 'release', 'design/illustration'),
                      lt(24, 'artist', 'release_group', 'tribute'), lt(25, 'artist', 'url', 'wikidata'),
                      lt(26, 'artist', 'url', 'official homepage'), lt(27, 'artist', 'url', 'bandcamp'),
                      lt(28, 'artist', 'artist', 'is person'), lt(29, 'artist', 'url', 'allmusic'),
                      lt(41, 'artist', 'release', 'producer')])
    add('link_attribute_type', [[3, N, 3, 0, g(952), 'lead vocals', 'x', TS], [4, N, 2, 0, g(953), 'piano', 'x', TS]])
    ln = lambda i, t: [i, t, N, N, N, N, N, N, 0, TS, 'f']
    add('link', [ln(30, 20), ln(31, 21), ln(32, 22), ln(33, 23), ln(34, 24), ln(35, 25), ln(36, 26), ln(37, 27),
                 [38, 28, 1965, 11, 21, 1990, N, N, 0, TS, 't'], ln(39, 29), ln(41, 41)])
    add('link_attribute', [[30, 4, TS], [30, 2, TS], [31, 3, TS]])          # out of order: read back sorted
    l = lambda i, link, a, b, order=0: [i, link, a, b, 0, TS, order, '', '']
    add('l_artist_artist', [l(106, 38, 8, 1)])
    # Björk's homepages: link order against id order (203 first by its order). Some Duo: a homepage and nothing else.
    # Wikidata, Bandcamp and AllMusic links: not taken (the community API has the first two).
    add('l_artist_url', [l(201, 35, 1, 320), l(202, 36, 1, 321, 1), l(203, 36, 1, 322, 0), l(204, 37, 6, 323),
                         l(205, 39, 6, 324), l(206, 36, 4, 325)])
    add('url', [[320, g(320), 'https://www.wikidata.org/wiki/Q42', 0, TS], [321, g(321), 'https://bjork.com/', 0, TS],
                [322, g(322), 'https://www.bjork.com/', 0, TS], [323, g(323), 'https://sigurros.bandcamp.com/', 0, TS],
                [324, g(324), 'https://www.allmusic.com/artist/mn0000', 0, TS],
                [325, g(325), 'https://someduo.example/', 0, TS]])
    # A newer trailing column (as a schema change would add) must not stop the run.
    rc = lambda i, name, more=(): [i, g(i), name, 500, 252000, '', 0, TS, 'f', *more]
    w('recording', [rc(6000, 'Human Behaviour', ['newer column']), rc(6001, 'Venus as a Boy'), rc(6002, 'Unsung')])
    # Link order against id order: Björk's link (order 0) comes first though Sigur Rós's has the lower id.
    w('l_artist_recording', [l(301, 30, 1, 6000, 0), l(302, 32, 8, 6001), l(300, 31, 6, 6000, 1)])
    # Kukl (credited on no recording): three links to three releases of ONE group (Debut), one role twice. The
    # Sugarcubes: one link, to the one release with several dates (2000), so its page's date is that release's first.
    w('l_artist_release', [l(400, 33, 3, 2000), l(401, 33, 3, 2004), l(402, 41, 3, 2005), l(403, 33, 2, 2000)])
    w('l_artist_release_group', [l(500, 34, 6, 1000)])
    # A catalogue number with an escaped backslash (COPY text writes one as two).
    w('release_label', [[1, 2000, 700, 'TPLP 31', TS], [2, 2000, 701, N, TS], [3, 2004, N, 'CAT\\\\1', TS]])
    lb = lambda i, name: [i, g(i), name, N, N, N, N, N, N, N, N, N, '', 0, TS, 'f']
    w('label', [lb(700, 'One Little Indian'), lb(701, 'Elektra')])
    ar = lambda i, name, typ: [i, g(i), name, typ, 0, TS, N, N, N, N, N, N, 'f', '']
    w('area', [ar(50, 'Iceland', 1), ar(51, 'Reykjavík', 3)])
    # Release 2000's first date stays 1993-07-05: a later event, and one of 1993 with no month (which sorts after it).
    add('release_unknown_country', [[2000, 1999, 1, 1], [2000, 1993, N, N]])
    # Björk born 1965-11-21 in Reykjavík, from Iceland; The Sugarcubes 1986-1992, ended.
    with open(os.path.join(d, 'artist'), encoding='utf-8') as f:
        art = [line.rstrip('\n').split('\t') for line in f]
    for r in art:
        if r[0] == '1':
            r[4:7], r[11], r[17] = ['1965', '11', '21'], '50', '51'
        elif r[0] == '2':
            r[4], r[7], r[16] = '1986', '1992', 't'
    w('artist', art)
    return root


def only(src, tables, root):
    """A copy of the dump folder holding ONLY these tables: so a table more.py reads but the build does not fetch
    fails."""
    d = os.path.join(root, 'mbdump')
    os.makedirs(d)
    for t in tables:
        if os.path.exists(os.path.join(src, 'mbdump', t)):
            shutil.copyfile(os.path.join(src, 'mbdump', t), os.path.join(d, t))
    return root


def q(db, sql, *args):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def bucketed(db, table):
    """{bucket: {key: record}} of a bucketed table."""
    return {int.from_bytes(b, 'big'): json.loads(zlib.decompress(v, -15)) for b, v in q(db, f'SELECT k, v FROM {table}')}


def flat(db, table):
    out = {}
    for recs in bucketed(db, table).values():
        out.update(recs)
    return out


def fetched_tables():
    """The tables the build's second download takes: more.fetch with the download itself left out."""
    got, old = [], (more.fetch_dump.fetch, more.fetch_dump.TABLES)
    more.fetch_dump.fetch = lambda dump, out: got.append((dump, out, list(more.fetch_dump.TABLES)))
    try:
        more.fetch('20260101-000000', '/x/work')
    finally:
        more.fetch_dump.fetch, more.fetch_dump.TABLES = old
    assert len(got) == 1 and got[0][:2] == ('20260101-000000', '/x/work'), got
    return got[0][2]


class More(unittest.TestCase):
    """One dump built all the way, today's way (tables -> entries -> keyed) and the more way (the same plus more.db)."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        t = lambda n: os.path.join(cls.tmp, n)
        cls.t = staticmethod(t)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            src = add_more(add_extras(add_candidates(fixture.make_dump(t('d'), 1)), 1))
            base = build(src, t('base.db'), '20260101-000000')
            measure_extra.facts(src, t('facts.db'))
            cls.src2 = only(src, fetched_tables(), t('fetched'))
            cls.made = more.entries(cls.src2, t('more.db'))
            keyed.aliases(base, DSC, t('aliases.tsv'))
            keyed.narrow(base, t('facts.db'), t('aliases.tsv'), t('tables.db'))
            keyed.entries(t('tables.db'), t('entries.db'))
            keyed.entries(t('tables.db'), t('entries-more.db'), t('more.db'))
            keyed.keyed(t('entries.db'), t('tables.db'), BITS, t('keyed.db'))
            keyed.keyed(t('entries-more.db'), t('tables.db'), BITS, t('keyed-more.db'))
            keyed.keyed(t('entries-more.db'), t('tables.db'), 'sized', t('keyed-sized.db'))
        cls.err = err.getvalue()
        cls.more, cls.ent, cls.ent_more = t('more.db'), t('entries.db'), t('entries-more.db')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def quiet(self, fn, *a, **k):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fn(*a, **k)

    def entry(self, kind, k, db=None):
        r = q(db or self.more, 'SELECT v FROM e WHERE kind = ? AND k = ?', kind, k)
        return json.loads(r[0][0]) if r else None

    def keys(self, kind, db=None):
        return sorted(k for (k,) in q(db or self.more, 'SELECT k FROM e WHERE kind = ?', kind))


class TestHelpers(unittest.TestCase):
    def test_trim_drops_trailing_empties_only(self):
        for t in (more.trim, keyed.trim):
            self.assertEqual(t([1, None, 0, [], '']), [1])
            self.assertEqual(t([0, 1, None, 0]), [0, 1])
            self.assertEqual(t([None, [], 2]), [None, [], 2])
            self.assertEqual(t([None, 0]), [])
            self.assertEqual(t(['x', [0]]), ['x', [0]])

    def test_heads_reads_the_first_fields_as_written(self):
        d = tempfile.mkdtemp()
        try:
            fixture.write(d, 't', [[1, 'a\\tb', 3], [4, 5, 6]])
            self.assertEqual(list(more.heads(d, 't', 3)), [['1', 'a\\tb', '3'], ['4', '5', '6']])
            self.assertEqual(list(more.heads(d, 't', 2)), [['1', 'a\\tb'], ['4', '5']])
            with self.assertRaises(SystemExit):
                list(more.heads(d, 't', 4))                           # a row with fewer fields stops the run
        finally:
            shutil.rmtree(d)

    def test_date_keeps_the_parts_known(self):
        self.assertEqual(more.date('1965', '11', '21'), 19651121)
        self.assertEqual(more.date('1965', None, None), 19650000)
        self.assertEqual(more.date('1965', None, '3'), 19650003)
        self.assertIsNone(more.date(None, '11', '21'))

    def test_the_second_download_takes_mores_tables_only(self):
        before = list(more.fetch_dump.TABLES)
        got = fetched_tables()
        self.assertEqual(more.fetch_dump.TABLES, before)               # put back
        self.assertEqual(got, more.TABLES)                             # its own, none of the first download's others
        self.assertNotIn('track', got)                                 # the first download's largest
        self.assertNotIn('genre', got)                                 # genres: the community API's

    def test_kinds_and_counts_by_format(self):
        self.assertEqual(keyed.kinds_of('data-1'), keyed.KINDS)
        self.assertEqual(keyed.kinds_of('data-2'), keyed.KINDS + keyed.MORE_KINDS)
        with self.assertRaises(SystemExit):
            keyed.kinds_of('data-3')
        self.assertEqual(set(keyed.SIZED), set(keyed.KINDS + keyed.MORE_KINDS))
        self.assertEqual(keyed.bits_of('data-2', 'sized'), keyed.SIZED)
        self.assertEqual(keyed.bits_of('data-2', 5), {k: 5 for k in keyed.KINDS + keyed.MORE_KINDS})
        self.assertEqual(keyed.bits_of('data-1', 14), {k: 14 for k in keyed.KINDS})
        for bad in (('data-1', 'sized'), ('data-2', '14'), ('data-2', -1), ('data-2', 25), ('data-2', True)):
            with self.assertRaises(SystemExit, msg=bad):
                keyed.bits_of(*bad)
        # Every key of a new kind is a gid but who's (an artist id as text).
        self.assertEqual(keyed.GID_KINDS & set(keyed.MORE_KINDS), {'prf', 'crd', 'pgr'})


class TestRecords(More):
    def test_it_reads_only_what_the_second_download_fetches(self):
        self.assertEqual(set(os.listdir(os.path.join(self.src2, 'mbdump'))), set(more.TABLES))

    def test_abo_where_from_years_and_homepages(self):
        self.assertEqual(self.entry('abo', hx(1)), [19651121, None, 0, 'Iceland', 'Reykjavík',
                                                    ['https://www.bjork.com/', 'https://bjork.com/']])
        self.assertEqual(self.entry('abo', hx(2)), [19860000, 19920000, 1])
        self.assertEqual(self.entry('abo', hx(4)), [None, None, 0, None, None, ['https://someduo.example/']])
        self.assertIsNone(self.entry('abo', hx(3)))                      # nothing known: no record
        self.assertIsNone(self.entry('abo', hx(6)))                      # Bandcamp and AllMusic only: none
        self.assertEqual(self.keys('abo'), sorted([hx(1), hx(2), hx(4)]))

    def test_lnk_every_type_both_ways_with_years_and_attributes(self):
        self.assertEqual(self.entry('lnk', hx(1)), [
            [0, 1, hx(3), 'Kukl', 19850000, 19880000, 1],
            [0, 1, hx(2), 'The Sugarcubes', 19900000, None, 0, [1]],
            [0, 1, hx(7), 'Björk & The Elgar Sisters'],
            [0, 2, hx(4), 'Some Duo'],
            [1, 28, hx(8), 'Unlinked Nobody', 19651121, 19900000, 1]])
        self.assertEqual(self.entry('lnk', hx(2)), [[1, 1, hx(1), 'Björk', 19900000, None, 0, [1]],
                                                    [1, 2, hx(6), 'Sigur Rós']])

    def test_crd_a_recordings_credits_by_artist_id(self):
        self.assertEqual(self.entry('crd', hx(6000)), [[1, 20, [2, 4]], [6, 21, [3]]])
        self.assertEqual(self.entry('crd', hx(6001)), [[8, 22]])
        self.assertEqual(self.keys('crd'), sorted([hx(6000), hx(6001)]))   # no credits: no record

    def test_who_names_every_artist_crd_and_rcr_name_and_no_other(self):
        self.assertEqual(self.keys('who'), ['1', '2', '3', '6', '8'])        # 2, 3: on a release only
        self.assertEqual(self.entry('who', '8'), [hx(8), 'Unlinked Nobody'])   # in no list, still named
        ids = {r[0] for k in self.keys('crd') for r in self.entry('crd', k)} | \
              {r[0] for k in self.keys('rcr') for r in self.entry('rcr', k)}
        self.assertEqual({str(i) for i in ids}, set(self.keys('who')))

    def test_rcr_a_releases_own_credits(self):
        self.assertEqual(self.entry('rcr', hx(4000)), [[3, 23], [2, 23]])
        self.assertEqual(self.entry('rcr', hx(4005)), [[3, 41]])
        self.assertEqual(self.keys('rcr'), sorted([hx(4000), hx(4004), hx(4005)]))

    def test_rlb_labels_and_catalogue_numbers(self):
        self.assertEqual(self.entry('rlb', hx(4000)), [['One Little Indian', 'TPLP 31'], ['Elektra']])
        self.assertEqual(self.entry('rlb', hx(4004)), [[None, 'CAT\\1']])

    def test_pgr_one_record_a_group_each_role_once(self):
        # Three links to three of Debut's releases: one record, the group's title and credit, the first date by
        # MusicBrainz's rule (1993-06 before 1993-07-05 before 1993 with no month), role 23 once.
        self.assertEqual(self.entry('pgr', hx(3)), [[hx(2000), 'Debut', 19930600, 'Björk', [[23], [41]]]])
        self.assertEqual(self.entry('pgr', hx(6)), [[hx(2000), 'Debut', None, 'Björk', [[24]]]])
        # One release with three dates (1999-01-01, 1993-07-05, 1993 with no month): its first, 1993-07-05.
        self.assertEqual(self.entry('pgr', hx(2)), [[hx(2000), 'Debut', 19930705, 'Björk', [[23]]]])
        self.assertEqual(self.keys('pgr'), sorted([hx(2), hx(3), hx(6)]))

    def test_the_names_ride_in_meta(self):
        names = json.loads(q(self.more, "SELECT value FROM meta WHERE key = 'more_names'")[0][0])
        self.assertEqual(names['link_type']['20'], 'instrument')
        self.assertEqual(names['attribute']['4'], 'piano')
        self.assertEqual(set(names), {'link_type', 'attribute'})

    def test_the_counts_are_kept(self):
        stat = dict(q(self.more, 'SELECT key, value FROM stat'))
        self.assertEqual(stat['l_artist_recording rows'], 3)
        self.assertEqual(stat['recordings'], 3)
        self.assertEqual(stat['  with credits'], 2)
        self.assertEqual(stat['  artist links to official homepage'], 3)
        for k in more.KINDS:
            self.assertEqual(stat[f'records {k}'], len(self.keys(k)), k)
            self.assertEqual(self.made[k], len(self.keys(k)), k)
        self.assertEqual({k for (k,) in q(self.more, 'SELECT DISTINCT kind FROM e')}, set(more.KINDS))

    def test_a_gid_kind_hashes_the_gid_and_who_its_text(self):
        self.assertEqual(q(self.more, "SELECT h FROM e WHERE kind = 'crd' AND k = ?", hx(6000))[0][0],
                         keyed._h_gid(uuid.UUID(g(6000)).bytes))
        self.assertEqual(q(self.more, "SELECT h FROM e WHERE kind = 'who' AND k = '8'")[0][0], keyed._h_text('8'))

    def test_two_records_for_one_key_stop_the_run(self):
        d = os.path.join(self.tmp, 'dup')
        shutil.copytree(self.src2, d)
        with open(os.path.join(d, 'mbdump', 'artist'), 'a', encoding='utf-8') as f:     # a second row for Björk's gid
            f.write('\t'.join(['99', g(1), 'Bjork', 'Bjork', '1965', N, N, N, N, N, '1', N, N, '', '0', TS, 'f', N, N]) + '\n')
        with self.assertRaises(SystemExit):
            self.quiet(more.entries, d, os.path.join(self.tmp, 'dup.db'))


class TestFolded(More):
    """keyed.entries with more.db: today's records, the new ones appended to art and rel, the rest kinds of their own."""
    def test_todays_other_kinds_are_as_they_were(self):
        for kind in set(keyed.KINDS) - set(keyed.FOLD):
            self.assertEqual(q(self.ent_more, 'SELECT h, k, v FROM e WHERE kind = ? ORDER BY k', kind),
                             q(self.ent, 'SELECT h, k, v FROM e WHERE kind = ? ORDER BY k', kind), kind)

    def test_art_and_rel_take_their_keys_records_as_trailing_fields(self):
        adds = {a: {k: json.loads(v) for k, v in q(self.more, 'SELECT k, v FROM e WHERE kind = ?', a)}
                for a in ('abo', 'lnk', 'rlb', 'rcr')}
        for kind, fields in keyed.FOLD.items():
            today = {k: (h, json.loads(v)) for h, k, v in q(self.ent, 'SELECT h, k, v FROM e WHERE kind = ?', kind)}
            folded = {k: (h, json.loads(v)) for h, k, v in q(self.ent_more, 'SELECT h, k, v FROM e WHERE kind = ?',
                                                             kind)}
            self.assertEqual(set(folded), set(today), kind)
            took = 0
            for k, (h, rec) in today.items():
                xs = keyed.trim([adds[a].get(k, []) for a in fields])
                want = rec + xs if isinstance(rec, list) else rec           # a redirect is never appended to
                self.assertEqual(folded[k], (h, want), (kind, k))
                took += bool(xs and isinstance(rec, list))
            self.assertGreater(took, 1, kind)

    def test_the_fields_land_where_a_reader_finds_them(self):
        bj = self.entry('art', hx(1), self.ent_more)
        self.assertEqual(len(bj), 10)
        self.assertEqual(bj[:8], self.entry('art', hx(1), self.ent))
        self.assertEqual((bj[8], bj[9]), (self.entry('abo', hx(1)), self.entry('lnk', hx(1))))
        # Kukl: links but nothing known of its dates: abo's field is there, empty, so lnk stays the 10th.
        kukl = self.entry('art', hx(3), self.ent_more)
        self.assertEqual((len(kukl), kukl[8], kukl[9]), (10, [], self.entry('lnk', hx(3))))
        # Debut (Deluxe): its labels, then its own credits; Debut (2005): no labels, so rcr stays the 5th field.
        self.assertEqual(self.entry('rel', hx(4000), self.ent_more)[3:],
                         [[['One Little Indian', 'TPLP 31'], ['Elektra']], [[3, 23], [2, 23]]])
        self.assertEqual(self.entry('rel', hx(4005), self.ent_more)[3:], [[], [[3, 41]]])
        self.assertEqual(self.entry('rel', hx(4004), self.ent_more)[3:], [[[None, 'CAT\\1']], [[3, 23]]])
        # A release with none of them: today's record exactly.
        self.assertEqual(self.entry('rel', hx(4001), self.ent_more), self.entry('rel', hx(4001), self.ent))

    def test_prf_holds_the_artists_art_lacks_and_no_other(self):
        art = {k for k, v in q(self.ent, "SELECT k, v FROM e WHERE kind = 'art'") if v.startswith('[')}
        want = {}
        for k in set(self.keys('abo')) | set(self.keys('lnk')):
            if k not in art:
                want[k] = keyed.trim([self.entry('abo', k) or [], self.entry('lnk', k) or []])
        got = {k: json.loads(v) for k, v in q(self.ent_more, "SELECT k, v FROM e WHERE kind = 'prf'")}
        self.assertEqual(got, want)
        self.assertGreater(len(got), 0)                                  # the fixture has such an artist
        self.assertEqual(got.get(hx(8)), [[], self.entry('lnk', hx(8))])  # Unlinked Nobody: a link only
        for k in got:
            self.assertEqual(q(self.ent_more, "SELECT h FROM e WHERE kind = 'prf' AND k = ?", k)[0][0],
                             keyed._h_gid(bytes.fromhex(k)))

    def test_crd_who_pgr_are_mores_records(self):
        for kind in ('crd', 'who', 'pgr'):
            self.assertEqual(q(self.ent_more, 'SELECT h, k, v FROM e WHERE kind = ? ORDER BY k', kind),
                             q(self.more, 'SELECT h, k, v FROM e WHERE kind = ? ORDER BY k', kind), kind)
        self.assertEqual({k for (k,) in q(self.ent_more, 'SELECT DISTINCT kind FROM e')},
                         set(keyed.KINDS + keyed.MORE_KINDS))

    def test_the_format_rides_in_em_and_the_names_are_kind_rol(self):
        self.assertEqual(dict(q(self.ent_more, 'SELECT key, value FROM em')), {'file_format': 'data-2'})
        self.assertEqual(dict(q(self.ent, 'SELECT key, value FROM em')), {'file_format': 'data-1'})
        names = json.loads(q(self.more, "SELECT value FROM meta WHERE key = 'more_names'")[0][0])
        rol = {k: (h, json.loads(v)) for h, k, v in q(self.ent_more, "SELECT h, k, v FROM e WHERE kind = 'rol'")}
        self.assertEqual(rol, {k: (keyed._h_text(k), v) for k, v in names.items()})
        self.assertEqual(rol['link_type'][1]['20'], 'instrument')
        self.assertEqual(rol['attribute'][1]['4'], 'piano')

    def test_labels_or_credits_of_a_release_the_file_lacks_are_counted_and_left_out(self):
        stray = os.path.join(self.tmp, 'more-stray.db')
        shutil.copyfile(self.more, stray)
        k = uuid.UUID(g(77777)).hex
        c = sqlite3.connect(stray)
        c.execute("INSERT INTO e VALUES ('rlb', ?, ?, '[[\"Stray\"]]')", (keyed._h_gid(bytes.fromhex(k)), k))
        c.commit()
        c.close()
        out = os.path.join(self.tmp, 'entries-stray.db')
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            keyed.entries(self.t('tables.db'), out, stray)
        self.assertIn('::warning::1 rlb record(s) name a release the file lacks', err.getvalue())
        self.assertEqual(q(out, 'SELECT kind FROM e WHERE k = ?', k), [])
        self.assertNotIn('::warning::', self.err)                         # the real fixture has none


class TestFile(More):
    def tables(self, db):
        return {t for (t,) in q(db, "SELECT name FROM sqlite_master WHERE type = 'table'")}

    def test_todays_file_is_unchanged_by_the_new_code(self):
        meta = dict(q(self.t('keyed.db'), 'SELECT key, value FROM meta'))
        self.assertEqual((meta['file_format'], meta['bits']), ('data-1', str(BITS)))
        self.assertNotIn('bits_by_kind', meta)
        self.assertEqual(self.tables(self.t('keyed.db')), {'meta', *keyed.KINDS})

    def test_the_more_files_meta_names_each_kinds_count_and_no_one_count(self):
        for path, want in (('keyed-more.db', {k: BITS for k in keyed.KINDS + keyed.MORE_KINDS}),
                           ('keyed-sized.db', keyed.SIZED)):
            meta = dict(q(self.t(path), 'SELECT key, value FROM meta'))
            self.assertEqual(meta['file_format'], 'data-2', path)
            self.assertEqual(meta['layout'], 'keyed', path)
            self.assertNotIn('bits', meta, path)                          # a reader of one count fails, never misreads
            self.assertEqual(json.loads(meta['bits_by_kind']), want, path)
            # The names are kind rol, never a meta row: a release's manifest carries every meta row, and a reader's
            # first read of a copy is its whole meta.
            self.assertNotIn('more_names', meta, path)
            today = dict(q(self.t('keyed.db'), 'SELECT key, value FROM meta'))
            self.assertEqual({k: v for k, v in meta.items() if k not in ('file_format', 'bits_by_kind')},
                             {k: v for k, v in today.items() if k not in ('file_format', 'bits')}, path)
            self.assertEqual(self.tables(self.t(path)), {'meta', *keyed.KINDS, *keyed.MORE_KINDS}, path)

    def test_each_kind_is_bucketed_at_its_own_count(self):
        for kind in keyed.KINDS + keyed.MORE_KINDS:
            bits = keyed.SIZED[kind]
            want = {k: json.loads(v) for k, v in q(self.ent_more, 'SELECT k, v FROM e WHERE kind = ?', kind)}
            got = {}
            for b, recs in bucketed(self.t('keyed-sized.db'), kind).items():
                for k, rec in recs.items():
                    h = q(self.ent_more, 'SELECT h FROM e WHERE kind = ? AND k = ?', kind, k)[0][0]
                    self.assertEqual(b, h >> (63 - bits), (kind, k))
                    got[k] = rec
            self.assertEqual(got, want, kind)
            self.assertEqual(flat(self.t('keyed-more.db'), kind), want, kind)

    def test_the_same_records_give_the_same_bytes(self):
        again = self.t('keyed-sized-again.db')
        self.quiet(keyed.keyed, self.ent_more, self.t('tables.db'), 'sized', again)
        with open(again, 'rb') as x, open(self.t('keyed-sized.db'), 'rb') as y:
            self.assertEqual(x.read(), y.read())

    def test_sized_is_refused_for_todays_file(self):
        with self.assertRaises(SystemExit):
            self.quiet(keyed.keyed, self.ent, self.t('tables.db'), 'sized', self.t('refused.db'))

    def test_the_parts_carry_every_kind(self):
        man = self.quiet(load_parts.parts, self.t('keyed-sized.db'), self.t('rel-sized'), 10_000)
        self.assertEqual((man['format'], man['kinds']), ('load-1', keyed.KINDS + keyed.MORE_KINDS))
        got = {k: {} for k in man['kinds']}
        for p in man['parts']:
            for kind, k, v in part_records(os.path.join(self.t('rel-sized'), p['name']), man['kinds']):
                self.assertNotIn(k, got[kind])
                got[kind][k] = v
        want = {k: dict(q(self.t('keyed-sized.db'), f'SELECT k, v FROM {k}')) for k in man['kinds']}
        self.assertEqual(got, want)
        self.assertEqual(man['tables'], {k: len(v) for k, v in want.items()})
        self.assertEqual(dict(man['meta']), dict(q(self.t('keyed-sized.db'), 'SELECT key, value FROM meta')))

    def test_a_more_file_short_of_a_kind_is_refused_by_the_parts(self):
        short = self.t('keyed-sized-short.db')
        shutil.copyfile(self.t('keyed-sized.db'), short)
        c = sqlite3.connect(short)
        c.execute('DROP TABLE pgr')
        c.commit()
        c.close()
        with self.assertRaises(SystemExit):
            self.quiet(load_parts.parts, short, self.t('rel-short'), 10_000)
        self.assertFalse(os.path.exists(self.t('rel-short')) and os.listdir(self.t('rel-short')))


if __name__ == '__main__':
    unittest.main()
