"""THE BUCKET REBUILD TEST: a keyed update sent as the ENTRIES that changed, each
changed bucket rebuilt by the Worker in D1 (LMS-Discography
docs/hosted-pack-plan.md §11, end).

Why: a keyed update re-sends every changed bucket whole. For 20261028 ->
20261101 at 16,384 buckets a kind that is 53,851 buckets, 476.9 MB gzipped (run
41350885542), against 1.9 MB for the tables layout. Simon, 2026-11-01: "we should
look to test for that next", "as long as it doesnt run us over any usage limits
in our subscription". Here the builder sends only the entries that changed; the
Worker reads each changed bucket, puts and deletes those entries, and writes the
bucket back (rebuild/patch.mjs).

Each bucket is checked by its CANONICAL text: its entries in key order (code
points, which is the order of their UTF-8 bytes), '{' + ','.join(json key + ':' +
value) + '}', hashed with SHA-256. keyed() writes a bucket in hash order with
Python's deflate; the Worker writes key order with its own. The bytes differ, the
entries do not, and the reader looks an entry up by its key. The Worker gets each
value's text by JSON.parse then JSON.stringify: every one of the 20261028 file's
17,747,916 values comes back byte for byte (Node 26, 2026-11-02), and a value that
did not would show as a hash mismatch, never as a silent change.

Sub-commands:
  make <entries A> <narrow A> <entries B> <narrow B> <bits> <out dir>
      edelta-<bits>.jsonl.gz, in (kind, bucket) order, the meta line first:
        {"t": "meta", "put": [[key, value]...], "del": [key...]}
        {"t": kind, "b": bucket, "put": [[key, value]...], "del": [key...],
         "ha": hash before | null (a new bucket), "hb": hash after | null (emptied),
         "zb": the bucket's bytes as keyed() writes it | null}
      a value is its JSON as stored (not a string of it);
      ahash-<bits>.tsv.gz, bhash-<bits>.tsv.gz: kind, bucket, hash of every bucket
      of A and of B (and "meta 0 <hash>");
      edelta-<bits>.json: the counts.
      Kept only once PROVEN: A's entries + the delta, read back from the file,
      give every bucket of B and B's meta.
  hashes <keyed file> <out tsv.gz>
      the same hashes read from a KEYED file (the file loaded into D1), so it can
      be held to a run's ahash list.
  same <hash list> <hash list>
      SAME, or the differences (meta lines ignored); a plain .tsv is Node's
      (rebuild/apply_node.mjs), a .tsv.gz this module's. Exits 1 on a difference.
  parts <edelta .jsonl.gz> <out dir> <lines a part> <dump A> <dump B>
      the delta split for a Worker that applies it by itself, one part a scheduled
      run (rebuild/updater.mjs): shuffled gzipped parts and edelta-<B>.json, the
      manifest (dumps, the meta line, each part's lines, bytes, SHA-256).
  report <edelta-<bits>.json> [<rebuild/apply_node.mjs's JSON>]
      the readable report.
"""
import gzip
import hashlib
import json
import os
import sqlite3
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hosted  # noqa: E402
from hosted import KINDS, jdump, log  # noqa: E402


def canon(pairs):
    """A bucket's canonical text from (key, value text) pairs. Python orders
    strings by code point, which is the UTF-8 bytes' order."""
    return '{' + ','.join(jdump(k) + ':' + v for k, v in sorted(pairs)) + '}'


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def meta_digest(meta):
    return digest(jdump(sorted(meta.items())))


def keyed_meta(narrow_db, bits):
    """The meta table keyed() writes for this narrow file and bucket size (its
    own lines, repeated here so hosted.py stays untouched; the suite holds the two
    equal)."""
    nar = sqlite3.connect(f'file:{narrow_db}?mode=ro', uri=True)
    meta = dict(nar.execute('SELECT key, value FROM meta'))
    names = {t: dict(nar.execute(f'SELECT id, name FROM {t}')) for t in ('artist_type', 'rg_type', 'rg_sec')}
    probe = dict(nar.execute('SELECT name, key FROM namekey_probe'))
    nar.close()
    meta.update({'hosted_layout': 'keyed', 'hosted_bits': str(bits),
                 'names': jdump({t: {str(i): v for i, v in d.items()} for t, d in names.items()}),
                 'namekey_probe': jdump(probe)})
    return meta


def buckets(entries_db, bits):
    """Every non-empty bucket of an entries file: ((kind index, bucket), kind,
    bucket, [(key, value text)] in keyed()'s order), kind by kind, bucket order."""
    if bits < 1:
        raise SystemExit('bits must be 1 or more (0 is one row per entry: nothing to rebuild)')
    c = sqlite3.connect(f'file:{entries_db}?mode=ro', uri=True)
    shift = 63 - bits
    try:
        for ki, kind in enumerate(KINDS):
            b, parts = None, []
            for h, k, v in c.execute('SELECT h, k, v FROM e WHERE kind = ? ORDER BY h, k', (kind,)):
                x = h >> shift
                if x != b and parts:
                    yield (ki, b), kind, b, parts
                    parts = []
                b = x
                parts.append((k, v))
            if parts:
                yield (ki, b), kind, b, parts
    finally:
        c.close()


def _pairs(xs, ys):
    """Two streams ordered by their first field, merged: (key, x or None, y or None)."""
    x, y = next(xs, None), next(ys, None)
    while x is not None or y is not None:
        if y is None or (x is not None and x[0] < y[0]):
            yield x[0], x, None
            x = next(xs, None)
        elif x is None or y[0] < x[0]:
            yield y[0], None, y
            y = next(ys, None)
        else:
            yield x[0], x, y
            x, y = next(xs, None), next(ys, None)


def _zsize(parts):
    """The bucket's bytes as keyed() writes them (its order, its deflate)."""
    return len(hosted._deflate(('{' + ','.join(jdump(k) + ':' + v for k, v in parts) + '}').encode('utf-8')))


def make(ent_a, nar_a, ent_b, nar_b, bits, outdir):
    os.makedirs(outdir, exist_ok=True)
    dpath = os.path.join(outdir, f'edelta-{bits}.jsonl.gz')
    apath = os.path.join(outdir, f'ahash-{bits}.tsv.gz')
    bpath = os.path.join(outdir, f'bhash-{bits}.tsv.gz')
    ma, mb = keyed_meta(nar_a, bits), keyed_meta(nar_b, bits)
    cnt = {k: {'buckets_a': 0, 'buckets_b': 0, 'changed': 0, 'new': 0, 'emptied': 0, 'put_new': 0,
               'put_changed': 0, 'del': 0, 'plain_bytes': 0, 'zb_bytes': 0} for k in KINDS}
    plain = 0
    with gzip.open(dpath, 'wt', encoding='utf-8', compresslevel=9) as f, \
            gzip.open(apath, 'wt', encoding='utf-8') as fa, gzip.open(bpath, 'wt', encoding='utf-8') as fb:
        meta = jdump({'t': 'meta', 'put': sorted([k, v] for k, v in mb.items() if ma.get(k) != v),
                      'del': sorted(k for k in ma if k not in mb)})
        f.write(meta + '\n')
        plain += len(meta.encode('utf-8')) + 1
        fa.write(f'meta\t0\t{meta_digest(ma)}\n')
        fb.write(f'meta\t0\t{meta_digest(mb)}\n')
        for _, a, b in _pairs(buckets(ent_a, bits), buckets(ent_b, bits)):
            kind, bucket = (a or b)[1], (a or b)[2]
            c = cnt[kind]
            ha = hb = None
            if a:
                ha = digest(canon(a[3]))
                fa.write(f'{kind}\t{bucket}\t{ha}\n')
                c['buckets_a'] += 1
            if b:
                hb = digest(canon(b[3]))
                fb.write(f'{kind}\t{bucket}\t{hb}\n')
                c['buckets_b'] += 1
            if ha == hb:
                continue
            da, db = dict(a[3]) if a else {}, dict(b[3]) if b else {}
            puts = sorted((k, v) for k, v in db.items() if da.get(k) != v)
            dels = sorted(k for k in da if k not in db)
            zb = _zsize(b[3]) if b else None
            line = ('{"t":' + jdump(kind) + ',"b":' + str(bucket) + ',"put":['
                    + ','.join('[' + jdump(k) + ',' + v + ']' for k, v in puts)
                    + '],"del":' + jdump(dels) + ',"ha":' + jdump(ha) + ',"hb":' + jdump(hb)
                    + ',"zb":' + jdump(zb) + '}')
            f.write(line + '\n')
            n = len(line.encode('utf-8')) + 1
            plain += n
            c['changed'] += 1
            c['new'] += a is None
            c['emptied'] += b is None
            c['put_new'] += sum(1 for k, _ in puts if k not in da)
            c['put_changed'] += sum(1 for k, _ in puts if k in da)
            c['del'] += len(dels)
            c['plain_bytes'] += n
            c['zb_bytes'] += zb or 0
    log(f'entry delta at bits {bits}: {sum(x["changed"] for x in cnt.values()):,} buckets changed; proving it')
    proof = prove(ent_a, ma, mb, dpath, bpath, bits)
    res = {'bits': bits, 'bytes_plain': plain, 'bytes_gz': os.path.getsize(dpath), 'kinds': cnt,
           'buckets_changed': sum(x['changed'] for x in cnt.values()),
           'meta_put': len(json.loads(meta)['put']), 'meta_del': len(json.loads(meta)['del']), 'proof': proof}
    with open(os.path.join(outdir, f'edelta-{bits}.json'), 'w') as f:
        json.dump(res, f, indent=1)
    return res


def _lines(dpath):
    with gzip.open(dpath, 'rt', encoding='utf-8') as f:
        for line in f:
            yield json.loads(line)


def read_hashes(path):
    out = {}
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for line in f:
            kind, b, h = line.rstrip('\n').split('\t')
            out[(kind, int(b))] = h
    return out


def prove(ent_a, meta_a, meta_b, dpath, bpath, bits):
    """A's entries + the delta READ BACK FROM ITS FILE == B: every bucket's hash
    against B's list (written from B's own entries), every changed bucket's
    before and after hashes, and the meta. Raises on the first difference."""
    want = read_hashes(bpath)
    lines = _lines(dpath)
    m = next(lines)
    if m.get('t') != 'meta':
        raise SystemExit('proof: the delta does not start with its meta line')
    meta = dict(meta_a)
    for k in m['del']:
        del meta[k]
    meta.update(dict(m['put']))
    if meta_digest(meta) != want.pop(('meta', 0)) or meta != meta_b:
        raise SystemExit('proof: the meta differs')
    order = {k: i for i, k in enumerate(KINDS)}
    keyed_lines = (((order[x['t']], x['b']), x) for x in lines)
    n = {'buckets': 0, 'changed': 0}
    for key, a, line in _pairs(buckets(ent_a, bits), keyed_lines):
        kind, bucket = (a[1], a[2]) if a else (line[1]['t'], line[1]['b'])
        cur = dict(a[3]) if a else {}
        if line:
            x = line[1]
            if (digest(canon(cur.items())) if cur else None) != x['ha']:
                raise SystemExit(f'proof: {kind} {bucket} is not the bucket the line was made from')
            for k in x['del']:
                del cur[k]
            for k, v in x['put']:
                cur[k] = jdump(v)
            got = digest(canon(cur.items())) if cur else None
            if got != x['hb']:
                raise SystemExit(f'proof: {kind} {bucket} rebuilt is not the line\'s after')
            n['changed'] += 1
        got = digest(canon(cur.items())) if cur else None
        if got != want.pop((kind, bucket), None):
            raise SystemExit(f'proof: {kind} {bucket} is not B\'s')
        n['buckets'] += got is not None
    if want:
        raise SystemExit(f'proof: {len(want)} bucket(s) of B never made, e.g. {next(iter(want))}')
    return n


def keyed_hashes(keyed_db, out):
    """The ahash/bhash list of a KEYED file: each bucket inflated, its entries
    in their JSON order (Python keeps it), their values dumped again."""
    c = sqlite3.connect(f'file:{keyed_db}?mode=ro', uri=True)
    n = 0
    with gzip.open(out, 'wt', encoding='utf-8') as f:
        f.write(f'meta\t0\t{meta_digest(dict(c.execute("SELECT key, value FROM meta")))}\n')
        for kind in KINDS:
            for k, v in c.execute(f'SELECT k, v FROM {kind} ORDER BY k'):
                d = json.loads(zlib.decompress(v, -15).decode('utf-8'))
                f.write(f'{kind}\t{int.from_bytes(k, "big")}\t{digest(canon((x, jdump(y)) for x, y in d.items()))}\n')
                n += 1
    c.close()
    return n


def parts(dpath, outdir, per_part, dump_a, dump_b, seed=1):
    """The delta as a Worker applies it BY ITSELF (rebuild/updater.mjs, one part a scheduled run): the bucket
    lines shuffled (seed fixed: lst lines are the big ones, so each part then costs about the same) into gzipped
    parts of per_part lines, and a manifest: the dumps it goes from and to, the meta line
    (applied last, so the database names the new dump only once every bucket is in), and each part's name,
    lines, bytes and SHA-256. Returns the manifest."""
    import random
    os.makedirs(outdir, exist_ok=True)
    with gzip.open(dpath, 'rt', encoding='utf-8') as f:
        raw = [x.rstrip('\n') for x in f if x.strip()]     # kept as written: never dumped again
    meta = json.loads(raw.pop(0))
    if meta.get('t') != 'meta':
        raise SystemExit('the delta does not start with its meta line')
    lines = raw
    random.Random(seed).shuffle(lines)
    man = {'format': 'edelta-1', 'from': dump_a, 'to': dump_b, 'buckets': len(lines),
           'meta': {'put': meta['put'], 'del': meta['del']}, 'parts': []}
    for i in range(0, len(lines), per_part):
        name = f'edelta-{dump_b}-{len(man["parts"]) + 1:03d}.jsonl.gz'
        path = os.path.join(outdir, name)
        chunk = lines[i:i + per_part]
        # mtime 0: the same lines give the same bytes
        with open(path, 'wb') as fh, gzip.GzipFile(fileobj=fh, mode='wb', compresslevel=9, mtime=0) as f:
            f.write(''.join(x + '\n' for x in chunk).encode('utf-8'))
        with open(path, 'rb') as f:
            data = f.read()
        man['parts'].append({'name': name, 'lines': len(chunk), 'bytes': len(data),
                             'sha256': hashlib.sha256(data).hexdigest()})
    with open(os.path.join(outdir, f'edelta-{dump_b}.json'), 'w') as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    return man


def _mb(n):
    return f'{n / 1e6:,.1f} MB'


def report(res, applied=None):
    """The readable report of make()'s counts and, when given, Node's rebuild
    of A's keyed file (rebuild/apply_node.mjs's JSON)."""
    k = res['kinds']
    tot = lambda f: sum(x[f] for x in k.values())
    out = [f'--- the update as changed entries, {2 ** res["bits"]:,} buckets a kind (builder/entry_delta.py make)',
           f'buckets changed {res["buckets_changed"]:,} of {tot("buckets_b"):,} in B '
           f'(new {tot("new"):,}, emptied {tot("emptied"):,}); meta keys put {res["meta_put"]}, deleted {res["meta_del"]}',
           f'entries: new {tot("put_new"):,}, changed {tot("put_changed"):,}, deleted {tot("del"):,}',
           f'the delta: {_mb(res["bytes_plain"])} plain, {_mb(res["bytes_gz"])} gzipped; '
           f'the changed buckets as keyed() writes them: {_mb(tot("zb_bytes"))}',
           f'PROVEN: A + the delta read back from its file == B, all {res["proof"]["buckets"]:,} buckets '
           f'({res["proof"]["changed"]:,} rebuilt) and the meta',
           '',
           'kind  buckets changed   of B      entries new  changed  deleted   delta plain   keyed bytes of them']
    for kind in KINDS:
        x = k[kind]
        out.append(f'{kind:4} {x["changed"]:>16,} {x["buckets_b"]:>7,} {x["put_new"]:>16,} {x["put_changed"]:>8,} '
                   f'{x["del"]:>8,} {_mb(x["plain_bytes"]):>13} {_mb(x["zb_bytes"]):>19}')
    if applied:
        out += ['', '--- Node rebuilding a copy of A\'s keyed file with rebuild/patch.mjs, as the Worker will',
                f'lines {applied["lines"]:,}: written {applied["written"]:,}, deleted {applied["deleted"]:,}, '
                f'already done {applied["already"]:,}, REFUSED {len(applied["bad"]):,}'
                + (f' e.g. {applied["bad"][:3]}' if applied['bad'] else ''),
                f'every bucket of the result hashed against B\'s list: {applied.get("same", "not compared")}',
                f'bytes read {_mb(applied["bytes_in"])}, written {_mb(applied["bytes_out"])} '
                f'(Node\'s deflate; keyed() writes the same buckets in {_mb(applied["zb"])})',
                f'Node\'s CPU for it {applied["cpu_ms"] / 1000:,.1f} s, wall {applied["wall_ms"] / 1000:,.1f} s '
                f'(node:sqlite reads and writes included; a preview, never the Worker\'s figure)']
    return '\n'.join(out)


def main():
    a = sys.argv[1:]
    if a[:1] == ['make'] and len(a) == 7:
        print(json.dumps(make(a[1], a[2], a[3], a[4], int(a[5]), a[6]), indent=1))
    elif a[:1] == ['hashes'] and len(a) == 3:
        print(keyed_hashes(a[1], a[2]))
    elif a[:1] == ['same'] and len(a) == 3:
        # A hash list (Node's, no meta line) against a run's list: prints SAME or the first differences.
        x, y = read_hashes(a[1]) if a[1].endswith('.gz') else _plain(a[1]), read_hashes(a[2])
        y.pop(('meta', 0), None)
        x.pop(('meta', 0), None)
        bad = sorted(set(x.items()) ^ set(y.items()))
        print('SAME' if not bad else f'DIFFERENT: {len(bad)} entries, e.g. {bad[:4]}')
        sys.exit(1 if bad else 0)
    elif a[:1] == ['parts'] and len(a) == 6:
        m = parts(a[1], a[2], int(a[3]), a[4], a[5])
        print(json.dumps({k: v for k, v in m.items() if k != 'meta'}, indent=1))
    elif a[:1] == ['report'] and len(a) in (2, 3):
        res = json.load(open(a[1]))
        print(report(res, json.load(open(a[2])) if len(a) == 3 else None))
    else:
        raise SystemExit(__doc__)


def _plain(path):
    out = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                kind, b, h = line.rstrip('\n').split('\t')
                out[(kind, int(b))] = h
    return out


if __name__ == '__main__':
    main()
