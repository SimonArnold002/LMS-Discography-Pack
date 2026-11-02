"""builder/entry_delta.py and rebuild/patch.mjs on the made-up dump: the keyed
update sent as the ENTRIES that changed, each changed bucket rebuilt where it is
stored (the bucket rebuild test, LMS-Discography docs/hosted-pack-plan.md §11).
Holds: the delta changes exactly the buckets the whole-bucket delta re-sends; it
is proven (A + delta == B, every bucket); Node's rebuild of a keyed A file gives
B's buckets and meta, and a retry writes nothing; a wrong base or a damaged line
is refused, never written; the JS and Python canonical texts agree, key order
included. The JS checks need Node 22.5+ and are skipped without it."""
import contextlib, gzip, io, json, os, shutil, sqlite3, subprocess, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'builder'))
sys.path.insert(0, HERE)
import entry_delta, hosted  # noqa: E402
from test_hosted import Hosted, NODE, q  # noqa: E402

REBUILD = os.path.join(HERE, '..', 'rebuild')
BITS = 3


def lines(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return [json.loads(x) for x in f]


def write_lines(path, xs):
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        for x in xs:
            f.write(json.dumps(x, ensure_ascii=False, separators=(',', ':')) + '\n')


class EntryDelta(Hosted):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ent, cls.key = {}, {}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for v in (1, 2):
                cls.ent[v] = os.path.join(cls.tmp, f'entries-{v}.db')
                hosted.entries(cls.f[v]['hosted'], cls.ent[v])
                cls.key[v] = os.path.join(cls.tmp, f'keyed-{v}.db')
                hosted.keyed(cls.ent[v], cls.f[v]['hosted'], BITS, cls.key[v])
            cls.out = os.path.join(cls.tmp, 'ed')
            cls.res = entry_delta.make(cls.ent[1], cls.f[1]['hosted'], cls.ent[2], cls.f[2]['hosted'], BITS, cls.out)
        cls.delta = os.path.join(cls.out, f'edelta-{BITS}.jsonl.gz')

    def node(self, script, *args):
        return subprocess.run([NODE, os.path.join(REBUILD, script), *args], capture_output=True, text=True)

    def apply(self, base, delta):
        """Node's rebuild of a copy of base: (counts, {(kind, bucket): hash}, meta)."""
        cp = self.path(f'apply-{len(os.listdir(self.tmp))}.db')
        shutil.copyfile(base, cp)
        hl = cp + '.tsv'
        p = self.node('apply_node.mjs', cp, delta, hl)
        if not p.stdout.strip():
            self.fail(p.stderr)
        with open(hl) as f:
            got = {(k, int(b)): h for k, b, h in (x.rstrip('\n').split('\t') for x in f if x.strip())}
        return json.loads(p.stdout), got, dict(q(cp, 'SELECT key, value FROM meta')), p.returncode, cp


class TestMake(EntryDelta):
    def test_meta_is_the_meta_keyed_writes(self):
        for v in (1, 2):
            self.assertEqual(entry_delta.keyed_meta(self.f[v]['hosted'], BITS), dict(q(self.key[v], 'SELECT key, value FROM meta')))

    def test_it_changes_exactly_the_buckets_the_whole_bucket_delta_resends(self):
        whole = self.path('whole.jsonl.gz')
        self.quiet(hosted.delta, self.key[1], self.key[2], whole)
        want = {(x['t'], int.from_bytes(hosted._dec((x.get('p') or x.get('d'))[0]), 'big'))
                for x in lines(whole) if x['t'] in hosted.KINDS}
        got = {(x['t'], x['b']) for x in lines(self.delta)[1:]}
        self.assertTrue(want)
        self.assertEqual(got, want)
        self.assertEqual(self.res['buckets_changed'], len(want))
        # Unchanged buckets exist, so the test sees both kinds.
        self.assertLess(len(want), sum(1 for _ in entry_delta.buckets(self.ent[2], BITS)))

    def test_lines_carry_only_what_changed_and_the_sizes_keyed_writes(self):
        a = {(k, b): dict(p) for _, k, b, p in entry_delta.buckets(self.ent[1], BITS)}
        b = {(k, x): dict(p) for _, k, x, p in entry_delta.buckets(self.ent[2], BITS)}
        for x in lines(self.delta)[1:]:
            old, new = a.get((x['t'], x['b']), {}), b.get((x['t'], x['b']), {})
            self.assertTrue(x['put'] or x['del'])
            self.assertEqual({k for k, _ in x['put']}, {k for k, v in new.items() if old.get(k) != v})
            self.assertEqual(set(x['del']), set(old) - set(new))
            for k, v in x['put']:
                self.assertEqual(hosted.jdump(v), new[k])
            if new:
                row = q(self.key[2], f'SELECT length(v) FROM {x["t"]} WHERE k = ?', x['b'].to_bytes(4, 'big'))
                self.assertEqual(x['zb'], row[0][0])

    def test_hash_lists_match_the_keyed_files(self):
        for v, name in ((1, 'ahash'), (2, 'bhash')):
            kh = self.path(f'kh{v}.tsv.gz')
            entry_delta.keyed_hashes(self.key[v], kh)
            self.assertEqual(entry_delta.read_hashes(kh), entry_delta.read_hashes(os.path.join(self.out, f'{name}-{BITS}.tsv.gz')))

    def test_the_proof_catches_a_damaged_delta(self):
        ma, mb = (entry_delta.keyed_meta(self.f[v]['hosted'], BITS) for v in (1, 2))
        bpath = os.path.join(self.out, f'bhash-{BITS}.tsv.gz')
        good = lines(self.delta)
        self.assertEqual(entry_delta.prove(self.ent[1], ma, mb, self.delta, bpath, BITS)['changed'], len(good) - 1)
        i = next(i for i, x in enumerate(good) if x['t'] != 'meta' and x['put'])
        for name, bad in (
                ('a put dropped', good[:i] + [{**good[i], 'put': good[i]['put'][1:]}] + good[i + 1:]),
                ('a changed bucket left out', good[:i] + good[i + 1:]),
                ('the meta line left out', good[1:]),
                ('a value altered', good[:i] + [{**good[i], 'put': [[good[i]['put'][0][0], 'x']] + good[i]['put'][1:]}] + good[i + 1:]),
                ('a before hash altered', good[:i] + [{**good[i], 'ha': '0' * 64}] + good[i + 1:])):
            p = self.path('bad.jsonl.gz')
            write_lines(p, bad)
            with self.assertRaises(SystemExit, msg=name):
                entry_delta.prove(self.ent[1], ma, mb, p, bpath, BITS)


@unittest.skipUnless(NODE, 'needs Node 22.5+ (node:sqlite)')
class TestRebuild(EntryDelta):
    def test_node_rebuilds_a_into_b(self):
        n, got, meta, rc, _ = self.apply(self.key[1], self.delta)
        self.assertEqual((rc, n['bad'], n['already']), (0, [], 0))
        self.assertEqual(n['written'] + n['deleted'], self.res['buckets_changed'])
        want = entry_delta.read_hashes(os.path.join(self.out, f'bhash-{BITS}.tsv.gz'))
        want.pop(('meta', 0))
        self.assertEqual(got, want)
        self.assertEqual(meta, dict(q(self.key[2], 'SELECT key, value FROM meta')))

    def test_a_retry_writes_nothing(self):
        _, _, _, _, once = self.apply(self.key[1], self.delta)
        n, got, _, rc, _ = self.apply(once, self.delta)
        self.assertEqual((rc, n['written'], n['deleted'], n['already']), (0, 0, 0, self.res['buckets_changed']))

    def test_a_wrong_base_or_a_damaged_line_is_refused_not_written(self):
        good = lines(self.delta)
        i = next(i for i, x in enumerate(good) if x['t'] != 'meta' and x['put'] and x['ha'])
        x = good[i]
        # The bucket is not the one the line was made from: one of A's entries altered first.
        base = self.path('altered.db')
        shutil.copyfile(self.key[1], base)
        c = sqlite3.connect(base)
        (v,) = c.execute(f'SELECT v FROM {x["t"]} WHERE k = ?', (x['b'].to_bytes(4, 'big'),)).fetchone()
        import zlib
        d = json.loads(zlib.decompress(v, -15))
        d[next(iter(d))] = ['altered']
        c.execute(f'UPDATE {x["t"]} SET v = ? WHERE k = ?', (hosted._deflate(hosted.jdump(d).encode()), x['b'].to_bytes(4, 'big')))
        c.commit()
        c.close()
        n, _, _, rc, cp = self.apply(base, self.delta)
        self.assertEqual((rc, n['bad']), (1, [[x['t'], x['b'], 'before']]))
        self.assertEqual(q(cp, f'SELECT v FROM {x["t"]} WHERE k = ?', x['b'].to_bytes(4, 'big'))[0][0],
                         q(base, f'SELECT v FROM {x["t"]} WHERE k = ?', x['b'].to_bytes(4, 'big'))[0][0])
        # A put dropped from a line: its after hash no longer matches.
        p = self.path('dropped.jsonl.gz')
        write_lines(p, good[:i] + [{**x, 'put': x['put'][1:]}] + good[i + 1:])
        n, _, _, rc, cp = self.apply(self.key[1], p)
        self.assertEqual((rc, n['bad']), (1, [[x['t'], x['b'], 'after']]))
        self.assertEqual(q(cp, f'SELECT v FROM {x["t"]} WHERE k = ?', x['b'].to_bytes(4, 'big'))[0][0],
                         q(self.key[1], f'SELECT v FROM {x["t"]} WHERE k = ?', x['b'].to_bytes(4, 'big'))[0][0])

    def test_js_and_python_agree_on_key_order_and_canonical_text(self):
        keys = ['b', 'a', '', 'aa', '', '\U0001F600', 'é', 'Z', '\x7f', '"q"', '__proto__', '10', '9', 'ß']
        pairs = [(k, hosted.jdump([k, 1, None, 'Björk\n '])) for k in keys]
        script = ("import('" + os.path.join(REBUILD, 'patch.mjs') + "').then(async (p) => {"
                  "const pairs = JSON.parse(process.argv[1]); const m = new Map(pairs.map(([k, v]) => [k, v]));"
                  "console.log(JSON.stringify({order: pairs.map((x) => x[0]).sort(p.cmpKey), hash: await p.hashOf(m)}));})")
        r = subprocess.run([NODE, '-e', script, json.dumps(pairs)], capture_output=True, text=True)
        got = json.loads(r.stdout or r.stderr)
        self.assertEqual(got['order'], sorted(keys))
        self.assertEqual(got['hash'], entry_delta.digest(entry_delta.canon(pairs)))


class TestEdges(EntryDelta):
    """What the made-up dump's two versions never do, made on a copy of version 2's entries: a bucket
    that appears (an entry in a bucket A lacks), a bucket that empties (every entry of an A bucket
    gone) and an entry deleted from a bucket that keeps others."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ent3 = os.path.join(cls.tmp, 'entries-3.db')
        shutil.copyfile(cls.ent[2], cls.ent3)
        a = {(k, b): p for _, k, b, p in entry_delta.buckets(cls.ent[1], BITS)}
        two = {(k, b): p for _, k, b, p in entry_delta.buckets(cls.ent[2], BITS)}
        shift = 63 - BITS
        c = sqlite3.connect(cls.ent3)
        # Emptied: an A bucket that B still has, every entry removed.
        cls.emptied = next(kb for kb in sorted(two) if kb in a)
        for k, _ in two[cls.emptied]:
            c.execute('DELETE FROM e WHERE kind = ? AND k = ?', (cls.emptied[0], k))
        # Deleted: one entry of a bucket holding two or more in A and B.
        kb = next(kb for kb in sorted(two) if kb in a and kb != cls.emptied and len(two[kb]) > 1 and len(a[kb]) > 1)
        cls.deleted = (kb, two[kb][0][0])
        c.execute('DELETE FROM e WHERE kind = ? AND k = ?', (kb[0], two[kb][0][0]))
        # New: a barcode-style text key whose bucket A lacks.
        i = 0
        while True:
            k = f'svc:9{i:08d}'
            h = hosted._h_text(k)
            if ('svc', h >> shift) not in a and ('svc', h >> shift) not in two:
                break
            i += 1
        cls.new = (('svc', h >> shift), k)
        c.execute('INSERT INTO e VALUES (?,?,?,?)', ('svc', h, k, hosted.jdump(['0' * 32])))
        c.commit()
        c.close()
        cls.out3 = os.path.join(cls.tmp, 'ed3')
        cls.res3 = cls.quiet(cls, entry_delta.make, cls.ent[1], cls.f[1]['hosted'], cls.ent3, cls.f[2]['hosted'], BITS, cls.out3)
        cls.delta3 = os.path.join(cls.out3, f'edelta-{BITS}.jsonl.gz')

    def line(self, kb):
        return next(x for x in lines(self.delta3)[1:] if (x['t'], x['b']) == kb)

    def test_each_edge_is_in_the_delta_and_proven(self):
        k = self.res3['kinds']
        self.assertGreaterEqual(sum(x['new'] for x in k.values()), 1)
        self.assertGreaterEqual(sum(x['emptied'] for x in k.values()), 1)
        self.assertGreaterEqual(sum(x['del'] for x in k.values()), 1)
        x = self.line(self.emptied)
        self.assertEqual((x['put'], x['hb'], x['zb']), ([], None, None))
        self.assertTrue(x['del'] and x['ha'])
        (kb, key) = self.deleted
        self.assertIn(key, self.line(kb)['del'])
        (kb, key) = self.new
        x = self.line(kb)
        self.assertEqual((x['ha'], x['del'], [p[0] for p in x['put']]), (None, [], [key]))
        self.assertEqual(self.res3['proof']['changed'], self.res3['buckets_changed'])

    def test_the_proof_needs_the_new_buckets_line(self):
        good = lines(self.delta3)
        p = self.path('no-new.jsonl.gz')
        write_lines(p, [x for x in good if (x['t'], x.get('b')) != self.new[0]])
        ma, mb = (entry_delta.keyed_meta(self.f[v]['hosted'], BITS) for v in (1, 2))
        with self.assertRaises(SystemExit):
            entry_delta.prove(self.ent[1], ma, mb, p, os.path.join(self.out3, f'bhash-{BITS}.tsv.gz'), BITS)

    @unittest.skipUnless(NODE, 'needs Node 22.5+ (node:sqlite)')
    def test_node_applies_each_edge(self):
        n, got, _, rc, cp = self.apply(self.key[1], self.delta3)
        self.assertEqual((rc, n['bad']), (0, []))
        self.assertGreaterEqual(n['deleted'], 1)
        want = entry_delta.read_hashes(os.path.join(self.out3, f'bhash-{BITS}.tsv.gz'))
        want.pop(('meta', 0))
        self.assertEqual(got, want)
        key = lambda kb: kb[1].to_bytes(4, 'big')
        self.assertEqual(q(cp, f'SELECT count(*) FROM {self.emptied[0]} WHERE k = ?', key(self.emptied))[0][0], 0)
        self.assertEqual(q(cp, f'SELECT count(*) FROM svc WHERE k = ?', key(self.new[0]))[0][0], 1)


if __name__ == '__main__':
    unittest.main()
