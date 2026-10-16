"""Publishing and pruning, and the manifest the plugin reads.

One GitHub release per dump, tag pack-<dump>, holding:
  pack-<dump>.db.gz           the whole pack (kept on the newest two releases
                              only, see KEEP_FULL)
  delta-<from>-<to>.db.gz     the change from the previous pack (none on the
                              first release, or after a format change)

manifest-v<format>.json at the repo root (committed by the workflow, so it has a
fixed address and the commit keeps the repo's scheduled workflows alive):
  { "format": 3, "updated": "...Z", "latest": "<dump>",
    "pack":   { "dump", "url", "size", "sha1", "db_size", "db_sha1" },
    "deltas": [ { "from", "to", "url", "size", "sha1", "db_size", "db_sha1" }, ... oldest first ] }
size/sha1 are the .gz file's, db_size/db_sha1 the unzipped file's (SHA-1:
what LMS's own installer checks with, Digest::SHA1).

Usage:
  release.py publish --repo O/R --manifest M --dump D --pack P.db.gz --pack-db P.db
                     [--delta X.db.gz --delta-db X.db --from F]
  release.py prune   --repo O/R --manifest M [--keep 8]
  release.py check   --manifest M     (prints the manifest's latest dump, '' if none)
  release.py fetch-prev --manifest M --out prev.db
                     downloads the manifest's whole pack, checks both hashes,
                     unzips it, and checks its meta.dump is the manifest's latest
"""
import argparse, datetime, gzip, hashlib, json, os, shutil, sqlite3, subprocess, sys, urllib.request

KEEP_RELEASES = 8   # Simon, 2026-10-16: about 4 weeks at two dumps a week
KEEP_FULL = 2       # releases keeping their whole pack: the newest, plus the one
                    # before so a manifest cached for a few minutes (raw.github
                    # caches ~5 min) never points at a deleted file


def file_info(path):
    h, n = hashlib.sha1(), 0
    with open(path, 'rb') as f:
        for buf in iter(lambda: f.read(1 << 20), b''):
            h.update(buf)
            n += len(buf)
    return n, h.hexdigest()


def asset_url(repo, tag, name):
    return f'https://github.com/{repo}/releases/download/{tag}/{name}'


def tag_of(dump):
    return f'pack-{dump}'


def load(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def save(path, manifest):
    manifest['updated'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
        f.write('\n')
    os.replace(tmp, path)


def entry(repo, dump, name, gz, db, **extra):
    size, sha1 = file_info(gz)
    db_size, db_sha1 = file_info(db)
    return dict(extra, url=asset_url(repo, tag_of(dump), name), size=size, sha1=sha1,
                db_size=db_size, db_sha1=db_sha1)


def add_release(manifest, fmt, dump, pack, delta):
    """The manifest after publishing `dump`: pack replaced, delta appended. A
    delta whose `from` is not the manifest's latest breaks the chain, so the old
    deltas are dropped (the device then takes the whole pack)."""
    m = dict(manifest or {})
    if m.get('format') not in (None, fmt):
        m = {}
    deltas = list(m.get('deltas') or [])
    if delta:
        if delta['from'] != m.get('latest'):
            deltas = []
        deltas.append(delta)
    else:
        deltas = []
    m.update(format=fmt, latest=dump, pack=dict(pack, dump=dump), deltas=deltas)
    return m


def prune_plan(tags, keep=KEEP_RELEASES):
    """(kept, deleted) pack-* tags, newest first; dump names sort by time."""
    mine = sorted((t for t in tags if t.startswith('pack-')), reverse=True)
    return mine[:keep], mine[keep:]


def prune_manifest(manifest, kept_tags):
    m = dict(manifest)
    keep = set(kept_tags)
    m['deltas'] = [d for d in m.get('deltas') or [] if tag_of(d['to']) in keep]
    return m


def stale_full_packs(releases, keep_full=KEEP_FULL):
    """[(tag, asset name)] whole-pack assets on any release past the newest
    keep_full. `releases` = [{tag, assets: [names]}]."""
    mine = sorted((r for r in releases if r['tag'].startswith('pack-')), key=lambda r: r['tag'], reverse=True)
    out = []
    for r in mine[keep_full:]:
        out += [(r['tag'], a) for a in r['assets'] if a.startswith('pack-') and a.endswith('.db.gz')]
    return out


# --- GitHub (gh, authenticated by the workflow's GITHUB_TOKEN) ------------------

def gh(*args, capture=False):
    r = subprocess.run(['gh'] + list(args), check=True, capture_output=capture, text=True)
    return r.stdout if capture else None


def releases(repo):
    out = gh('api', '--paginate', f'repos/{repo}/releases?per_page=100', '--jq',
             '.[] | {tag: .tag_name, assets: [.assets[].name]}', capture=True)
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def publish(a):
    fmt = int(subprocess.run([sys.executable, '-I', os.path.join(os.path.dirname(__file__), 'build_pack.py'),
                              '--format'], check=True, capture_output=True, text=True).stdout)
    tag = tag_of(a.dump)
    pack_name = f'pack-{a.dump}.db.gz'
    files = [(a.pack, pack_name)]
    delta = None
    if a.delta:
        delta_name = f'delta-{a.from_}-{a.dump}.db.gz'
        files.append((a.delta, delta_name))
        delta = entry(a.repo, a.dump, delta_name, a.delta, a.delta_db, **{'from': a.from_, 'to': a.dump})
    pack = entry(a.repo, a.dump, pack_name, a.pack, a.pack_db)
    for path, name in files:     # an asset is named after its file (gh's "#x" is only a label)
        if os.path.basename(path) != name:
            raise SystemExit(f'{path}: the file must be named {name}')
    notes = (f'MusicBrainz dump {a.dump}, pack format {fmt}.'
             + (f' Delta from {a.from_}.' if delta else ' Whole pack only.'))
    if a.replace and any(r['tag'] == tag for r in releases(a.repo)):
        gh('release', 'delete', tag, '--repo', a.repo, '--cleanup-tag', '--yes')
        print(f'replaced the existing release {tag}')
    gh('release', 'create', tag, '--repo', a.repo, '--title', f'Pack {a.dump}', '--notes', notes,
       *[path for path, _ in files])
    m = add_release(load(a.manifest), fmt, a.dump, pack, delta)
    save(a.manifest, m)
    for t, name in stale_full_packs(releases(a.repo)):
        gh('release', 'delete-asset', t, name, '--repo', a.repo, '--yes')
        print(f'removed {name} from {t}')
    print(f'published {tag}: pack {pack["size"]:,} bytes'
          + (f', delta {delta["size"]:,} bytes from {a.from_}' if delta else ''))


def fetch_prev(a):
    m = load(a.manifest)
    if not m or not m.get('pack'):
        raise SystemExit('the manifest has no pack')
    p = m['pack']
    gz = a.out + '.gz'
    req = urllib.request.Request(p['url'], headers={'User-Agent': 'LMS-Discography-Pack/1'})
    with urllib.request.urlopen(req, timeout=120) as r, open(gz, 'wb') as f:
        shutil.copyfileobj(r, f, 1 << 20)
    if file_info(gz) != (p['size'], p['sha1']):
        raise SystemExit(f'{p["url"]}: size/SHA-1 {file_info(gz)} is not the manifest\'s {(p["size"], p["sha1"])}')
    with gzip.open(gz, 'rb') as src, open(a.out, 'wb') as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    os.remove(gz)
    if file_info(a.out) != (p['db_size'], p['db_sha1']):
        raise SystemExit('the unzipped pack does not match the manifest')
    c = sqlite3.connect(a.out)
    dump = c.execute("SELECT value FROM meta WHERE key = 'dump'").fetchone()
    c.close()
    if not dump or dump[0] != m['latest']:
        raise SystemExit(f'the previous pack says dump {dump}, the manifest {m["latest"]}')
    print(f'previous pack {m["latest"]}: {p["db_size"]:,} bytes, hashes ok')


def prune(a):
    rel = releases(a.repo)
    kept, gone = prune_plan([r['tag'] for r in rel], a.keep)
    for t in gone:
        gh('release', 'delete', t, '--repo', a.repo, '--cleanup-tag', '--yes')
        print(f'deleted release {t}')
    m = load(a.manifest)
    if m:
        save(a.manifest, prune_manifest(m, kept))
    print(f'kept {len(kept)} release(s), deleted {len(gone)}')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('publish')
    for o in ('--repo', '--manifest', '--dump', '--pack', '--pack-db'):
        p.add_argument(o, required=True)
    p.add_argument('--delta')
    p.add_argument('--delta-db')
    p.add_argument('--from', dest='from_')
    p.add_argument('--replace', action='store_true', help='delete an existing release of this dump first')
    p = sub.add_parser('prune')
    p.add_argument('--repo', required=True)
    p.add_argument('--manifest', required=True)
    p.add_argument('--keep', type=int, default=KEEP_RELEASES)
    p = sub.add_parser('check')
    p.add_argument('--manifest', required=True)
    p = sub.add_parser('fetch-prev')
    p.add_argument('--manifest', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--repo')
    a = ap.parse_args()
    if a.cmd == 'publish':
        if bool(a.delta) != bool(a.delta_db) or bool(a.delta) != bool(a.from_):
            ap.error('--delta, --delta-db and --from go together')
        publish(a)
    elif a.cmd == 'prune':
        prune(a)
    elif a.cmd == 'fetch-prev':
        fetch_prev(a)
    else:
        m = load(a.manifest)
        print((m or {}).get('latest', ''))


if __name__ == '__main__':
    main()
