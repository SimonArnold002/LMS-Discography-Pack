"""A report on one build's keyed file and its release parts, for the build's test run (which publishes nothing): the
file's size and the rows a load writes; each table's bucket count, buckets, pages, values' bytes, average and largest
bucket; the parts (how many, their bytes, the largest) and the manifest's bytes; each meta row's bytes; the records
more.py made, by kind; and the disk free after each of the build's commands.

  stat    <more.db>                                         more.py's counts, one "key<TAB>value" a line
  report  <keyed.db> <manifest.json> <df.txt> <stat.txt>    the report, to stdout
"""
import json, os, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keyed, measure_extra  # noqa: E402


def mb(n):
    return f'{n / 1e6:,.1f} MB'


def stat(more_db):
    c = sqlite3.connect(f'file:{more_db}?mode=ro', uri=True)
    rows = c.execute('SELECT key, value FROM stat ORDER BY n').fetchall()
    c.close()
    return ''.join(f'{k}\t{v}\n' for k, v in rows)


def report(keyed_db, manifest, df_txt, stat_txt):
    c = sqlite3.connect(f'file:{keyed_db}?mode=ro', uri=True)
    meta = dict(c.execute('SELECT key, value FROM meta'))
    kinds = keyed.kinds_of(meta.get('file_format', keyed.FILE_FORMAT))
    by_kind = json.loads(meta['bits_by_kind']) if 'bits_by_kind' in meta else {k: int(meta['bits']) for k in kinds}
    pages = measure_extra.btrees(c)
    rows = {k: c.execute(f'SELECT count(*), coalesce(sum(length(v)), 0), coalesce(max(length(v)), 0) FROM {k}').fetchone()
            for k in kinds}
    c.close()
    with open(manifest, encoding='utf-8') as f:
        man = json.load(f)
    parts = man['parts']
    total = sum(r[0] for r in rows.values())
    L = [f"The build's file: dump {meta.get('dump', '?')}, {meta.get('file_format', '?')}.", '',
         f'  file                       {mb(os.path.getsize(keyed_db)):>12}',
         f'  rows a load writes         {total:>12,}',
         f'  parts                      {len(parts):>12,}',
         f"  parts' bytes               {mb(sum(p['bytes'] for p in parts)):>12}",
         f"  largest part               {mb(max((p['bytes'] for p in parts), default=0)):>12}",
         f'  manifest                   {os.path.getsize(manifest):>12,} bytes',
         '', 'TABLES' + ('' if pages else ' (no dbstat on this SQLite: pages not counted)'),
         f"{'table':6} {'bits':>4} {'buckets':>8} {'pages':>12} {'values':>12} {'average':>10} {'largest':>10}"]
    for k in kinds:
        n, size, top = rows[k]
        L.append(f"{k:6} {by_kind[k]:>4} {n:>8,} {mb(pages.get(k, 0)) if pages else '?':>12} {mb(size):>12} "
                 f"{(size / n / 1e3 if n else 0):>7,.1f} kB {top / 1e3:>7,.1f} kB")
    L += ['', 'META (bytes a row)']
    for k, v in sorted(meta.items(), key=lambda kv: (-len(kv[1].encode('utf-8')), kv[0])):
        L.append(f"  {k:24} {len(v.encode('utf-8')):>10,}")
    L += ['', 'RECORDS MADE (more.py)']
    with open(stat_txt, encoding='utf-8') as f:
        for line in f:
            k, v = line.rstrip('\n').split('\t', 1)
            L.append(f'  {k:52} {int(v):>14,}' if v.lstrip('-').isdigit() else f'  {k}: {v}')
    L += ['', 'DISK']
    with open(df_txt, encoding='utf-8') as f:
        L += ['  ' + line.rstrip('\n') for line in f]
    return '\n'.join(L) + '\n'


def main():
    a = sys.argv[1:]
    if a[:1] == ['stat'] and len(a) == 2:
        sys.stdout.write(stat(a[1]))
    elif a[:1] == ['report'] and len(a) == 5:
        sys.stdout.write(report(*a[1:]))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
