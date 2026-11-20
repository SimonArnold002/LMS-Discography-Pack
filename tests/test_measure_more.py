"""measure_more.py on the made-up dump (tests/fixture.py, test_measure_extra's and test_keyed's extra tables, and the
second pass's tables made up here): what each new kind holds, record by record, and that `measure` adds exactly
those kinds to a copy of today's keyed file, leaving today's kinds as they were. Sizes of a few rows prove nothing;
the runner's run on the real dump is the measurement.
Needs perl and an LMS-Discography checkout ($DSC_DIR, else ../LMS-Discography) for today's file's name keys.
Run: python3 -m unittest discover -s tests
"""
import contextlib, io, json, os, shutil, sqlite3, sys, tempfile, unittest, uuid, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'builder'))
sys.path.insert(0, HERE)
import fixture, keyed, measure_extra, measure_more  # noqa: E402
from test_keyed import add_extras  # noqa: E402
from test_measure_extra import add_candidates  # noqa: E402
from test_pack import DSC, build  # noqa: E402

N, TS, g = fixture.N, fixture.TS, fixture.g
BITS = 3
hx = lambda n: uuid.UUID(g(n)).hex


def second_pass(src, root):
    """The second pass's folder: ONLY the tables measure_more.fetch takes (copied from the first pass, or made up
    here when the first pass has none), plus the derived dump's three, so a table it reads but does not fetch fails."""
    d, der = os.path.join(root, 'mbdump'), os.path.join(root, 'derived', 'mbdump')
    os.makedirs(d)
    os.makedirs(der)
    for t in measure_more.MORE_CORE:
        if os.path.exists(os.path.join(src, 'mbdump', t)):
            shutil.copyfile(os.path.join(src, 'mbdump', t), os.path.join(d, t))
    w = lambda t, rows, where=d: fixture.write(where, t, rows)

    def add(table, rows):
        with open(os.path.join(d, table), 'a', encoding='utf-8', newline='\n') as f:
            for r in rows:
                f.write('\t'.join(N if v is None else str(v) for v in r) + '\n')

    lt = lambda i, e0, e1, name: [i, N, 0, g(900 + i), e0, e1, name, '', '', '', '', TS, 'f', 't', 0, 0]
    add('link_type', [lt(20, 'artist', 'recording', 'instrument'), lt(21, 'artist', 'recording', 'vocal'),
                      lt(22, 'artist', 'recording', 'producer'), lt(23, 'artist', 'release', 'design/illustration'),
                      lt(24, 'artist', 'release_group', 'tribute'), lt(25, 'artist', 'url', 'wikidata'),
                      lt(26, 'artist', 'url', 'official homepage'), lt(27, 'artist', 'url', 'bandcamp'),
                      lt(28, 'artist', 'artist', 'is person'), lt(29, 'artist', 'url', 'allmusic')])
    add('link_attribute_type', [[3, N, 3, 0, g(952), 'lead vocals', 'x', TS], [4, N, 2, 0, g(953), 'piano', 'x', TS]])
    ln = lambda i, t: [i, t, N, N, N, N, N, N, 0, TS, 'f']
    add('link', [ln(30, 20), ln(31, 21), ln(32, 22), ln(33, 23), ln(34, 24), ln(35, 25), ln(36, 26), ln(37, 27),
                 [38, 28, 1965, 11, 21, 1990, N, N, 0, TS, 't'], ln(39, 29)])
    add('link_attribute', [[30, 4, TS], [30, 2, TS], [31, 3, TS]])          # out of order: read back sorted
    l = lambda i, link, a, b, order=0: [i, link, a, b, 0, TS, order, '', '']
    add('l_artist_artist', [l(106, 38, 8, 1)])
    # Björk's homepages: link order against id order (203 first by its order).
    add('l_artist_url', [l(201, 35, 1, 320), l(202, 36, 1, 321, 1), l(203, 36, 1, 322, 0), l(204, 37, 6, 323),
                         l(205, 39, 6, 324)])
    add('url', [[320, g(320), 'https://www.wikidata.org/wiki/Q42', 0, TS], [321, g(321), 'https://bjork.com/', 0, TS],
                [322, g(322), 'https://www.bjork.com/', 0, TS], [323, g(323), 'https://sigurros.bandcamp.com/', 0, TS],
                [324, g(324), 'https://www.allmusic.com/artist/mn0000', 0, TS]])
    # A newer trailing column (as a schema change would add) must not stop the run.
    rc = lambda i, name, more=(): [i, g(i), name, 500, 252000, '', 0, TS, 'f', *more]
    w('recording', [rc(6000, 'Human Behaviour', ['newer column']), rc(6001, 'Venus as a Boy'), rc(6002, 'Unsung')])
    # Link order against id order: Björk's link (order 0) comes first though Sigur Rós's has the lower id.
    w('l_artist_recording', [l(301, 30, 1, 6000, 0), l(302, 32, 8, 6001), l(300, 31, 6, 6000, 1)])
    w('l_artist_release', [l(400, 33, 3, 2000)])                          # Kukl: credited on no recording
    w('l_artist_release_group', [l(500, 34, 6, 1000)])
    # A catalogue number with an escaped backslash (COPY text writes one as two).
    w('release_label', [[1, 2000, 700, 'TPLP 31', TS], [2, 2000, 701, N, TS], [3, 2004, N, 'CAT\\\\1', TS]])
    lb = lambda i, name: [i, g(i), name, N, N, N, N, N, N, N, N, N, '', 0, TS, 'f']
    w('label', [lb(700, 'One Little Indian'), lb(701, 'Elektra')])
    ar = lambda i, name, typ: [i, g(i), name, typ, 0, TS, N, N, N, N, N, N, 'f', '']
    w('area', [ar(50, 'Iceland', 1), ar(51, 'Reykjavík', 3)])
    # Release 2000's first date stays 1993-07-05: a later event, and one of 1993 with no month (which sorts after it).
    add('release_unknown_country', [[2000, 1999, 1, 1], [2000, 1993, N, N]])
    w('genre', [[1, g(801), 'electronic', '', 0, TS], [2, g(802), 'art pop', '', 0, TS], [3, g(803), 'trip hop', '', 0, TS]])
    # Björk born 1965-11-21 in Reykjavík, from Iceland; The Sugarcubes 1986-1992, ended.
    with open(os.path.join(d, 'artist'), encoding='utf-8') as f:
        art = [line.rstrip('\n').split('\t') for line in f]
    for r in art:
        if r[0] == '1':
            r[4:7], r[11], r[17] = ['1965', '11', '21'], '50', '51'
        elif r[0] == '2':
            r[4], r[7], r[16] = '1986', '1992', 't'
    w('artist', art)
    w('tag', [[10, 'electronic', 5], [11, 'Art Pop', 2], [12, 'seen live', 9], [13, 'trip hop', 1],
              [14, 'Electronic', 1]], der)
    w('artist_tag', [[1, 10, 3, TS], [1, 11, 5, TS], [1, 12, 9, TS], [1, 13, 0, TS], [1, 14, 1, TS],
                     [2, 10, -1, TS]], der)
    w('release_group_tag', [[1000, 10, 2, TS], [1000, 13, 2, TS], [1001, 12, 3, TS]], der)
    return root


def q(db, sql, *args):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


class More(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        t = lambda n: os.path.join(cls.tmp, n)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            src = add_extras(add_candidates(fixture.make_dump(t('d1'), 1)), 1)
            base = build(src, t('base.db'), '20260101-000000')
            measure_extra.facts(src, t('facts.db'))
            keyed.aliases(base, DSC, t('aliases.tsv'))
            keyed.narrow(base, t('facts.db'), t('aliases.tsv'), t('tables.db'))
            keyed.entries(t('tables.db'), t('entries.db'))
            keyed.keyed(t('entries.db'), t('tables.db'), BITS, t('keyed.db'))
            cls.src2 = second_pass(src, t('m'))
            cls.made = measure_more.entries(cls.src2, t('more.db'))
            cls.fig = measure_more.measure(t('keyed.db'), t('more.db'), t('out'))
        cls.keyed, cls.more, cls.out = t('keyed.db'), t('more.db'), t('out')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def entry(self, kind, k):
        r = q(self.more, 'SELECT v FROM e WHERE kind = ? AND k = ?', kind, k)
        return json.loads(r[0][0]) if r else None

    def keys(self, kind):
        return sorted(k for (k,) in q(self.more, 'SELECT k FROM e WHERE kind = ?', kind))


class TestHelpers(unittest.TestCase):
    def test_trim_drops_trailing_empties_only(self):
        self.assertEqual(measure_more.trim([1, None, 0, [], '']), [1])
        self.assertEqual(measure_more.trim([0, 1, None, 0]), [0, 1])
        self.assertEqual(measure_more.trim([None, [], 2]), [None, [], 2])
        self.assertEqual(measure_more.trim([None, 0]), [])
        self.assertEqual(measure_more.trim(['x', [0]]), ['x', [0]])

    def test_heads_reads_the_first_fields_as_written(self):
        d = tempfile.mkdtemp()
        try:
            fixture.write(d, 't', [[1, 'a\\tb', 3], [4, 5, 6]])
            self.assertEqual(list(measure_more.heads(d, 't', 3)), [['1', 'a\\tb', '3'], ['4', '5', '6']])
            self.assertEqual(list(measure_more.heads(d, 't', 2)), [['1', 'a\\tb'], ['4', '5']])
            with self.assertRaises(SystemExit):
                list(measure_more.heads(d, 't', 4))                   # a row with fewer fields stops the run
        finally:
            shutil.rmtree(d)

    def test_fetch_takes_the_tables_and_the_tags(self):
        calls, old = [], (measure_more.fetch_dump.fetch, measure_more.measure_extra.fetch_tar, measure_more.fetch_dump.TABLES)
        measure_more.fetch_dump.fetch = lambda dump, out: calls.append(('main', dump, out, list(measure_more.fetch_dump.TABLES)))
        measure_more.measure_extra.fetch_tar = lambda dump, tar, tables, out: calls.append(('tar', dump, tar, tables, out))
        try:
            measure_more.fetch('20260101-000000', '/x/work2')
        finally:
            measure_more.fetch_dump.fetch, measure_more.measure_extra.fetch_tar, measure_more.fetch_dump.TABLES = old
        self.assertEqual(calls, [('main', '20260101-000000', '/x/work2', measure_more.MORE_CORE),
                                 ('tar', '20260101-000000', 'mbdump-derived.tar.bz2', ['tag', 'artist_tag', 'release_group_tag'],
                                  '/x/work2/derived')])

    def test_date_keeps_the_parts_known(self):
        self.assertEqual(measure_more.date('1965', '11', '21'), 19651121)
        self.assertEqual(measure_more.date('1965', None, None), 19650000)
        self.assertEqual(measure_more.date('1965', None, '3'), 19650003)
        self.assertIsNone(measure_more.date(None, '11', '21'))


class TestEntries(More):
    def test_it_reads_only_what_fetch_takes(self):
        have = set(os.listdir(os.path.join(self.src2, 'mbdump')))
        self.assertEqual(have, set(measure_more.MORE_CORE))
        self.assertEqual(set(os.listdir(os.path.join(self.src2, 'derived', 'mbdump'))), set(measure_more.DERIVED_TABLES))

    def test_abo_where_from_and_years(self):
        self.assertEqual(self.entry('abo', hx(1)), [19651121, None, 0, 'Iceland', 'Reykjavík'])
        self.assertEqual(self.entry('abo', hx(2)), [19860000, 19920000, 1])
        self.assertIsNone(self.entry('abo', hx(3)))                      # nothing known: no record
        self.assertEqual(self.keys('abo'), sorted([hx(1), hx(2)]))

    def test_aur_wikidata_homepages_bandcamp(self):
        self.assertEqual(self.entry('aur', hx(1)), [42, ['https://www.bjork.com/', 'https://bjork.com/']])
        self.assertEqual(self.entry('aur', hx(6)), [None, [], ['https://sigurros.bandcamp.com/']])
        self.assertEqual(self.keys('aur'), sorted([hx(1), hx(6)]))    # Discogs and AllMusic links: not these kinds

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

    def test_crx_is_crd_with_the_artists_written_in(self):
        self.assertEqual(self.entry('crx', hx(6000)), [[hx(1), 'Björk', 20, [2, 4]], [hx(6), 'Sigur Rós', 21, [3]]])
        self.assertEqual(self.keys('crx'), self.keys('crd'))

    def test_who_names_every_artist_crd_and_rcr_name_and_no_other(self):
        self.assertEqual(self.keys('who'), ['1', '3', '6', '8'])             # 3: on a release only
        self.assertEqual(self.entry('who', '8'), [hx(8), 'Unlinked Nobody'])   # in no list, still named
        ids = {r[0] for k in self.keys('crd') for r in self.entry('crd', k)} | \
              {r[0] for k in self.keys('rcr') for r in self.entry('rcr', k)}
        self.assertEqual({str(i) for i in ids}, set(self.keys('who')))

    def test_rcr_a_releases_own_credits(self):
        self.assertEqual(self.entry('rcr', hx(4000)), [[3, 23]])
        self.assertEqual(self.keys('rcr'), [hx(4000)])

    def test_rlb_labels_and_catalogue_numbers(self):
        self.assertEqual(self.entry('rlb', hx(4000)), [['One Little Indian', 'TPLP 31'], ['Elektra']])
        self.assertEqual(self.entry('rlb', hx(4004)), [[None, 'CAT\\1']])

    def test_per_whole_record_links_with_title_date_credit(self):
        self.assertEqual(self.entry('per', hx(3)), [[hx(4000), hx(2000), 'Debut (Deluxe)', 19930705, 'Björk', 23]])
        self.assertEqual(self.entry('per', hx(6)), [[None, hx(2000), 'Debut', None, 'Björk', 24]])
        self.assertEqual(self.keys('per'), sorted([hx(3), hx(6)]))

    def test_genres_from_the_tags_most_votes_first(self):
        self.assertEqual(self.entry('agn', hx(1)), [[2, 5], [1, 4]])     # two tags of one genre add up; 0 votes out
        self.assertIsNone(self.entry('agn', hx(2)))                      # votes below 0: none
        self.assertEqual(self.entry('ggn', hx(2000)), [[1, 2], [3, 2]])   # a tie: by genre id
        self.assertEqual(self.keys('ggn'), [hx(2000)])                   # a tag that is no genre: nothing

    def test_the_names_ride_in_meta(self):
        names = json.loads(q(self.more, "SELECT value FROM meta WHERE key = 'more_names'")[0][0])
        self.assertEqual(names['link_type']['20'], 'instrument')
        self.assertEqual(names['attribute']['4'], 'piano')
        self.assertEqual(names['genre']['2'], 'art pop')

    def test_the_counts_are_kept(self):
        stat = dict(q(self.more, 'SELECT key, value FROM stat'))
        self.assertEqual(stat['l_artist_recording rows'], 3)
        self.assertEqual(stat['recordings'], 3)
        self.assertEqual(stat['  with credits'], 2)
        self.assertEqual(stat['  artist links to official homepage'], 2)
        for k in measure_more.KINDS + measure_more.COMPARE:
            self.assertEqual(stat[f'records {k}'], len(self.keys(k)), k)
            self.assertEqual(self.made[k], len(self.keys(k)), k)

    def test_two_records_for_one_key_stop_the_run(self):
        d = os.path.join(self.tmp, 'dup')
        shutil.copytree(self.src2, d)
        with open(os.path.join(d, 'mbdump', 'artist'), 'a', encoding='utf-8') as f:     # a second row for Björk's gid
            f.write('\t'.join(['99', g(1), 'Bjork', 'Bjork', '1965', N, N, N, N, N, '1', N, N, '', '0', TS, 'f', N, N]) + '\n')
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            measure_more.entries(d, os.path.join(self.tmp, 'dup.db'))

    def test_a_genre_cap(self):
        out = os.path.join(self.tmp, 'cap.db')
        old = measure_more.GENRES
        measure_more.GENRES = 1
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                measure_more.entries(self.src2, out)
        finally:
            measure_more.GENRES = old
        self.assertEqual(json.loads(q(out, "SELECT v FROM e WHERE kind = 'agn' AND k = ?", hx(1))[0][0]), [[2, 5]])


class TestMeasure(More):
    def comb(self):
        return os.path.join(self.out, 'keyed-more.db')

    def test_todays_kinds_are_left_as_they_were(self):
        for kind in keyed.KINDS:
            self.assertEqual(q(self.comb(), f'SELECT k, v FROM {kind} ORDER BY k'),
                             q(self.keyed, f'SELECT k, v FROM {kind} ORDER BY k'), kind)
        before = dict(q(self.keyed, 'SELECT key, value FROM meta'))
        after = dict(q(self.comb(), 'SELECT key, value FROM meta'))
        self.assertEqual({k: v for k, v in after.items() if k != 'more_names'}, before)
        self.assertIn('more_names', after)

    def test_each_new_kind_holds_its_records_bucketed_the_builds_way(self):
        tables = {t for (t,) in q(self.comb(), "SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(tables, set(keyed.KINDS) | set(measure_more.KINDS) | {'meta'})   # crx: not in the file
        shift = 63 - BITS
        for kind in measure_more.KINDS:
            got = {}
            for b, v in q(self.comb(), f'SELECT k, v FROM {kind}'):
                for k, rec in json.loads(zlib.decompress(v, -15)).items():
                    h = q(self.more, 'SELECT h FROM e WHERE kind = ? AND k = ?', kind, k)[0][0]
                    self.assertEqual(b, (h >> shift).to_bytes(4, 'big'), (kind, k))
                    got[k] = rec
            self.assertEqual(got, {k: json.loads(v) for k, v in q(self.more, 'SELECT k, v FROM e WHERE kind = ?', kind)},
                             kind)
            self.assertEqual(self.fig[kind]['records'], len(got), kind)
            self.assertEqual(self.fig[kind]['rows'], len(q(self.comb(), f'SELECT k FROM {kind}')), kind)

    def test_a_gid_kind_hashes_the_gid_as_the_build_does(self):
        h = q(self.more, "SELECT h FROM e WHERE kind = 'crd' AND k = ?", hx(6000))[0][0]
        self.assertEqual(h, keyed._h_gid(uuid.UUID(g(6000)).bytes))
        h = q(self.more, "SELECT h FROM e WHERE kind = 'who' AND k = '8'")[0][0]
        self.assertEqual(h, keyed._h_text('8'))

    def test_the_report(self):
        with open(os.path.join(self.out, 'report.txt'), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('dump 20260101-000000', text)
        for kind in measure_more.KINDS + measure_more.COMPARE:
            self.assertRegex(text, rf'\n{kind} +{self.fig[kind]["records"]:,} ')
        for name, _ in measure_more.FEATURES:
            self.assertIn(name, text)
        self.assertIn('All of them', text)
        self.assertIn('Over the 2 MB cap: none.', text)
        self.assertIn(f'With the kinds below: ', text)
        self.assertFalse(os.path.exists(os.path.join(self.out, 'compare.db')))

    def test_it_refuses_a_file_that_is_not_bucketed_or_already_holds_the_kinds(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                measure_more.measure(self.comb(), self.more, os.path.join(self.tmp, 'again'))
            flat = os.path.join(self.tmp, 'flat.db')
            keyed.keyed(os.path.join(self.tmp, 'entries.db'), os.path.join(self.tmp, 'tables.db'), 0, flat)
            with self.assertRaises(SystemExit):
                measure_more.measure(flat, self.more, os.path.join(self.tmp, 'flat'))


if __name__ == '__main__':
    unittest.main()
