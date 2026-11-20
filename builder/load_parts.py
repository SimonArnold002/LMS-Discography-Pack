"""A whole KEYED file (keyed.py keyed) cut into parts of a set size, with a manifest, for a release.

Sub-commands:
  parts <keyed file> <out dir> <MB a part>
      load-<dump>-NNN.bin: binary records [1-byte kind index][4-byte key][4-byte length][value], the kinds in
      the file's order (keyed.kinds_of its file_format) and each kind's rows in key order, a part cut at a record once it reaches its size (the same
      file gives the same bytes);
      load-<dump>.json, the manifest: format, dump, the kinds (their index order), every kind's rows, the meta
      rows and each part's name, records, bytes, SHA-256.
      Refuses, before writing anything, a file whose keys are not 4-byte bucket numbers (bits 0, or not bytes at
      all), whose values are not bytes, or that holds tables other than the meta and its format's kinds, and an out
      dir that already holds load parts (a stale part must never travel with a new manifest).
"""
import hashlib
import json
import os
import sqlite3
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keyed import FILE_FORMAT, kinds_of  # noqa: E402


def parts(keyed_db, outdir, part_bytes):
    """Writes the parts and the manifest; returns the manifest."""
    # First, as it reads nothing: the checks below scan every table of the keyed file (~1 GB on the real pack).
    if os.path.isdir(outdir) and any(f.startswith('load-') for f in os.listdir(outdir)):
        raise SystemExit(f'{outdir} already holds load parts: give a fresh folder')
    c = sqlite3.connect(f'file:{keyed_db}?mode=ro', uri=True)
    tables = {t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite%'")}
    if 'meta' not in tables:
        raise SystemExit(f'not a keyed file: tables {sorted(tables)}')
    meta = sorted(c.execute('SELECT key, value FROM meta'))
    KINDS = kinds_of(dict(meta).get('file_format', FILE_FORMAT))
    if tables != {'meta', *KINDS}:
        raise SystemExit(f'not a keyed file: tables {sorted(tables)}')
    dump = dict(meta).get('dump')
    if not dump:
        raise SystemExit('the file names no dump')
    for kind in KINDS:
        (n,) = c.execute(f"SELECT count(*) FROM {kind} WHERE typeof(k) != 'blob' OR length(k) != 4 OR typeof(v) != 'blob'").fetchone()
        if n:
            raise SystemExit(f'{kind}: {n} row(s) not a 4-byte key and a value of bytes; a part holds 4-byte bucket numbers')
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
