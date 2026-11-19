"""builder/keyed.py and builder/load_parts.py on the made-up dump (tests/fixture.py plus test_measure_extra's extra
tables): what the tables file holds, that the keyed file holds the same records and is the same bytes for the same
records, and that the parts carry every row once with a manifest that says so. Sizes of a few rows prove nothing.

Needs perl and an LMS-Discography checkout ($DSC_DIR, else ../LMS-Discography) for the name keys.
Run: python3 -m unittest discover -s tests
"""
import contextlib, hashlib, io, json, os, shutil, sqlite3, struct, sys, tempfile, unittest, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'builder'))
sys.path.insert(0, HERE)
import fixture, keyed, load_parts, measure_extra  # noqa: E402
from test_measure_extra import add_candidates  # noqa: E402
from test_pack import DSC, build  # noqa: E402

N, TS = fixture.N, fixture.TS
BITS = 3


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


def rows(db):
    """{kind: {key: value}} and the meta, read from a copy (missing tables: empty)."""
    c = sqlite3.connect(db)
    have = {t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    out = {k: dict(c.execute(f'SELECT k, v FROM {k}')) for k in keyed.KINDS if k in have}
    meta = dict(c.execute('SELECT key, value FROM meta')) if 'meta' in have else None
    c.close()
    return out, meta, have


def records(path):
    with open(path, 'rb') as f:
        b = f.read()
    out, o = [], 0
    while o < len(b):
        (n,) = struct.unpack('>I', b[o + 5:o + 9])
        out.append((keyed.KINDS[b[o]], b[o + 1:o + 5], b[o + 9:o + 9 + n]))
        o += 9 + n
    return out


class Keyed(unittest.TestCase):
    """Both versions of the made-up dump built all the way: the tables file, the records, the keyed file, the parts."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.f, cls.ent, cls.key = {}, {}, {}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for v, dump in ((1, '20260101-000000'), (2, '20260102-000000')):
                src = add_extras(add_candidates(fixture.make_dump(os.path.join(cls.tmp, f'd{v}'), v)), v)
                base = build(src, os.path.join(cls.tmp, f'base{v}.db'), dump)
                facts = os.path.join(cls.tmp, f'facts{v}.db')
                measure_extra.facts(src, facts)
                al = os.path.join(cls.tmp, f'aliases{v}.tsv')
                keyed.aliases(base, DSC, al)
                out = os.path.join(cls.tmp, f'tables-{v}.db')
                keyed.narrow(base, facts, al, out)
                cls.f[v] = {'base': base, 'facts': facts, 'aliases': al, 'tables': out}
                cls.ent[v] = os.path.join(cls.tmp, f'entries-{v}.db')
                keyed.entries(out, cls.ent[v])
                cls.key[v] = os.path.join(cls.tmp, f'keyed-{v}.db')
                keyed.keyed(cls.ent[v], out, BITS, cls.key[v])
            cls.dumps = [dict(q(cls.key[v], 'SELECT key, value FROM meta'))['dump'] for v in (1, 2)]
            total = sum(9 + len(v) for kind in keyed.KINDS for _, v in q(cls.key[2], f'SELECT k, v FROM {kind}'))
            cls.part_bytes = total // 5 + 1
            cls.rel = {v: os.path.join(cls.tmp, f'load-rel-{v}') for v in (1, 2)}
            cls.man = {v: load_parts.parts(cls.key[v], cls.rel[v], cls.part_bytes) for v in (1, 2)}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def quiet(self, fn, *a, **k):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fn(*a, **k)

    def entry(self, v, kind, k):
        r = q(self.ent[v], 'SELECT v FROM e WHERE kind = ? AND k = ?', kind, k)
        return json.loads(r[0][0]) if r else None


class TestTables(Keyed):
    def test_it_is_the_pack_plus_the_extras(self):
        base = tables(self.f[1]['base'])
        self.assertEqual(tables(self.f[1]['tables']) - base,
                         {'rg', 'credit', 'artist_rg', 'barcode', 'svc_album', 'alias_name'})
        self.assertEqual(base - tables(self.f[1]['tables']), set())
        meta = dict(q(self.f[1]['tables'], 'SELECT key, value FROM meta'))
        self.assertEqual((meta['layout'], meta['file_format']), ('narrow', 'data-1'))

    def test_todays_tables_are_unchanged_but_the_release_map_is_whole(self):
        for t in sorted(tables(self.f[1]['base']) - {'meta', 'sqlite_stat1', 'release'}):
            self.assertEqual(q(self.f[1]['tables'], f'SELECT * FROM {t} ORDER BY 1, 2'),
                             q(self.f[1]['base'], f'SELECT * FROM {t} ORDER BY 1, 2'), t)
        today = set(q(self.f[1]['base'], 'SELECT * FROM release'))
        whole = set(q(self.f[1]['tables'], 'SELECT * FROM release'))
        self.assertLess(len(today), 10)
        self.assertEqual(len(whole), 10)       # every release of the made-up dump
        self.assertLessEqual(today, whole)
        # The untyped shared group's release is in today's map, type unknown.
        self.assertEqual(q(self.f[1]['tables'], 'SELECT type FROM release WHERE gid = ?',
                           uuid.UUID(fixture.g(4008)).bytes), [(None,)])

    def test_alias_keys_leave_out_the_artists_own_name(self):
        keys = set(q(self.f[1]['tables'], 'SELECT key, artist FROM alias_name'))
        self.assertIn(('sykurmolarnir', 2), keys)
        self.assertIn(('bjork gudmundsdottir', 1), keys)
        # Björk's alias "Bjork" keys as her own name does: the name keys answer it.
        self.assertNotIn(('bjork', 1), keys)
        self.assertIn(('kukl collective', 3), set(q(self.f[2]['tables'], 'SELECT key, artist FROM alias_name')))
        tsv = {(k, a): n for k, a, n in keyed.read_aliases(self.f[1]['aliases'])}
        self.assertEqual(set(tsv), keys)
        self.assertEqual(tsv['sykurmolarnir', 2], 'Sykurmolarnir')    # the alias's own text, for MusicBrainz


class TestKeyed(Keyed):
    def test_every_kind_one_entry_per_key(self):
        h = self.f[2]['tables']
        self.assertEqual({k for (k,) in q(self.ent[2], 'SELECT DISTINCT kind FROM e')}, set(keyed.KINDS))
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
        # Her links: the band named after her carries a 6th field, 1, and no other link has one; the band's own
        # document carries it on its link back to her.
        hx = lambda n: fixture.g(n).replace('-', '')
        fwd = {r[3]: r for r in bjork[6] if r[0] == 0}
        self.assertEqual(fwd[hx(7)], [0, 1, 0, hx(7), 'Björk & The Elgar Sisters', 1])
        self.assertEqual(fwd[hx(2)], [0, 1, 1, hx(2), 'The Sugarcubes'])
        self.assertEqual({len(r) for g, r in fwd.items() if g != hx(7)}, {5})
        back = [r for r in self.entry(2, 'art', hx(7))[6] if r[3] == hx(1)]
        self.assertEqual(back, [[1, 1, 0, hx(1), 'Björk', 1]])
        # A merged-away gid points at the current artist.
        self.assertEqual(self.entry(2, 'art', fixture.g(99).replace('-', '')), fixture.g(1).replace('-', ''))

    def test_the_list_and_alias_documents_are_the_narrow_answers(self):
        h = self.f[2]['tables']
        for (a,) in q(h, 'SELECT DISTINCT a.gid FROM artist_rg ar JOIN artist a ON a.id = ar.artist'):
            want = [[g.hex(), t, d, ty, sc, cv, cr] for g, t, d, ty, sc, cv, cr in q(
                h, 'SELECT g.gid, g.title, g.date, g.type, g.sec, g.cover, c.name FROM artist_rg ar JOIN artist x '
                   'ON x.id = ar.artist JOIN rg g ON g.id = ar.rg LEFT JOIN credit c ON c.id = g.credit '
                   'WHERE x.gid = ? ORDER BY g.id', a)]
            got = self.entry(2, 'lst', a.hex())
            self.assertEqual([x[:7] for x in got], want)
            # The 8th field: the group's extras, exactly its grp entry; none when it has none.
            for x in got:
                self.assertEqual(x[7] if len(x) > 7 else None, self.entry(2, 'grp', x[0]), x[1])
        bj = self.entry(2, 'lst', fixture.g(1).replace('-', ''))
        self.assertEqual([x[1] for x in bj], ['Debut', 'Joint', 'Bootleg Live', 'Post', 'Untyped'])
        self.assertTrue(any(len(x) > 7 for x in bj) and any(len(x) == 7 for x in bj))   # some with extras, some not
        # "Kukl Group": the Kukl with a group first.
        self.assertEqual([x[0] for x in self.entry(2, 'ali', 'kukl group')],
                         [fixture.g(9).replace('-', ''), fixture.g(3).replace('-', '')])

    def test_keyed_files_are_deterministic_and_complete(self):
        for bits in (0, 1, 3):
            a, b = self.path(f'k{bits}a.db'), self.path(f'k{bits}b.db')
            self.quiet(keyed.keyed, self.ent[1], self.f[1]['tables'], bits, a)
            self.quiet(keyed.keyed, self.ent[1], self.f[1]['tables'], bits, b)
            with open(a, 'rb') as x, open(b, 'rb') as y:
                self.assertEqual(x.read(), y.read(), f'bits {bits}')
            n_entries = q(self.ent[1], 'SELECT count(*) FROM e')[0][0]
            rows = sum(q(a, f'SELECT count(*) FROM {k}')[0][0] for k in keyed.KINDS)
            if bits == 0:
                self.assertEqual(rows, n_entries)
            else:
                self.assertLessEqual(rows, len(keyed.KINDS) * 2 ** bits)
                self.assertLess(rows, n_entries)          # buckets hold several records
            meta = dict(q(a, 'SELECT key, value FROM meta'))
            self.assertEqual((meta['layout'], meta['bits']), ('keyed', str(bits)))
            self.assertEqual(q(a, "SELECT count(*) FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")[0][0], 0)


class TestParts(Keyed):
    def test_parts_hold_every_row_once_with_their_sizes_and_hashes(self):
        man, rel = self.man[2], self.rel[2]
        self.assertGreaterEqual(len(man['parts']), 4)
        got = {k: {} for k in keyed.KINDS}
        order = []
        for p in man['parts']:
            path = os.path.join(rel, p['name'])
            with open(path, 'rb') as f:
                data = f.read()
            self.assertEqual((p['bytes'], p['sha256']), (len(data), hashlib.sha256(data).hexdigest()))
            rs = records(path)
            self.assertEqual(p['records'], len(rs))
            for kind, k, v in rs:
                self.assertNotIn(k, got[kind])
                got[kind][k] = v
                order.append((keyed.KINDS.index(kind), k))
        want, meta, _ = rows(self.key[2])
        self.assertEqual(got, want)                                   # every row once, byte for byte
        self.assertEqual(order, sorted(order))                        # kinds in order, keys in order
        self.assertEqual(man['tables'], {k: len(v) for k, v in want.items()})
        self.assertEqual(dict(man['meta']), meta)
        self.assertEqual((man['format'], man['dump'], man['kinds']), ('load-1', self.dumps[1], list(keyed.KINDS)))
        with open(os.path.join(rel, f'load-{self.dumps[1]}.json')) as f:
            self.assertEqual(json.load(f), man)
        for p in man['parts'][:-1]:                                   # cut once a part reaches its size
            self.assertGreaterEqual(p['bytes'], self.part_bytes)

    def test_a_refusal_writes_nothing_and_a_used_folder_is_refused(self):
        # A key that is not 4 bytes in the LAST kind: refused before any part is written.
        bad = self.path('keyed-badsvc.db')
        shutil.copyfile(self.key[2], bad)
        c = sqlite3.connect(bad)
        (k,) = c.execute('SELECT k FROM svc ORDER BY k LIMIT 1').fetchone()
        c.execute('UPDATE svc SET k = ? WHERE k = ?', (k + b'x', k))
        c.commit()
        c.close()
        out = self.path('load-badsvc')
        with self.assertRaises(SystemExit):
            self.quiet(load_parts.parts, bad, out, self.part_bytes)
        self.assertEqual(os.listdir(out) if os.path.isdir(out) else [], [])
        # Likewise a TEXT key of 4 characters, and a TEXT value (a record holds bytes).
        for sql, name in (("UPDATE svc SET k = 'abcd' WHERE k = ?", 'textkey'), ("UPDATE svc SET v = 'x' WHERE k = ?", 'textval')):
            bad = self.path(f'keyed-{name}.db')
            shutil.copyfile(self.key[2], bad)
            c = sqlite3.connect(bad)
            c.execute(sql, (k,))
            self.assertEqual(c.total_changes, 1)
            c.commit()
            c.close()
            out = self.path(f'load-{name}')
            with self.assertRaises(SystemExit):
                self.quiet(load_parts.parts, bad, out, self.part_bytes)
            self.assertEqual(os.listdir(out) if os.path.isdir(out) else [], [], name)
        # A folder already holding parts: refused, so no stale part can travel with a new manifest.
        used = self.path('load-used')
        self.quiet(load_parts.parts, self.key[2], used, self.part_bytes // 2)
        before = sorted(os.listdir(used))
        with self.assertRaises(SystemExit):
            self.quiet(load_parts.parts, self.key[2], used, self.part_bytes)
        self.assertEqual(sorted(os.listdir(used)), before)
        # Refused before the keyed file is read at all: the file here is not even a database.
        not_db = self.path('not-a-database.db')
        with open(not_db, 'w') as f:
            f.write('x')
        with self.assertRaises(SystemExit) as e:
            self.quiet(load_parts.parts, not_db, used, self.part_bytes)
        self.assertIn('already holds load parts', str(e.exception))

    def test_the_same_file_gives_the_same_bytes(self):
        again = self.quiet(load_parts.parts, self.key[2], self.path('load-again'), self.part_bytes)
        self.assertEqual(again, self.man[2])

    def test_a_file_not_keyed_by_bucket_is_refused(self):
        ent = self.path('entries-b0.db')
        self.quiet(keyed.entries, self.f[2]['tables'], ent)
        k0 = self.path('keyed-b0.db')
        self.quiet(keyed.keyed, ent, self.f[2]['tables'], 0, k0)
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


if __name__ == '__main__':
    unittest.main()
