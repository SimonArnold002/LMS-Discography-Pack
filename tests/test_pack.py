"""Builder, delta and manifest tests on the made-up dump (tests/fixture.py).

Needs an LMS-Discography checkout for the name keys: $DSC_DIR, else the
sibling folder ../LMS-Discography. Run: python3 -m unittest discover -s tests
"""
import gzip, json, os, shutil, sqlite3, subprocess, sys, tempfile, unittest, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER = os.path.join(HERE, '..', 'builder')
sys.path.insert(0, BUILDER)
sys.path.insert(0, HERE)
import fixture, make_delta, release  # noqa: E402

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
        self.assertEqual(meta['format'], '4')
        self.assertEqual(meta['dump'], '20260101-000000')
        self.assertEqual(meta['dump_timestamp'], '2026-01-01 00:00:00.000000+00')

    def test_format_flag(self):
        out = subprocess.run([sys.executable, '-I', os.path.join(BUILDER, 'build_pack.py'), '--format'],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, '4')

    def test_tables(self):
        names = {r[0] for r in self.q(self.v1, "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('namekey_probe', names)
        cols = [r[1] for r in self.q(self.v1, 'PRAGMA table_info(artist_rel)')]
        self.assertEqual(cols, ['a', 'b', 'kind', 'epon', 'skey'])
        # Format 4: the lookups are tables, artist_info is on the artist row.
        self.assertTrue({'artist_gk', 'artist_rel_b'} <= names)
        self.assertNotIn('rgx_gk', names)
        self.assertEqual([r[1] for r in self.q(self.v1, 'PRAGMA table_info(rgx)')],
                         ['gk', 'id', 'gid', 'boot', 'discogs'])
        self.assertNotIn('artist_info', names)
        self.assertEqual([r[1] for r in self.q(self.v1, 'PRAGMA table_info(artist)')],
                         ['id', 'gid', 'name', 'ngroups', 'comment', 'type', 'country'])

    def test_no_secondary_index(self):
        # A device compacts by copying each table in one statement; an index
        # would have to be rebuilt in one go. Not even sqlite_stat1 (no ANALYZE).
        objs = self.q(self.v1, "SELECT type, name FROM sqlite_master WHERE type != 'table' OR name LIKE 'sqlite%'")
        self.assertEqual(objs, [])

    def test_lookup_tables_mirror_their_tables(self):
        for v in (self.v1, self.v2):
            self.assertEqual(sorted(self.q(v, 'SELECT gk, id FROM artist_gk')),
                             sorted(self.q(v, 'SELECT substr(gid, 1, 8), id FROM artist')))
            self.assertEqual(self.q(v, 'SELECT count(*) FROM rgx WHERE gk != substr(gid, 1, 8)')[0][0], 0)
            self.assertGreater(self.q(v, 'SELECT count(*) FROM rgx')[0][0], 0)
            self.assertEqual(sorted(self.q(v, 'SELECT b, a, kind FROM artist_rel_b')),
                             sorted(self.q(v, 'SELECT b, a, kind FROM artist_rel')))
            self.assertGreater(len(self.q(v, 'SELECT 1 FROM artist_rel_b')), 0)

    def test_artist_info_on_the_artist_row(self):
        rows = self.q(self.v1, 'SELECT count(*) FROM artist WHERE comment IS NOT NULL OR type IS NOT NULL OR country IS NOT NULL')
        self.assertGreater(rows[0][0], 0)

    def test_relation_order_is_musicbrainz(self):
        # Bjork's bands: Kukl (1985-1988, ended), The Sugarcubes (1990), The
        # Elgar Sisters (undated, last); dated before undated, by begin date.
        order = [r[0] for r in self.q(self.v1, 'SELECT b FROM artist_rel WHERE a = 1 AND kind = 1 ORDER BY kind, skey')]
        self.assertEqual(order, [3, 2, 7])
        self.assertEqual(self.q(self.v1, 'SELECT epon FROM artist_rel WHERE a = 1 AND b = 2')[0][0], 1)

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


class TestDelta(Built):
    def test_minimal_and_proven(self):
        out = os.path.join(self.tmp, 'd12.db')
        frm, to, counts = make_delta.make(self.v1, self.v2, out)
        self.assertEqual((frm, to), ('20260101-000000', '20260102-000000'))
        # One new link is ONE row: per-relation sort keys, not a global rank.
        self.assertEqual(counts['artist_rel'], (1, 0))
        self.assertEqual(counts['artist_url'], (0, 1))
        self.assertEqual(counts['artist_alias'], (1, 0))
        self.assertEqual(counts['artist_type'], (0, 0))
        self.assertGreaterEqual(counts['artist'][0], 2)          # renamed duo + Bjork's new group count
        self.assertEqual(make_delta.verify(self.v1, self.v2, out), '')

    def test_shape(self):
        out = os.path.join(self.tmp, 'd12s.db')
        make_delta.make(self.v1, self.v2, out)
        names = {r[0] for r in self.q(out, "SELECT name FROM sqlite_master")}
        pack = {r[0] for r in self.q(self.v2, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite%'")}
        self.assertEqual(names, {'delta_meta'} | {f'put_{t}' for t in pack} | {f'del_{t}' for t in pack})
        self.assertEqual([r[1] for r in self.q(out, 'PRAGMA table_info(put_artist_rel)')],
                         ['a', 'b', 'kind', 'epon', 'skey'])
        self.assertEqual([r[1] for r in self.q(out, 'PRAGMA table_info(del_artist_alias)')],
                         ['artist', 'name', 'locale'])
        self.assertEqual(dict(self.q(out, 'SELECT key, value FROM delta_meta')),
                         {'format': '4', 'from': '20260101-000000', 'to': '20260102-000000'})

    def test_tampered_delta_fails_its_proof(self):
        out = os.path.join(self.tmp, 'd12t.db')
        make_delta.make(self.v1, self.v2, out)
        c = sqlite3.connect(out)
        c.execute('DELETE FROM put_artist_alias')
        c.commit()
        c.close()
        self.assertIn('artist_alias', make_delta.verify(self.v1, self.v2, out))

    def test_wrong_base_refused(self):
        out = os.path.join(self.tmp, 'd12w.db')
        make_delta.make(self.v1, self.v2, out)
        copy = os.path.join(self.tmp, 'v2copy.db')
        shutil.copyfile(self.v2, copy)
        with self.assertRaises(ValueError):
            make_delta.apply(copy, out)                         # v2 is not the delta's base
        self.assertEqual(make_delta.same(copy, self.v2), '')    # and nothing changed

    def test_small_batches(self):
        out = os.path.join(self.tmp, 'd12b.db')
        make_delta.make(self.v1, self.v2, out)
        copy = os.path.join(self.tmp, 'v1copy.db')
        shutil.copyfile(self.v1, copy)
        make_delta.apply(copy, out, batch=1)
        self.assertEqual(make_delta.same(copy, self.v2), '')

    def test_format_change_gives_no_delta(self):
        odd = os.path.join(self.tmp, 'odd.db')
        shutil.copyfile(self.v1, odd)
        c = sqlite3.connect(odd)
        c.execute('CREATE TABLE extra (x INTEGER PRIMARY KEY)')
        c.commit()
        c.close()
        with self.assertRaises(make_delta.FormatChange):
            make_delta.make(odd, self.v2, os.path.join(self.tmp, 'dx.db'))


class TestFetchPrev(Built):
    def run_fetch(self, manifest):
        m = os.path.join(self.tmp, 'm.json')
        release.save(m, manifest)
        out = os.path.join(self.tmp, 'prev.db')
        return subprocess.run([sys.executable, '-I', os.path.join(BUILDER, 'release.py'), 'fetch-prev',
                               '--manifest', m, '--out', out], capture_output=True, text=True), out

    def manifest(self, **over):
        gz = os.path.join(self.tmp, 'v1.db.gz')
        with open(self.v1, 'rb') as src, gzip.open(gz, 'wb') as dst:
            shutil.copyfileobj(src, dst)
        size, sha1 = release.file_info(gz)
        db_size, db_sha1 = release.file_info(self.v1)
        pack = dict(url='file://' + gz, size=size, sha1=sha1, db_size=db_size, db_sha1=db_sha1, dump='20260101-000000')
        pack.update(over)
        return {'format': 3, 'latest': '20260101-000000', 'pack': pack, 'deltas': []}

    def test_ok(self):
        r, out = self.run_fetch(self.manifest())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(release.file_info(out), release.file_info(self.v1))

    def test_bad_hash_refused(self):
        r, _ = self.run_fetch(self.manifest(sha1='0' * 40))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('SHA-1', r.stderr)

    def test_wrong_version_refused(self):
        m = self.manifest()
        m['latest'] = '20991231-000000'
        r, _ = self.run_fetch(m)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('dump', r.stderr)


class TestManifest(unittest.TestCase):
    P = {'url': 'u', 'size': 1, 'sha1': 'a', 'db_size': 2, 'db_sha1': 'b'}

    def d(self, frm, to):
        return dict(self.P, **{'from': frm, 'to': to})

    def test_chain(self):
        m = release.add_release(None, 3, 'A', self.P, None)
        self.assertEqual((m['latest'], m['deltas']), ('A', []))
        m = release.add_release(m, 3, 'B', self.P, self.d('A', 'B'))
        m = release.add_release(m, 3, 'C', self.P, self.d('B', 'C'))
        self.assertEqual([(x['from'], x['to']) for x in m['deltas']], [('A', 'B'), ('B', 'C')])
        self.assertEqual(m['pack']['dump'], 'C')

    def test_broken_chain_drops_old_deltas(self):
        m = release.add_release(None, 3, 'A', self.P, None)
        m = release.add_release(m, 3, 'B', self.P, self.d('A', 'B'))
        m = release.add_release(m, 3, 'D', self.P, self.d('C', 'D'))
        self.assertEqual([(x['from'], x['to']) for x in m['deltas']], [('C', 'D')])

    def test_format_change_starts_over(self):
        m = release.add_release(None, 3, 'A', self.P, None)
        m = release.add_release(m, 3, 'B', self.P, self.d('A', 'B'))
        m = release.add_release(m, 4, 'C', self.P, None)
        self.assertEqual((m['format'], m['deltas']), (4, []))

    def test_prune(self):
        tags = [f'pack-2026010{i}-000000' for i in range(1, 10)] + ['other']
        kept, gone = release.prune_plan(tags, 8)
        self.assertEqual(len(kept), 8)
        self.assertEqual(gone, ['pack-20260101-000000'])
        m = {'deltas': [self.d('20260101-000000', '20260102-000000'), self.d('20260100-000000', '20260101-000000')]}
        self.assertEqual(len(release.prune_manifest(m, kept)['deltas']), 1)

    def test_stale_full_packs(self):
        rel = [{'tag': f'pack-{i}', 'assets': [f'pack-{i}.db.gz', f'delta-x-{i}.db.gz']} for i in ('A', 'B', 'C')]
        self.assertEqual(release.stale_full_packs(rel, 2), [('pack-A', 'pack-A.db.gz')])

    def test_check_prints_latest(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'm.json')
            out = subprocess.run([sys.executable, '-I', os.path.join(BUILDER, 'release.py'), 'check', '--manifest', p],
                                 capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(out, '')
            release.save(p, release.add_release(None, 3, 'A', self.P, None))
            out = subprocess.run([sys.executable, '-I', os.path.join(BUILDER, 'release.py'), 'check', '--manifest', p],
                                 capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(out, 'A')
            with open(p) as f:
                self.assertIn('updated', json.load(f))


if __name__ == '__main__':
    unittest.main()
