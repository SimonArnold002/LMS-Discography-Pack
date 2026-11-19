"""The pack builder on the made-up dump (tests/fixture.py).

Needs an LMS-Discography checkout for the name keys: $DSC_DIR, else the
sibling folder ../LMS-Discography. Run: python3 -m unittest discover -s tests
"""
import os, shutil, sqlite3, subprocess, sys, tempfile, unittest, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER = os.path.join(HERE, '..', 'builder')
sys.path.insert(0, BUILDER)
sys.path.insert(0, HERE)
import fixture  # noqa: E402

DSC = os.environ.get('DSC_DIR') or os.path.join(HERE, '..', '..', 'LMS-Discography')


def fixture_gid_blob(n):
    """The blob the pack stores for the made-up dump's gid number n."""
    return uuid.UUID(fixture.g(n)).bytes


def build(src, out, dump):
    r = subprocess.run([sys.executable, '-I', os.path.join(BUILDER, 'build_pack.py'), src, out,
                        '--dsc', DSC, '--dump', dump], capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f'build failed:\n{r.stdout}\n{r.stderr}')
    return out


class Built(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.v1 = build(fixture.make_dump(os.path.join(cls.tmp, 'd1'), 1), os.path.join(cls.tmp, 'v1.db'), '20260101-000000')
        cls.v2 = build(fixture.make_dump(os.path.join(cls.tmp, 'd2'), 2), os.path.join(cls.tmp, 'v2.db'), '20260102-000000')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def q(self, db, sql, *args):
        c = sqlite3.connect(db)
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()


class TestBuild(Built):
    def test_format_and_version(self):
        meta = dict(self.q(self.v1, 'SELECT key, value FROM meta'))
        self.assertEqual(meta['format'], '3')
        self.assertEqual(meta['dump'], '20260101-000000')
        self.assertEqual(meta['dump_timestamp'], '2026-01-01 00:00:00.000000+00')

    def test_format_flag(self):
        out = subprocess.run([sys.executable, '-I', os.path.join(BUILDER, 'build_pack.py'), '--format'],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, '3')

    def test_tables(self):
        names = {r[0] for r in self.q(self.v1, "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('namekey_probe', names)
        cols = [r[1] for r in self.q(self.v1, 'PRAGMA table_info(artist_rel)')]
        self.assertEqual(cols, ['a', 'b', 'kind', 'epon', 'skey', 'named'])

    def test_relation_order_is_musicbrainz(self):
        # Bjork's bands: Kukl (1985-1988, ended), The Sugarcubes (1990), The
        # Elgar Sisters (undated, last); dated before undated, by begin date.
        order = [r[0] for r in self.q(self.v1, 'SELECT b FROM artist_rel WHERE a = 1 AND kind = 1 ORDER BY kind, skey')]
        self.assertEqual(order, [3, 2, 7])
        self.assertEqual(self.q(self.v1, 'SELECT epon FROM artist_rel WHERE a = 1 AND b = 2')[0][0], 1)

    def test_a_band_named_after_its_member_is_marked(self):
        # Björk is a member of "Björk & The Elgar Sisters" (7): named after her, not marked eponymous by MusicBrainz.
        # The Sugarcubes (2, eponymous) and Kukl (3) are not named after her; her collaboration (4) is no band link.
        rows = self.q(self.v1, 'SELECT b, kind, epon, named FROM artist_rel WHERE a = 1 ORDER BY b')
        self.assertEqual(rows, [(2, 1, 1, 0), (3, 1, 0, 0), (4, 2, 0, 0), (7, 1, 0, 1)])
        self.assertEqual(self.q(self.v1, 'SELECT count(*) FROM artist_rel WHERE named')[0][0], 1)

    def test_named_after_is_the_plugins_own_rule(self):
        # builder/namekeys.pl --named-after: Sources::_namedAfter on Sources::_norm, the cases its own comments name.
        cases = [('Oscar Peterson', 'The Oscar Peterson Trio', True), ('Miles Davis', 'Miles Davis Quintet', True),
                 ('Chick Corea', 'Chick Corea & Origin', True), ('Chick Corea', 'The Chick Corea + Steve Gadd Band', True),
                 ('The The', 'The The Band', True), ('Björk', 'Bjork Trio', True),
                 ('Frank Sinatra', 'The Nelson Riddle Orchestra', False), ('Davis', 'Miles Davis', False),
                 ('Miles Davis', 'Miles Davis', False), ('Miles Davis', 'The Miles Davis', False),
                 ('Miles', 'Milestones', False), ('John Coltrane', 'Miles Davis Quintet', False)]
        lines = ''.join(f'{i}\t{a}\t{b}\n' for i, (a, b, _) in enumerate(cases))
        # Three ways, one answer: the checkout's own sub where it has one, the script's copy of it (--named-after=own),
        # and a checkout from before the plugin had the sub (its Sources.pm with that sub cut out).
        with open(os.path.join(DSC, 'Discography', 'Sources.pm'), encoding='utf-8') as f:
            src = f.read()
        old = os.path.join(self.tmp, 'dsc-before')
        os.makedirs(os.path.join(old, 'Discography'), exist_ok=True)
        cut = src.replace('sub _namedAfter {', 'sub _namedAfterGone {')
        with open(os.path.join(old, 'Discography', 'Sources.pm'), 'w', encoding='utf-8') as f:
            f.write(cut)
        for dsc, flag in ((DSC, '--named-after'), (DSC, '--named-after=own'), (old, '--named-after')):
            with self.subTest(flag=flag, old=dsc is old):
                p = subprocess.run(['perl', os.path.join(HERE, '..', 'builder', 'namekeys.pl'), dsc, flag],
                                   input=lines.encode('utf-8'), capture_output=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual({int(x) for x in p.stdout.split()}, {i for i, c in enumerate(cases) if c[2]})

    def test_kept_and_dropped(self):
        ids = {r[0] for r in self.q(self.v1, 'SELECT id FROM artist')}
        self.assertEqual(ids, {1, 2, 3, 4, 5, 6, 7})          # 8 has nothing the pack keeps
        self.assertEqual(self.q(self.v1, 'SELECT ngroups FROM artist WHERE id = 1')[0][0], 3)
        self.assertEqual(self.q(self.v1, 'SELECT boot FROM rgx WHERE id = 1003')[0][0], 1)
        self.assertEqual(self.q(self.v1, 'SELECT title FROM rg_edition WHERE rg = 1000'), [('Debut (Deluxe)',)])
        self.assertEqual(self.q(self.v1, 'SELECT discogs FROM rgx WHERE id = 1000')[0][0], 999)
        self.assertEqual(self.q(self.v1, 'SELECT svc, ext FROM artist_url WHERE artist = 1'), [(3, 12345)])
        rels = self.q(self.v1, 'SELECT count(*) FROM release')[0][0]
        self.assertEqual(rels, 3)            # Joint (two artists), Hits (Various Artists), Debut credited with Kukl

    def test_release_map_own_credit(self):
        # Release 2004 (Bjork's group Debut, the release credited Bjork & Kukl):
        # mapped to Debut with Debut's types. Release 2005 (credited "Bjork",
        # the same artist spelt another way): not mapped.
        row = self.q(self.v1, 'SELECT hex(rg), type, sec FROM release WHERE gid = ?', fixture_gid_blob(2000 + 2004))
        self.assertEqual(row, [(fixture_gid_blob(1000 + 1000).hex().upper(), 1, None)])
        self.assertEqual(self.q(self.v1, 'SELECT count(*) FROM release WHERE gid = ?', fixture_gid_blob(2000 + 2005))[0][0], 0)
        # A single artist's release credited as its group is: not mapped.
        self.assertEqual(self.q(self.v1, 'SELECT count(*) FROM release WHERE gid = ?', fixture_gid_blob(2000 + 2000))[0][0], 0)

    def test_name_keys_are_dscs(self):
        keys = dict(self.q(self.v1, 'SELECT artist, key FROM artist_name'))
        self.assertEqual(keys[1], 'bjork')
        self.assertEqual(keys[6], 'sigur ros')
        probe = dict(self.q(self.v1, 'SELECT name, key FROM namekey_probe'))
        self.assertEqual(probe['Björk'], 'bjork')
        self.assertEqual(probe['米津玄師'], '米津玄師')
        self.assertGreater(len(probe), 25)


if __name__ == '__main__':
    unittest.main()
