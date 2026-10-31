"""measure_extra.py on the made-up dump (tests/fixture.py, plus the candidates'
tables made up here): what each candidate table holds, and that every variant
is today's pack plus exactly its own tables. Sizes of a few rows prove nothing;
the runner's run on the real dump is the measurement.
Run: python3 -m unittest discover -s tests
"""
import contextlib, io, os, shutil, sqlite3, sys, tempfile, unittest, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'builder'))
sys.path.insert(0, HERE)
import fixture, measure_extra  # noqa: E402
from test_pack import build  # noqa: E402

N, TS, g = fixture.N, fixture.TS, fixture.g


def blob(n):
    return uuid.UUID(g(n)).bytes


def add_candidates(root, caa=True):
    """The tables the candidates read, beside the fixture's (version 1)."""
    d = os.path.join(root, 'mbdump')
    w = lambda t, rows: fixture.write(d, t, rows)
    # A cancelled release (status 6): no date counts, never in a range.
    with open(os.path.join(d, 'release_status'), 'a', encoding='utf-8') as f:
        f.write('\t'.join(['6', 'Cancelled', N, '6', 'x', g(972)]) + '\n')
    bars = {2000: '5016958997028', 2001: '1234567', 2002: N, 2003: '', 2004: ' 0602537347234 ', 2005: 'CAT-001'}
    with open(os.path.join(d, 'release'), encoding='utf-8') as f:
        rel = [line.rstrip('\n').split('\t') for line in f]
    for r in rel:
        r[9] = bars[int(r[0])]
    rel.append([2006, g(4006), 'Debut (Cancelled)', 500, 1000, 6, N, N, N, '5016958997028', '', 0, -1, TS])
    # An official release with a disc whose tracklist MusicBrainz lacks: its
    # count is unknown, so it is in no range (7 tracks if it were).
    rel.append([2007, g(4007), 'Debut (Bonus Disc)', 500, 1000, 1, N, N, N, N, '', 0, -1, TS])
    w('release', rel)
    w('artist_credit', [[500, 'Björk', 1, 3, TS, 0, g(500)], [501, 'Björk & Sigur Rós', 2, 2, TS, 0, g(501)],
                        [502, 'Various Artists', 1, 2, TS, 0, g(502)], [503, 'Björk & Kukl', 2, 1, TS, 0, g(503)],
                        [504, 'Bjork', 1, 1, TS, 0, g(504)]])
    w('release_country', [[2000, 50, 1993, 7, 5], [2004, 50, 1993, N, N], [2006, 50, 1990, 1, 1],
                          [2001, 50, N, N, N], [2003, 50, 1995, 3, N]])
    w('release_unknown_country', [[2005, 1993, 6, N], [2002, N, N, N]])
    # A newer trailing column (as a schema change would add) must not stop the run.
    md = lambda i, rel, pos, n: [i, rel, pos, N, '', 0, TS, n, g(i), 'newer column']
    w('medium', [md(3000, 2000, 1, 4), md(3001, 2004, 1, 2), md(3002, 2004, 2, 1), md(3003, 2005, 1, 5),
                 md(3004, 2003, 1, 10), md(3005, 2001, 1, 0), md(3006, 2006, 1, 1),
                 md(3007, 2007, 1, 7), md(3008, 2007, 2, 0)])
    trk, n = [], 5000
    t = lambda m, ms, data='f', name='x': trk.append([n + len(trk), g(n + len(trk)), 1, m, 1, '1', name, 500,
                                                       ms, 0, TS, data])
    for ms in (200600, 180000, 220000):             # 600.6 s: 601 rounded, 600 cut
        t(3000, ms, name='Human\\tBehaviour')       # an escaped tab inside a value
    t(3000, 5000, data='t')                         # a data track: not audio
    t(3001, 100000)
    t(3001, N)                                      # a length unknown
    t(3002, 60000)
    for _ in range(5):
        t(3003, 130000)
    for _ in range(10):
        t(3004, 100000)
    t(3006, 1000)
    for _ in range(7):
        t(3007, 100000)
    w('track', trk)
    with open(os.path.join(d, 'url'), 'a', encoding='utf-8') as f:
        for i, u in [(310, 'https://www.qobuz.com/gb-en/album/debut-bjork/0075596111012'),
                     (311, 'https://open.qobuz.com/album/A1b2c3d4e5f6g'),
                     (312, 'https://www.deezer.com/album/302127'),
                     (313, 'https://tidal.com/browse/album/1234567'),
                     (314, 'https://open.spotify.com/album/3T4tUhGYeRNVUGevb0wThu'),
                     (315, 'https://www.discogs.com/release/123'),
                     (316, 'https://listen.tidal.com/album/7654321/'),
                     (317, 'https://www.deezer.com/artist/27')]:
            f.write('\t'.join([str(i), g(i), u, '0', TS]) + '\n')
    lru = lambda i, rel, url: [i, 30, rel, url, 0, TS, 0, '', '']
    w('l_release_url', [lru(500, 2000, 310), lru(501, 2004, 311), lru(502, 2000, 312), lru(503, 2001, 313),
                        lru(504, 2002, 314), lru(505, 2000, 315), lru(506, 2005, 316), lru(507, 2005, 317),
                        lru(508, 2006, 312)])
    if caa:
        c = os.path.join(root, 'caa', 'mbdump')
        os.makedirs(c, exist_ok=True)
        fixture.write(c, 'cover_art_archive.art_type', [[1, 'Front', N, 0, '', g(800)], [2, 'Back', N, 0, '', g(801)]])
        fixture.write(c, 'cover_art_archive.cover_art_type', [[90001, 1], [90002, 2], [90003, 2]])
        ca = lambda i, rel: [i, rel, '', 1, 1, TS, 0, 'image/jpeg', 100, 10, 20, 30]
        fixture.write(c, 'cover_art_archive.cover_art', [ca(90001, 2004), ca(90002, 2001), ca(90003, 2003)])
    return root


def q(db, sql, *args):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


class TestMeasureExtra(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        src = add_candidates(fixture.make_dump(os.path.join(cls.tmp, 'd'), 1))
        cls.base = build(src, os.path.join(cls.tmp, 'base.db'), '20260101-000000')
        cls.facts = os.path.join(cls.tmp, 'facts.db')
        with contextlib.redirect_stdout(io.StringIO()):
            measure_extra.facts(src, cls.facts)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cls.res = measure_extra.measure(cls.base, cls.facts, os.path.join(cls.tmp, 'out'), keep=True)
        cls.report = buf.getvalue()
        cls.stat = dict(q(cls.facts, 'SELECT key, value FROM stat'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def v(self, name):
        return self.res[name]['path']

    # --- lists -------------------------------------------------------------
    def test_lists_rows(self):
        self.assertEqual(q(self.v('lists'), 'SELECT * FROM rg ORDER BY id'),
                         [(1000, blob(2000), 'Debut', 19930600, 1, None, 1, None),
                          (1001, blob(2001), 'Joint', None, 1, None, 0, 501),
                          (1003, blob(2003), 'Bootleg Live', 19950300, 1, None, 0, None)])

    def test_lists_leave_out_various_artists(self):
        # Hits is Various Artists' alone: no list has it; VA has no list.
        self.assertEqual(q(self.v('lists'), 'SELECT * FROM artist_rg ORDER BY 1, 2'),
                         [(1, 1000), (1, 1001), (1, 1003), (6, 1001)])

    def test_lists_credit_only_when_not_plain(self):
        self.assertEqual(q(self.v('lists'), 'SELECT * FROM credit'), [(501, 'Björk & Sigur Rós')])

    def test_first_release_date_rule(self):
        # Debut: 1993-07-05, 1993 (month and day unknown) and 1993-06 -> 1993-06,
        # each part NULLS LAST; the cancelled 1990-01-01 does not count. Joint's
        # only event has no date part: none.
        d = dict(q(self.facts, 'SELECT id, date FROM f_rg'))
        self.assertEqual((d[1000], d[1001], d[1002], d[1003]), (19930600, None, None, 19950300))

    def test_cover_flag_is_a_front(self):
        d = dict(q(self.facts, 'SELECT id, cover FROM f_rg'))
        self.assertEqual((d[1000], d[1001], d[1002], d[1003]), (1, 0, 0, 0))

    # --- relmap ------------------------------------------------------------------
    def test_relmap_maps_every_release(self):
        base = q(self.base, 'SELECT * FROM release ORDER BY gid')
        full = q(self.v('lists+relmap'), 'SELECT * FROM release ORDER BY gid')
        self.assertEqual(len(base), 3)
        self.assertTrue(set(base) <= set(full))
        rg = {blob(2000 + i): blob(1000 + g_) for i, g_ in
              [(2000, 1000), (2001, 1001), (2002, 1002), (2003, 1003), (2004, 1000), (2005, 1000), (2006, 1000),
               (2007, 1000)]}
        self.assertEqual({r: x for r, x, _, _ in full}, rg)
        self.assertEqual({r[3] for r in full if r[1] == blob(2002)}, {2})   # Hits: Compilation

    # --- layer A --------------------------------------------------------------------
    def test_barcodes(self):
        self.assertEqual(q(self.v('barcode'), 'SELECT * FROM barcode ORDER BY 1'),
                         [(602537347234, 1000), (5016958997028, 1000)])
        self.assertEqual(q(self.v('barcode'), 'SELECT * FROM rg_gid'), [(1000, blob(2000))])
        self.assertEqual(self.stat['releases with a barcode of 8-14 digits'], 3)
        self.assertEqual(self.stat['releases with another barcode (letters, too short or long), left out'], 2)

    def test_service_album_ids(self):
        self.assertEqual(sorted(q(self.v('links'), 'SELECT svc, ext, rg FROM svc_album'), key=repr),
                         sorted([(1, '0075596111012', 1000), (1, 'a1b2c3d4e5f6g', 1000), (2, 302127, 1000),
                                 (4, 1234567, 1001), (4, 7654321, 1000),
                                 (5, '3T4tUhGYeRNVUGevb0wThu', 1002)], key=repr))
        self.assertEqual(q(self.v('links'), 'SELECT typeof(ext) FROM svc_album WHERE svc = 2'), [('integer',)])
        self.assertEqual([r[0] for r in q(self.v('links'), 'SELECT id FROM rg_gid ORDER BY id')], [1000, 1001, 1002])
        self.assertEqual((self.stat['  Qobuz'], self.stat['  Deezer'], self.stat['  TIDAL'], self.stat['  Spotify']),
                         (2, 2, 2, 1))

    def test_layer_a_shares_one_gid_table(self):
        self.assertEqual([r[0] for r in q(self.v('barcode+links'), 'SELECT id FROM rg_gid ORDER BY id')],
                         [1000, 1001, 1002])

    # --- layer C ----------------------------------------------------------------------
    def test_ranges(self):
        # Debut: 3 tracks / 600.6 s, 3 tracks / a length unknown, 5 tracks / 650 s;
        # the data track, the cancelled release and the one with a disc of no
        # tracklist left out. Joint's medium has no tracklist, Bootleg Live is a
        # bootleg, Hits has no medium: no row.
        self.assertEqual(q(self.v('range'), 'SELECT * FROM rg_range'), [(blob(2000), 3, 5, 601, 650)])
        self.assertEqual((self.stat['tracks'], self.stat['  data tracks, left out'],
                          self.stat['  audio tracks with no length']), (30, 1, 1))

    # --- all -----------------------------------------------------------------------------
    def test_all_holds_the_groups_the_maps_name(self):
        # Hits is in no list, but a Spotify link names it: rg takes its row, no rg_gid.
        tabs = {t for (t,) in q(self.v('all'), "SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn('rg_gid', tabs)
        self.assertEqual([r[0] for r in q(self.v('all'), 'SELECT id FROM rg ORDER BY id')], [1000, 1001, 1002, 1003])
        self.assertEqual(q(self.v('all'), 'SELECT * FROM credit'), [(501, 'Björk & Sigur Rós')])

    def test_every_variant_is_the_base_plus_its_tables(self):
        for name, _, parts in measure_extra.VARIANTS:
            c = sqlite3.connect(self.v(name))
            c.execute('ATTACH ? AS b', (self.base,))
            want_new, want_grown = measure_extra.expected(parts)
            base_t = {t for (t,) in c.execute("SELECT name FROM b.sqlite_master WHERE type = 'table'")}
            mine = {t for (t,) in c.execute("SELECT name FROM main.sqlite_master WHERE type = 'table'")}
            self.assertEqual(mine - base_t, want_new, name)
            for t in base_t:
                lost = c.execute(f'SELECT count(*) FROM (SELECT * FROM b."{t}" EXCEPT SELECT * FROM main."{t}")').fetchone()[0]
                more = c.execute(f'SELECT count(*) FROM (SELECT * FROM main."{t}" EXCEPT SELECT * FROM b."{t}")').fetchone()[0]
                self.assertEqual(lost, 0, f'{name}: {t} lost rows')
                if t not in want_grown:
                    self.assertEqual(more, 0, f'{name}: {t} gained rows')
            c.close()

    def test_report(self):
        self.assertIn("self-check: every variant is today's pack plus exactly its own tables", self.report)
        for _, label, _ in measure_extra.VARIANTS:
            self.assertIn(label, self.report)
        for name in ['base'] + [n for n, *_ in measure_extra.VARIANTS]:
            self.assertGreater(self.res[name]['gz'], 0, name)
            self.assertEqual(self.res[name]['size'], os.path.getsize(self.res[name]['path']), name)

    def test_gzipped_as_a_release_is(self):
        # build.yml's `gzip -9 -n`: the header says maximum compression (XFL 2),
        # no file name, no time.
        for name in ['base'] + [n for n, *_ in measure_extra.VARIANTS]:
            with open(self.res[name]['path'] + '.gz', 'rb') as f:
                h = f.read(10)
            self.assertEqual((h[:3], h[3] & 0x08, h[4:8], h[8]), (b'\x1f\x8b\x08', 0, b'\0\0\0\0', 2), name)

    def test_report_cross_checks_a_published_pack(self):
        man = os.path.join(self.tmp, 'manifest.json')
        with open(man, 'w') as f:
            f.write('{"pack": {"dump": "20260101-000000", "db_size": 1, "size": 2}}')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            measure_extra.report(self.res, {'dump': '20260101-000000', 'format': '3'},
                                 q(self.facts, 'SELECT key, value FROM stat'), man)
        self.assertIn('published pack-20260101-000000 (manifest.json): 1 bytes on disk, 2 gzipped', buf.getvalue())


class TestNoCoverArtTables(unittest.TestCase):
    def test_flag_left_null_and_said(self):
        tmp = tempfile.mkdtemp()
        try:
            src = add_candidates(fixture.make_dump(os.path.join(tmp, 'd'), 1), caa=False)
            facts = os.path.join(tmp, 'facts.db')
            with contextlib.redirect_stdout(io.StringIO()):
                measure_extra.facts(src, facts)
            self.assertEqual(q(facts, 'SELECT DISTINCT cover FROM f_rg'), [(None,)])
            self.assertTrue(dict(q(facts, 'SELECT key, value FROM stat'))['cover flags'].startswith('NOT MEASURED'))
        finally:
            shutil.rmtree(tmp)


class TestColumns(unittest.TestCase):
    def test_short_row_stops_the_run(self):
        tmp = tempfile.mkdtemp()
        try:
            src = add_candidates(fixture.make_dump(os.path.join(tmp, 'd'), 1))
            with open(os.path.join(src, 'mbdump', 'medium'), 'a', encoding='utf-8') as f:
                f.write('3999\n')
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                measure_extra.facts(src, os.path.join(tmp, 'facts.db'))
        finally:
            shutil.rmtree(tmp)


if __name__ == '__main__':
    unittest.main()
