"""A made-up MusicBrainz dump of a few rows per table, in two versions, laid out
as fetch_dump.py leaves the real one (<dir>/mbdump/<table>, the three version
files beside them). Column counts and positions are the real dump's (checked
against the 2026-10-14 dump's first rows). Nothing here is real data except
the special-purpose Various Artists gid.

Version 2 differs from 1 by: one new member-of-band link (Sigur Ros -> The
Sugarcubes), one new alias, a renamed artist (Some Duo -> Some Duo!), one
artist url gone, and a new release group (Post) for Bjork.

Two releases of Bjork's group Debut carry credits of their own: one names Kukl
too (the release map keeps it: Kukl's page could not place it otherwise), one
only spells Bjork another way (left out: Bjork's own list places it).
"""
import os

N = '\\N'
TS = '2026-01-01 00:00:00+00'
VA = '89ad4ac3-39f7-470e-963a-56509c546377'


def g(n):
    return f'00000000-0000-4000-8000-{n:012d}'


def write(d, table, rows):
    with open(os.path.join(d, table), 'w', encoding='utf-8', newline='\n') as f:
        for r in rows:
            f.write('\t'.join(N if v is None else str(v) for v in r) + '\n')


def make_dump(root, version=1):
    d = os.path.join(root, 'mbdump')
    os.makedirs(d, exist_ok=True)
    v2 = version >= 2
    lt = lambda i, e0, e1, name: [i, N, 0, g(900 + i), e0, e1, name, '', '', '', '', TS, 'f', 't', 0, 0]
    write(d, 'link_type', [lt(1, 'artist', 'artist', 'member of band'),
                           lt(2, 'artist', 'artist', 'collaboration'),
                           lt(3, 'artist', 'url', 'discogs'),
                           lt(4, 'release_group', 'url', 'discogs'),
                           lt(5, 'release_group', 'url', 'wikipedia')])
    write(d, 'link_attribute_type', [[1, N, 1, 0, g(950), 'eponymous', 'x', TS],
                                     [2, N, 2, 0, g(951), 'guitar', 'x', TS]])
    ln = lambda i, t, by=None, ey=None, ended='f': [i, t, by, None, None, ey, None, None, 0, TS, ended]
    links = [ln(10, 1, by=1990), ln(11, 1), ln(12, 1, by=1985, ey=1988, ended='t'), ln(13, 2),
             ln(14, 3), ln(15, 4), ln(16, 5)]
    if v2:
        links.append(ln(17, 1))
    write(d, 'link', links)
    write(d, 'link_attribute', [[10, 1, TS]])
    l = lambda i, link, a, b, order=0: [i, link, a, b, 0, TS, order, '', '']
    aa = [l(100, 10, 1, 2), l(101, 12, 1, 3), l(102, 13, 1, 4), l(104, 11, 1, 7)]
    if v2:
        aa.append(l(103, 17, 6, 2))
    write(d, 'l_artist_artist', aa)
    write(d, 'l_artist_url', [] if v2 else [l(200, 14, 1, 300)])
    write(d, 'l_release_group_url', [l(400, 15, 1000, 301), l(401, 16, 1000, 302)])
    write(d, 'url', [[300, g(300), 'https://www.discogs.com/artist/12345', 0, TS],
                     [301, g(301), 'https://www.discogs.com/master/999', 0, TS],
                     [302, g(302), 'https://en.wikipedia.org/wiki/Debut', 0, TS]])
    al = lambda i, a, name, loc, prim: [i, a, name, loc, 0, TS, 1, name, N, N, N, N, N, N, prim, 'f']
    aliases = [al(1, 1, 'Bjork', 'en', 't')]
    if v2:
        aliases.append(al(2, 6, 'Sigur Ros', 'en', 'f'))
    write(d, 'artist_alias', aliases)
    ar = lambda i, gid, name, typ=None, area=None, cmt='': [i, gid, name, name, N, N, N, N, N, N, typ, area, N, cmt, 0, TS, 'f', N, N]
    write(d, 'artist', [ar(1, g(1), 'Björk', 1, 50, 'Icelandic singer'),
                        ar(2, g(2), 'The Sugarcubes', 2),
                        ar(3, g(3), 'Kukl', 2),
                        ar(4, g(4), 'Some Duo!' if v2 else 'Some Duo'),
                        ar(5, VA, 'Various Artists'),
                        ar(6, g(6), 'Sigur Rós', 2),
                        ar(7, g(7), 'The Elgar Sisters', 2),
                        ar(8, g(8), 'Unlinked Nobody')])
    acn = [[500, 0, 1, 'Björk', ''], [501, 0, 1, 'Björk', ' & '], [501, 1, 6, 'Sigur Rós', ''],
           [502, 0, 5, 'Various Artists', ''],
           [503, 0, 1, 'Björk', ' & '], [503, 1, 3, 'Kukl', ''],
           [504, 0, 1, 'Bjork', '']]
    write(d, 'artist_credit_name', acn)
    rg = lambda i, name, ac, typ=1: [i, g(1000 + i), name, ac, typ, '', 0, TS]
    groups = [rg(1000, 'Debut', 500), rg(1001, 'Joint', 501), rg(1002, 'Hits', 502), rg(1003, 'Bootleg Live', 500)]
    if v2:
        groups.append(rg(1004, 'Post', 500))
    write(d, 'release_group', groups)
    re_ = lambda i, name, rgid, st, ac=500: [i, g(2000 + i), name, ac, rgid, st, N, N, N, N, '', 0, -1, TS]
    write(d, 'release', [re_(2000, 'Debut (Deluxe)', 1000, 1), re_(2001, 'Joint', 1001, 1, 501),
                         re_(2002, 'Hits', 1002, 1, 502), re_(2003, 'Bootleg Live', 1003, 3),
                         re_(2004, 'Debut', 1000, 1, 503), re_(2005, 'Debut', 1000, 1, 504)])
    write(d, 'release_status', [[1, 'Official', N, 1, 'x', g(970)], [3, 'Bootleg', N, 3, 'x', g(971)]])
    write(d, 'release_group_alias', [[1, 1000, 'Début', N, 0, TS, N, 'Début', N, N, N, N, N, N, 'f', 'f']])
    write(d, 'release_group_secondary_type_join', [[1002, 1, TS]])
    write(d, 'iso_3166_1', [[50, 'IS']])
    write(d, 'artist_gid_redirect', [[g(99), 1, TS]])
    write(d, 'artist_type', [[1, 'Person', N, 1, 'x', g(980)], [2, 'Group', N, 2, 'x', g(981)]])
    write(d, 'release_group_primary_type', [[1, 'Album', N, 1, 'x', g(990)]])
    write(d, 'release_group_secondary_type', [[1, 'Compilation', N, 0, 'x', g(991)]])
    for f, v in (('TIMESTAMP', f'2026-01-0{version} 00:00:00.000000+00'),
                 ('SCHEMA_SEQUENCE', '31'), ('REPLICATION_SEQUENCE', str(100 + version))):
        with open(os.path.join(d, f), 'w') as fh:
            fh.write(v + '\n')
    return root
