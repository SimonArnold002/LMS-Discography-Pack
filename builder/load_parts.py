"""THE SPARE-COPY LOAD TEST (LMS-Discography docs/hosted-pack-plan.md §11, end). Simon, 2026-11-02: "keep two
copies only during an update, one is updated the other stays as it was and is kept live until completion and then
its switched over", then "yes" to loading the spare WHOLE from the new dump's file (no delta). Two D1 databases are
bound to the Worker; the one serving is the newest COMPLETE copy, and a copy is complete once its meta names a dump,
which the loader writes LAST (rebuild/loader.mjs).

Here a whole KEYED file (hosted.py keyed) is laid out for a Worker that loads it BY ITSELF, a few parts a scheduled
run, from a GitHub release.

Sub-commands:
  parts <keyed file> <out dir> <MB a part>
      load-<dump>-NNN.bin: binary records [1-byte kind index][4-byte key][4-byte length][value], the kinds in
      KINDS order and each kind's rows in key order, a part cut at a record once it reaches its size (the same
      file gives the same bytes);
      load-<dump>.json, the manifest: format, dump, the kinds (their index order), every kind's rows, the meta
      rows (written last by the Worker) and each part's name, records, bytes, SHA-256.
      Refuses a file whose keys are not 4-byte bucket numbers (bits 0) or that holds tables other than the meta
      and the kinds.
"""
import hashlib
import json
import os
import sqlite3
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hosted import KINDS  # noqa: E402


def parts(keyed_db, outdir, part_bytes):
    """Writes the parts and the manifest; returns the manifest."""
    c = sqlite3.connect(f'file:{keyed_db}?mode=ro', uri=True)
    tables = {t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite%'")}
    if tables != {'meta', *KINDS}:
        raise SystemExit(f'not a keyed file: tables {sorted(tables)}')
    meta = sorted(c.execute('SELECT key, value FROM meta'))
    dump = dict(meta).get('dump')
    if not dump:
        raise SystemExit('the file names no dump')
    os.makedirs(outdir, exist_ok=True)
    man = {'format': 'load-1', 'dump': dump, 'kinds': list(KINDS), 'tables': {}, 'meta': [list(x) for x in meta],
           'parts': []}
    buf, n = bytearray(), 0

    def cut():
        nonlocal buf, n
        name = f'load-{dump}-{len(man["parts"]) + 1:03d}.bin'
        with open(os.path.join(outdir, name), 'wb') as f:
            f.write(buf)
        man['parts'].append({'name': name, 'records': n, 'bytes': len(buf), 'sha256': hashlib.sha256(buf).hexdigest()})
        buf, n = bytearray(), 0

    for i, kind in enumerate(KINDS):
        rows = 0
        for k, v in c.execute(f'SELECT k, v FROM {kind} ORDER BY k'):
            if len(k) != 4:
                raise SystemExit(f'{kind}: a key of {len(k)} bytes; the loader takes 4-byte bucket numbers')
            buf += bytes([i]) + k + struct.pack('>I', len(v)) + v
            n += 1
            rows += 1
            if len(buf) >= part_bytes:
                cut()
        man['tables'][kind] = rows
    if buf:
        cut()
    c.close()
    with open(os.path.join(outdir, f'load-{dump}.json'), 'w') as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    return man


def main():
    a = sys.argv[1:]
    if a[:1] == ['parts'] and len(a) == 4:
        m = parts(a[1], a[2], int(float(a[3]) * 1_000_000))
        print(json.dumps({'dump': m['dump'], 'tables': m['tables'], 'parts': len(m['parts']),
                          'bytes': sum(p['bytes'] for p in m['parts'])}, indent=1))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
