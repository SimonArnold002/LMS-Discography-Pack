"""Fetch the tables the pack needs from MusicBrainz's newest full dump.

The dump (mbdump.tar.bz2, ~7.6 GB) is STREAMED: downloaded, MD5-hashed and fed
to bzip2 + tar as it arrives, and only the tables the builder reads are written
to disk (~5 GB). The MD5 is checked against the dump's own MD5SUMS once the
stream ends; a mismatch fails the run (nothing is built from a damaged dump).

Usage: python3 -I fetch_dump.py latest            prints the newest dump's name
       python3 -I fetch_dump.py <name> <out dir>   writes <out dir>/mbdump/...
"""
import hashlib, os, shutil, subprocess, sys, urllib.request

BASE = 'https://data.metabrainz.org/pub/musicbrainz/data/fullexport/'
UA = 'LMS-Discography-Pack/1 (https://github.com/SimonArnold002/LMS-Discography-Pack)'
TABLES = [
    'artist', 'artist_alias', 'artist_credit_name', 'artist_gid_redirect', 'artist_type',
    'iso_3166_1', 'l_artist_artist', 'l_artist_url', 'l_release_group_url', 'link',
    'link_attribute', 'link_attribute_type', 'link_type', 'release', 'release_group',
    'release_group_alias', 'release_group_primary_type', 'release_group_secondary_type',
    'release_group_secondary_type_join', 'release_status', 'url',
]
ROOT_FILES = ['TIMESTAMP', 'SCHEMA_SEQUENCE', 'REPLICATION_SEQUENCE']
CHUNK = 1 << 20


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=120)


def latest():
    return get(BASE + 'LATEST').read().decode().strip()


def expected_md5(name):
    for line in get(BASE + name + '/MD5SUMS').read().decode().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip('*') == 'mbdump.tar.bz2':
            return parts[0].lower()
    raise SystemExit('MD5SUMS has no line for mbdump.tar.bz2')


def fetch(name, out):
    want = expected_md5(name)
    os.makedirs(out, exist_ok=True)
    bz = shutil.which('lbzip2') or shutil.which('pbzip2') or 'bzip2'
    # Patterns, so a member stored as "./mbdump/x" matches as well as "mbdump/x";
    # GNU tar anchors a pattern at the start, so "*mbdump/artist" cannot match
    # mbdump/artist_alias. A pattern matching nothing fails tar (exit 2).
    members = ['*' + f for f in ROOT_FILES] + ['*mbdump/' + t for t in TABLES]
    unzip = subprocess.Popen([bz, '-dc'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    untar = subprocess.Popen(['tar', '--wildcards', '-xf', '-', '-C', out] + members, stdin=unzip.stdout)
    unzip.stdout.close()
    md5, n = hashlib.md5(), 0
    with get(BASE + name + '/mbdump.tar.bz2') as r:
        while True:
            buf = r.read(CHUNK)
            if not buf:
                break
            md5.update(buf)
            n += len(buf)
            unzip.stdin.write(buf)
            if n % (512 * CHUNK) < CHUNK:
                print(f'  {n / 1e9:.1f} GB', flush=True)
    unzip.stdin.close()
    if unzip.wait() != 0 or untar.wait() != 0:
        raise SystemExit(f'{bz} or tar failed ({unzip.returncode}, {untar.returncode})')
    got = md5.hexdigest()
    if got != want:
        raise SystemExit(f'mbdump.tar.bz2 MD5 {got}, MD5SUMS says {want}')
    for f in ROOT_FILES:                     # the builder reads them beside the tables
        os.replace(os.path.join(out, f), os.path.join(out, 'mbdump', f))
    missing = [t for t in TABLES if not os.path.exists(os.path.join(out, 'mbdump', t))]
    if missing:
        raise SystemExit(f'missing from the dump: {missing}')
    print(f'{name}: {n / 1e9:.2f} GB fetched, MD5 ok, {len(TABLES)} tables in {out}/mbdump')


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == 'latest':
        print(latest())
    elif len(sys.argv) == 3:
        fetch(sys.argv[1], sys.argv[2])
    else:
        raise SystemExit(__doc__)
