"""builder/load_parts.py and rebuild/loader.mjs on the made-up dump: the spare-copy load (LMS-Discography
docs/hosted-pack-plan.md §11, end; Simon 2026-11-02: two copies only during an update, the old one serving until the
new one is complete, then switched over and the old one emptied). Holds: the parts carry every row of the keyed file
once, byte for byte, with their sizes and hashes; the loader fills the copy that does not serve, the serving copy
untouched and still the one a reader takes until the meta goes in last; the copy that served before is emptied only
EMPTY_AFTER later; a run cut off, a damaged part, a held lease, a dump not newer, a table or part short of its rows, a
switch cut off before its last step, and a newer dump arriving while the old copy waits to be emptied. The JS checks
need Node 22.5+ and are skipped without it."""
import json, os, shutil, sqlite3, struct, subprocess, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'builder'))
sys.path.insert(0, HERE)
import hosted, load_parts  # noqa: E402
from test_entry_delta import EntryDelta, BITS  # noqa: E402
from test_hosted import NODE, q  # noqa: E402

REBUILD = os.path.join(HERE, '..', 'rebuild')
EMPTY_AFTER = 5 * 60 * 1000          # the loader's default; the driver steps 60 s a run


def rows(db):
    """{kind: {key: value}} and the meta, read from a copy (missing tables: empty)."""
    c = sqlite3.connect(db)
    have = {t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    out = {k: dict(c.execute(f'SELECT k, v FROM {k}')) for k in hosted.KINDS if k in have}
    meta = dict(c.execute('SELECT key, value FROM meta')) if 'meta' in have else None
    c.close()
    return out, meta, have


def records(path):
    with open(path, 'rb') as f:
        b = f.read()
    out, o = [], 0
    while o < len(b):
        (n,) = struct.unpack('>I', b[o + 5:o + 9])
        out.append((hosted.KINDS[b[o]], b[o + 1:o + 5], b[o + 9:o + 9 + n]))
        o += 9 + n
    return out


class Load(EntryDelta):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dumps = [dict(q(cls.key[v], 'SELECT key, value FROM meta'))['dump'] for v in (1, 2)]
        total = sum(9 + len(v) for kind in hosted.KINDS for _, v in q(cls.key[2], f"SELECT k, v FROM {kind}"))
        cls.part_bytes = total // 5 + 1
        cls.rel = {v: os.path.join(cls.tmp, f'load-rel-{v}') for v in (1, 2)}
        cls.man = {v: cls.quiet(cls, load_parts.parts, cls.key[v], cls.rel[v], cls.part_bytes) for v in (1, 2)}


class TestLoadParts(Load):
    def test_parts_hold_every_row_once_with_their_sizes_and_hashes(self):
        man, rel = self.man[2], self.rel[2]
        self.assertGreaterEqual(len(man['parts']), 4)
        got = {k: {} for k in hosted.KINDS}
        order = []
        for p in man['parts']:
            path = os.path.join(rel, p['name'])
            with open(path, 'rb') as f:
                data = f.read()
            self.assertEqual((p['bytes'], p['sha256']), (len(data), __import__('hashlib').sha256(data).hexdigest()))
            rs = records(path)
            self.assertEqual(p['records'], len(rs))
            for kind, k, v in rs:
                self.assertNotIn(k, got[kind])
                got[kind][k] = v
                order.append((hosted.KINDS.index(kind), k))
        want, meta, _ = rows(self.key[2])
        self.assertEqual(got, want)                                   # every row once, byte for byte
        self.assertEqual(order, sorted(order))                        # kinds in order, keys in order
        self.assertEqual(man['tables'], {k: len(v) for k, v in want.items()})
        self.assertEqual(dict(man['meta']), meta)
        self.assertEqual((man['format'], man['dump'], man['kinds']), ('load-1', self.dumps[1], list(hosted.KINDS)))
        with open(os.path.join(rel, f'load-{self.dumps[1]}.json')) as f:
            self.assertEqual(json.load(f), man)
        for p in man['parts'][:-1]:                                   # cut once a part reaches its size
            self.assertGreaterEqual(p['bytes'], self.part_bytes)

    def test_the_same_file_gives_the_same_bytes(self):
        again = self.quiet(load_parts.parts, self.key[2], self.path('load-again'), self.part_bytes)
        self.assertEqual(again, self.man[2])

    def test_a_file_not_keyed_by_bucket_is_refused(self):
        ent = self.path('entries-b0.db')
        self.quiet(hosted.entries, self.f[2]['hosted'], ent)
        k0 = self.path('keyed-b0.db')
        self.quiet(hosted.keyed, ent, self.f[2]['hosted'], 0, k0)
        with self.assertRaises(SystemExit):
            self.quiet(load_parts.parts, k0, self.path('load-b0'), self.part_bytes)
        short = self.path('keyed-short.db')
        shutil.copyfile(self.key[2], short)
        c = sqlite3.connect(short)
        (k,) = c.execute('SELECT k FROM lst ORDER BY k LIMIT 1').fetchone()
        c.execute('UPDATE lst SET k = ? WHERE k = ?', (k[1:], k))
        c.commit()
        c.close()
        with self.assertRaises(SystemExit):
            self.quiet(load_parts.parts, short, self.path('load-short'), self.part_bytes)
        extra = self.path('keyed-extra.db')
        shutil.copyfile(self.key[2], extra)
        c = sqlite3.connect(extra)
        c.execute('CREATE TABLE other (x)')
        c.commit()
        c.close()
        with self.assertRaises(SystemExit):
            self.quiet(load_parts.parts, extra, self.path('load-extra'), self.part_bytes)


@unittest.skipUnless(NODE, 'needs Node 22.5+ (node:sqlite)')
class TestLoader(Load):
    """rebuild/loader.mjs, one scheduled run at a time (rebuild/tick_load_node.mjs), on two database files."""
    def setUp(self):
        self.n = len(self.man[2]['parts'])
        self.runs = -(-self.n // 2)                                   # PARTS_PER_RUN 2

    def ticks(self, db1=None, db2=None, rel=None, mname=None, max_runs=100, files=None, **env):
        if files is None:
            files = []
            for i, src in enumerate((db1, db2), 1):
                p = self.path(f'copy{i}-{len(os.listdir(self.tmp))}.db')
                if src:
                    shutil.copyfile(src, p)
                files.append(p)
        p = subprocess.run([NODE, os.path.join(REBUILD, 'tick_load_node.mjs'), *files, rel or self.rel[2],
                            mname or f'load-{self.dumps[1]}.json', str(max_runs)],
                           capture_output=True, text=True, env=dict(os.environ, **{k: str(v) for k, v in env.items()}))
        if p.returncode:
            self.fail(p.stderr)
        return [json.loads(x) for x in p.stdout.splitlines()], files

    def assertCopyIs(self, db, v):
        got, meta, _ = rows(db)
        want, wmeta, _ = rows(self.key[v])
        self.assertEqual(got, want)
        self.assertEqual(meta, wmeta)

    def assertEmptied(self, db):
        got, meta, have = rows(db)
        self.assertEqual((got, meta), ({}, None))
        self.assertTrue(have <= {'upd'}, have)

    def test_a_first_load_fills_db1_and_serves_once_complete(self):
        rs, (d1, d2) = self.ticks()
        self.assertEqual([r['state'] for r in rs], ['parts'] * (self.runs - 1) + ['switched', 'current'])
        self.assertEqual([r['reads'] for r in rs], [None] * (self.runs - 1) + ['DB1', 'DB1'])
        self.assertEqual((rs[-2]['copy'], rs[-2]['from'], rs[-2]['rows']), ('DB1', None, sum(self.man[2]['tables'].values())))
        self.assertCopyIs(d1, 2)
        self.assertEqual(rows(d2)[2], set())                         # DB2 never touched
        self.assertEqual(q(d1, 'SELECT k, v FROM upd ORDER BY k'), [('lease', '0')])

    def test_a_newer_dump_loads_the_spare_switches_then_empties_the_old_copy(self):
        rs, (d1, d2) = self.ticks(self.key[1])
        states = [r['state'] for r in rs]
        sw = states.index('switched')
        self.assertEqual(states[:sw], ['parts'] * (self.runs - 1))
        self.assertEqual([r.get('target') for r in rs[:sw]], ['DB2'] * (self.runs - 1))
        self.assertEqual([r['reads'] for r in rs[:sw]], ['DB1'] * (self.runs - 1))   # the old copy serves throughout
        self.assertEqual((rs[sw]['copy'], rs[sw]['from'], rs[sw]['reads']), ('DB2', 'DB1', 'DB2'))
        em = states.index('emptied')
        self.assertEqual(states[sw + 1:em], ['current'] * (em - sw - 1))
        self.assertTrue(all(r['empty_in'] > 0 for r in rs[sw + 1:em]))
        self.assertEqual((em - sw) * 60_000, EMPTY_AFTER)            # emptied EMPTY_AFTER after the switch, not sooner
        self.assertEqual((rs[em]['copy'], rs[em]['serving']), ('DB1', 'DB2'))
        self.assertEqual(states[em + 1:], ['current'])
        self.assertNotIn('empty_in', rs[-1])
        self.assertCopyIs(d2, 2)
        self.assertEmptied(d1)
        self.assertEqual(q(d1, 'SELECT k, v FROM upd ORDER BY k'), [('lease', '0')])

    def test_until_the_meta_the_serving_copy_is_untouched(self):
        rs, (d1, d2) = self.ticks(self.key[1], max_runs=self.runs - 1)
        self.assertEqual([r['state'] for r in rs], ['parts'] * (self.runs - 1))
        self.assertCopyIs(d1, 1)
        got, meta, _ = rows(d2)
        self.assertFalse(meta)
        self.assertEqual(sum(map(len, got.values())), rs[-1]['rows'])

    def test_a_run_cut_off_part_way_is_done_again(self):
        rs, (d1, d2) = self.ticks(self.key[1], FAIL_DB='DB2', FAIL_BATCH=2)
        self.assertEqual(rs[0]['state'], 'threw')
        self.assertEqual(q(d1, "SELECT v FROM upd WHERE k = 'lease'"), [('0',)])
        self.assertEqual((rs[1]['state'], rs[1]['part']), ('parts', 3))    # part 1 was in before the cut, 2 is redone
        self.assertIn('switched', [r['state'] for r in rs])
        self.assertCopyIs(d2, 2)

    def test_a_damaged_part_is_fetched_again_and_nothing_of_it_is_written(self):
        rel = self.path('load-rel-damaged')
        shutil.copytree(self.rel[2], rel)
        p2 = os.path.join(rel, self.man[2]['parts'][1]['name'])
        with open(p2, 'rb') as f:
            good = f.read()
        with open(p2, 'wb') as f:
            f.write(good[:-1] + bytes([good[-1] ^ 1]))
        rs, files = self.ticks(self.key[1], rel=rel, max_runs=2)
        self.assertEqual([(r['state'], r['part']) for r in rs], [('part damaged', 2), ('part damaged', 2)])
        got, meta, _ = rows(files[1])
        self.assertEqual((sum(map(len, got.values())), meta), (self.man[2]["parts"][0]["records"], {}))
        with open(p2, 'wb') as f:
            f.write(good)
        rs2, _ = self.ticks(rel=rel, files=files)
        self.assertIn('switched', [r['state'] for r in rs2])
        self.assertCopyIs(files[1], 2)

    def test_a_held_lease_does_nothing(self):
        rs, (d1, d2) = self.ticks(self.key[1], LEASE_HELD=1)
        self.assertEqual([r['state'] for r in rs], ['busy'])
        self.assertEqual(rows(d2)[2], set())
        self.assertCopyIs(d1, 1)

    def test_a_dump_not_newer_than_the_serving_one_loads_nothing(self):
        for rel, mname in ((self.rel[2], f'load-{self.dumps[1]}.json'), (self.rel[1], f'load-{self.dumps[0]}.json')):
            rs, (d1, d2) = self.ticks(self.key[2], rel=rel, mname=mname)
            self.assertEqual([(r['state'], r['reads']) for r in rs], [('current', 'DB1')])
            self.assertEqual(rows(d2)[2], set())

    def stops(self, man_change, expect):
        rel = self.path(f'load-rel-{expect.split()[0]}')
        shutil.copytree(self.rel[2], rel)
        mpath = os.path.join(rel, f'load-{self.dumps[1]}.json')
        with open(mpath) as f:
            man = json.load(f)
        man_change(man)
        with open(mpath, 'w') as f:
            json.dump(man, f)
        rs, files = self.ticks(self.key[1], rel=rel)
        self.assertEqual(rs[-1]['state'], 'stopped')
        self.assertIn(expect, rs[-1]['error'])
        self.assertNotIn('switched', [r['state'] for r in rs])
        self.assertFalse(rows(files[1])[1])                           # the meta never went in
        self.assertEqual(rs[-1]['reads'], 'DB1')
        rs2, _ = self.ticks(rel=rel, files=files)
        self.assertEqual([r['state'] for r in rs2], ['stopped'])      # it stays stopped,
        self.assertEqual(rs2[0]['part'], rs[-1]['part'])
        self.assertNotIn('queries', rs2[0])                           # without trying anything again

    def test_a_table_short_of_its_rows_stops_before_the_meta(self):
        self.stops(lambda m: m['tables'].__setitem__('grp', m['tables']['grp'] + 1), 'grp holds')

    def test_a_part_short_of_its_records_stops(self):
        self.stops(lambda m: m['parts'][1].__setitem__('records', m['parts'][1]['records'] + 1), 'part 2:')

    def test_a_bad_manifest_loads_nothing(self):
        for change in (lambda m: m.__setitem__('format', 'load-2'),
                       lambda m: m.__setitem__('kinds', m['kinds'][:-1] + ['other']),
                       lambda m: m.__setitem__('meta', [x for x in m['meta'] if x[0] != 'dump'] + [['dump', '20250101-000000']])):
            rel = self.path(f'load-rel-bad-{len(os.listdir(self.tmp))}')
            shutil.copytree(self.rel[2], rel)
            mpath = os.path.join(rel, f'load-{self.dumps[1]}.json')
            with open(mpath) as f:
                man = json.load(f)
            change(man)
            with open(mpath, 'w') as f:
                json.dump(man, f)
            rs, (d1, d2) = self.ticks(self.key[1], rel=rel)
            self.assertEqual([(r['state'], r['reads']) for r in rs], [('bad manifest', 'DB1')])
            self.assertEqual(rows(d2)[2], set())

    def test_a_switch_cut_off_before_its_last_step_is_finished_by_the_next_run(self):
        d1, d2 = self.path('cut-switch-1.db'), self.path('cut-switch-2.db')
        shutil.copyfile(self.key[1], d1)
        shutil.copyfile(self.key[2], d2)                              # the meta went in; DB1's rows did not follow
        c = sqlite3.connect(d1)
        c.execute('CREATE TABLE upd (k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID')
        c.execute("INSERT INTO upd VALUES ('run', ?)", (json.dumps({'dump': self.dumps[1], 'target': 'DB2', 'from': 'DB1',
                                                                    'part': self.n, 'rows': 1}),))
        c.commit()
        c.close()
        rs, _ = self.ticks(files=[d1, d2])
        self.assertEqual((rs[0]['state'], rs[0]['reads'], rs[0]['empty_in']), ('current', 'DB2', EMPTY_AFTER))
        self.assertEqual([r['state'] for r in rs][-2:], ['emptied', 'current'])
        self.assertEmptied(d1)
        self.assertCopyIs(d2, 2)

    def test_a_load_given_up_is_emptied(self):
        rs, files = self.ticks(self.key[1], max_runs=1)               # part of B in DB2, then the release goes back
        self.assertEqual([(r['state'], r['target']) for r in rs], [('parts', 'DB2')])
        rs2, _ = self.ticks(rel=self.rel[1], mname=f'load-{self.dumps[0]}.json', files=files)
        self.assertEqual((rs2[0]['state'], rs2[0]['reads'], rs2[0]['empty_in']), ('current', 'DB1', EMPTY_AFTER))
        self.assertEqual([r['state'] for r in rs2][-2:], ['emptied', 'current'])
        self.assertEqual(rs2[-2]['copy'], 'DB2')
        self.assertEmptied(files[1])
        self.assertCopyIs(files[0], 1)
        self.assertEqual(q(files[0], 'SELECT k, v FROM upd ORDER BY k'), [('lease', '0')])

    def test_a_newer_dump_while_the_old_copy_waits_to_be_emptied(self):
        rs, files = self.ticks(self.key[1], max_runs=self.runs + 1)
        self.assertEqual([r['state'] for r in rs][-2:], ['switched', 'current'])   # DB2 serves, DB1 waits
        c3 = self.path('keyed-3.db')
        shutil.copyfile(self.key[2], c3)
        c = sqlite3.connect(c3)
        c.execute("UPDATE meta SET value = '29991231-000000' WHERE key = 'dump'")
        c.commit()
        c.close()
        rel3 = self.path('load-rel-3')
        self.quiet(load_parts.parts, c3, rel3, self.part_bytes)
        rs2, _ = self.ticks(rel=rel3, mname='load-29991231-000000.json', files=files)
        states = [r['state'] for r in rs2]
        self.assertEqual(states[:self.runs], ['parts'] * (self.runs - 1) + ['switched'])
        self.assertEqual([r.get('target') for r in rs2[:self.runs - 1]], ['DB1'] * (self.runs - 1))
        self.assertEqual([r['reads'] for r in rs2[:self.runs - 1]], ['DB2'] * (self.runs - 1))
        self.assertEqual((rs2[self.runs - 1]['copy'], rs2[self.runs - 1]['from']), ('DB1', 'DB2'))
        self.assertEqual(states[-2:], ['emptied', 'current'])
        self.assertEqual(rs2[-2]['copy'], 'DB2')
        self.assertEmptied(files[1])
        got, meta, _ = rows(files[0])
        self.assertEqual((got, meta['dump']), (rows(self.key[2])[0], '29991231-000000'))


if __name__ == '__main__':
    unittest.main()
