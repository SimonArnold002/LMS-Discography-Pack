package Plugins::Discography::Pack;

# THE MUSICBRAINZ PACK (2026-10-16; test build; docs/musicbrainz-pack-plan.md,
# the HYBRID). A read-only SQLite file built from MusicBrainz's free (CC0) dump,
# holding only what Discography still asks MusicBrainz for once ListenBrainz and
# the community API have given a page its first list:
#   * per artist: canonical name, aliases (locale, primary), Qobuz / Deezer /
#     Discogs ids, member-of-band and collaboration links (the `eponymous`
#     flag), and how many release groups it is credited on;
#   * per release group, only when it has one of them: aliases, official
#     edition titles, the 5 release-page links, its Discogs entry, and a
#     bootleg-only verdict;
#   * merged-away artist ids -> the current artist;
#   * FORMAT 2 (Simon, 2026-10-16: "the whole plan is to stop them", the two
#     requests a pack page still sent): every artist's same-name key (DSC's own
#     _nameKey) with disambiguation, type and country, for the same-name list;
#     and every release of a SHARED group (2+ credited artists, or Various
#     Artists and the like) -> its group and the group's types, for an owned
#     release the community's list does not place. From format 3's builder
#     also a release whose own credit names an artist its group's credit does
#     not (Centro-Matic's "South San Gabriel Songs/Music": the release credits
#     Centro-Matic, the group South San Gabriel); same table, more rows.
# No group list (ListenBrainz and the community API keep giving that). Built by
# LMS-Discography-Pack (builder/build_pack.py, on GitHub's runners) and
# downloaded and updated by PackUpdate.pm; read here, never written.
#
# EVERY ANSWER HERE IS IN THE SHAPE OF THE REQUEST IT REPLACES, so the code that
# parses MusicBrainz's reply parses the pack's too (API::_artistReadParsed), and
# every cache it fills is filled the same way. The pack answers undef whenever it
# cannot answer (no file, the pref off, an artist it does not hold, a read that
# failed), and the caller then asks MusicBrainz exactly as before: the worst case
# of anything here is today's speed, never a different page.
#
# WHERE IT LIVES: <cachedir>/DiscographyPack/, the file the pref mb_pack_file
# names (a downloaded pack, pack-<dump>.db, PackUpdate.pm), else pack.db (one
# copied in by hand). Opened read-only, checked for its expected tables and
# format, and nothing else in the file is trusted: no trigger, no view (a
# downloaded database is untrusted input), trusted_schema off. A read error
# closes it for the rest of the run.
#
# FORMAT 3 (2026-10-16, the format updates arrive in; LMS-Discography-Pack
# builds it): artist_rel carries each relation's own sort key `skey` in place of
# format 2's global rank `ord`; meta carries `dump` (the MusicBrainz dump it was
# built from, the version a delta goes from and to); and namekey_probe holds
# names with the keys the builder's _norm gave them. At open this module's
# _nameKey must give the same keys, or the same-name list is not used (a plugin
# and a pack built with different matcher code would key names differently and
# find the wrong acts).
#
# FORMAT 4 (2026-10-24; LMS-Discography CLAUDE.md A2 `THE PACK GROWS IN
# PLACE`): the same rows in a file a device can compact without a VACUUM.
# A delta applied in place splits B-tree pages (31 MB from one 0.57 MB delta,
# measured); only a copy of each table in ONE statement packs them full again,
# and that needs a file with NO secondary index (an index is rebuilt in one
# go). So format 4 has none:
#   * artist_gk / rgx_gk (gk = the first 8 bytes of the gid, id) replace the
#     unique gid indexes: a lookup finds the id by its prefix and checks the
#     FULL gid on the row, so two gids sharing 8 bytes can never be confused;
#   * the TABLE artist_rel_b (b, a, kind) replaces the index on artist_rel(b);
#   * artist_info's comment, type and country are columns of artist.
# Every answer is the same as format 3's for the same dump (proved over every
# artist, group and release by LMS-Discography-Pack's verify run).

use strict;
use warnings;

use DBI;

use Slim::Utils::Log;
use Slim::Utils::Prefs;

my $log   = logger('plugin.discography');
my $prefs = preferences('plugin.discography');

use constant DIR      => 'DiscographyPack';
use constant FILE     => 'pack.db';
use constant IN_CHUNK => 400;     # ids to one IN (...) list

# The file names the pref mb_pack_file may hold: a bare name in DIR, never a
# path (the pref can be set over the CLI).
my $FILE_RE = qr/^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.db$/;

# The tables each format holds; anything else (bar SQLite's own sqlite_*
# tables) and the file is refused. Format 1 (the first test pack) still opens:
# it answers what it holds, and the same-name list and the release map go to
# MusicBrainz as before.
my %FORMAT_TABLES = (
    1 => [ qw(meta artist artist_alias artist_url artist_rel artist_redirect
              rgx rg_alias rg_edition rg_url) ],
    2 => [ qw(meta artist artist_alias artist_url artist_rel artist_redirect
              rgx rg_alias rg_edition rg_url
              artist_name artist_info artist_type release rg_type rg_sec) ],
    3 => [ qw(meta artist artist_alias artist_url artist_rel artist_redirect
              rgx rg_alias rg_edition rg_url
              artist_name artist_info artist_type release rg_type rg_sec
              namekey_probe) ],
    4 => [ qw(meta artist artist_gk artist_alias artist_url artist_rel artist_rel_b
              artist_redirect rgx rgx_gk rg_alias rg_edition rg_url
              artist_name artist_type release rg_type rg_sec
              namekey_probe) ],
);
my %TABLES  = map { ($_ => 1) } map { @$_ } values %FORMAT_TABLES;
my %INDEXES = map { ($_ => 1) } qw(artist_gid rgx_gid artist_rel_b);

my $UUID_RE = qr/^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i;

my ($dbh, $broken, $info, $names);    # $names: format 2's small id -> name tables
my $held;    # PackUpdate is changing the file: no reads until it releases

sub _dbg { Plugins::Discography::Plugin::dbg(@_) }

sub dir {
    my $dir = preferences('server')->get('cachedir') || '/tmp';
    return "$dir/" . DIR;
}

# The pack in use: the downloaded file mb_pack_file names when it is there,
# else the hand-placed pack.db.
sub path {
    my $f = $prefs->get('mb_pack_file') // '';
    my $d = dir();
    return "$d/$f" if length $f && $f =~ $FILE_RE && -f "$d/$f";
    return "$d/" . FILE;
}

# Is $name a file name mb_pack_file may hold? (PackUpdate names its downloads.)
sub validFile { return defined $_[1] && $_[1] =~ $FILE_RE ? 1 : 0 }

sub enabled { $prefs->get('mb_pack') ? 1 : 0 }

# Close the file (the pref turned off, a new pack copied in, a read error).
sub shut {
    my (undef, $why) = @_;
    eval { $dbh->disconnect if $dbh; 1 };
    ($dbh, $info, $names) = (undef, undef, undef);
    $broken = $why if defined $why;
    return;
}

# Forget a read error: the next read opens the file again. A hold stays.
sub reload { __PACKAGE__->shut; $broken = undef; return }

# PackUpdate.pm while it writes or renames the file: closed, and every read
# answers undef (the caller asks MusicBrainz) until release(). Kept apart from
# $broken so a reload (the CLI's, a settings save) cannot reopen the file in
# the middle of a delta's transaction.
sub hold    { my (undef, $why) = @_; __PACKAGE__->shut; $held = $why || 'held'; return }
sub release { $held = undef; __PACKAGE__->reload; return }

sub _dbh {
    return undef unless enabled();
    return undef if $held;
    return undef if $broken;
    return $dbh if $dbh;
    my $path = path();
    return undef unless -f $path;    # not latched: a pack copied in later is used

    my $h = eval {
        my $flags = eval { require DBD::SQLite; DBD::SQLite::OPEN_READONLY() };
        my $c = DBI->connect("dbi:SQLite:dbname=$path", '', '', {
            RaiseError => 1, PrintError => 0, AutoCommit => 1,
            sqlite_unicode => 1,
            (defined $flags ? (sqlite_open_flags => $flags) : ()),
        });
        $c->do('PRAGMA query_only=1');
        eval { $c->do('PRAGMA trusted_schema=OFF') };
        $c;
    };
    unless ($h) {
        $log->warn("dsc: the MusicBrainz pack could not be opened ($path): " . ($@ || '?'));
        $broken = 'open failed';
        return undef;
    }

    my $why = eval {
        my (%t, %i);
        for my $o (@{ $h->selectall_arrayref('SELECT type, name FROM sqlite_master') }) {
            my ($type, $name) = @$o;
            next if $name =~ /^sqlite_/;
            return "a $type ($name)" if $type eq 'trigger' || $type eq 'view';
            return "an unexpected table ($name)" if $type eq 'table' && !$TABLES{$name};
            return "an unexpected index ($name)" if $type eq 'index' && !$INDEXES{$name};
            $t{$name} = 1 if $type eq 'table';
            $i{$name} = 1 if $type eq 'index';
        }
        my %meta = map { @$_ } @{ $h->selectall_arrayref('SELECT key, value FROM meta') };
        my $want = $FORMAT_TABLES{ $meta{format} // '' }
            or return 'format ' . ($meta{format} // '?') . ', expected ' . join(' or ', sort keys %FORMAT_TABLES);
        my @miss = grep { !$t{$_} } @$want;
        return 'missing ' . join(', ', @miss) if @miss;
        my %inFormat = map { ($_ => 1) } @$want;
        my @extra = grep { !$inFormat{$_} } sort keys %t;
        return 'tables of another format: ' . join(', ', @extra) if @extra;
        $info = { path => $path, map { ($_ => $meta{$_}) } keys %meta };
        $info->{bytes} = -s $path;
        if ($meta{format} >= 2) {
            $names = {};
            for my $t (qw(artist_type rg_type rg_sec)) {
                $names->{$t} = { map { @$_ } @{ $h->selectall_arrayref("SELECT id, name FROM $t") } };
            }
        }
        if ($meta{format} >= 3) {
            my $bad = _probeMismatch($h);
            $info->{namekeys} = $bad ? "mismatch: $bad" : 'ok';
            if ($bad) {
                $names->{nokeys} = 1;
                $log->warn("dsc: the MusicBrainz pack's name keys differ from this plugin's ($bad) - "
                         . 'its same-name list is not used, MusicBrainz is asked instead');
            }
        }
        '';
    };
    $why = "unreadable: $@" unless defined $why;
    if (length $why) {
        $log->warn("dsc: the MusicBrainz pack at $path was refused: $why");
        eval { $h->disconnect };
        $broken = $why;
        return undef;
    }
    $log->info("dsc: MusicBrainz pack opened: dump $info->{dump_timestamp}, built $info->{built}");
    return $dbh = $h;
}

# Format 3's namekey_probe: '' when every probe name gets the key the builder
# wrote, else a short description of the first difference. Keyed with API's
# own _nameKey, reached at call time (no sibling `use`); an API that cannot be
# reached counts as a mismatch, so the list is never trusted unchecked.
sub _probeMismatch {
    my ($h) = @_;
    my $rows = $h->selectall_arrayref('SELECT name, key FROM namekey_probe');
    return 'no probe names' unless @$rows;
    my $key = Plugins::Discography::API->can('_nameKey') or return 'no _nameKey';
    my $n = 0;
    for my $r (@$rows) {
        my $mine = eval { $key->($r->[0]) } // '';
        next if $mine eq $r->[1];
        return "'$r->[0]' keys as '$mine', the pack has '$r->[1]'" . (@$rows > 1 ? ' (and maybe more)' : '');
    }
    return '';
}

# Run one read; any error closes the pack for the rest of the run (a damaged
# file must not answer at all), and the caller asks MusicBrainz.
sub _try {
    my ($what, $code) = @_;
    my $h = _dbh() or return undef;
    my @r = eval { $code->($h) };
    if ($@) {
        $log->warn("dsc: MusicBrainz pack read failed ($what), closed: $@");
        __PACKAGE__->shut('read failed');
        return undef;
    }
    return wantarray ? @r : $r[0];
}

sub available { _dbh() ? 1 : 0 }

# Status for the CLI (["discography","pack"]).
sub info {
    my $have = _dbh();
    return { enabled => enabled(), path => path(), present => (-f path() ? 1 : 0),
             open => ($have ? 1 : 0), ($broken ? (refused => $broken) : ()),
             ($held ? (held => $held) : ()), ($info ? %$info : ()) };
}

# An mbid as a blob literal, or undef for anything that is not a uuid (which
# is all that ever reaches SQL from here, so the literal is safe).
sub _blob {
    my ($mbid) = @_;
    return undef unless defined $mbid && $mbid =~ $UUID_RE;
    (my $h = lc $mbid) =~ s/-//g;
    return "X'$h'";
}

# The first 8 bytes of an mbid as a blob literal (format 4's gk), or undef.
sub _prefix {
    my ($mbid) = @_;
    return undef unless defined $mbid && $mbid =~ $UUID_RE;
    (my $h = lc $mbid) =~ s/-//g;
    return "X'" . substr($h, 0, 16) . "'";
}

sub _f4 { return ($info && ($info->{format} // 0) >= 4) ? 1 : 0 }

sub _uuid {
    my $h = unpack('H32', $_[0] // '');
    return length $h == 32
        ? join('-', substr($h, 0, 8), substr($h, 8, 4), substr($h, 12, 4), substr($h, 16, 4), substr($h, 20))
        : '';
}

# The artist's pack id: its own gid, else a merged-away gid's current artist.
sub _artistId {
    my ($h, $mbid) = @_;
    my $b = _blob($mbid) or return undef;
    my ($id) = _f4()
        ? $h->selectrow_array('SELECT a.id FROM artist_gk k JOIN artist a ON a.id = k.id'
                              . ' WHERE k.gk = ' . _prefix($mbid) . " AND a.gid = $b")
        : $h->selectrow_array("SELECT id FROM artist WHERE gid = $b");
    ($id) = $h->selectrow_array("SELECT artist FROM artist_redirect WHERE gid = $b") unless defined $id;
    return $id;
}

my %KIND = (1 => 'member of band', 2 => 'collaboration');
my @SVC_URL = (undef, 'https://open.qobuz.com/artist/%s', 'https://www.deezer.com/artist/%s',
               'https://www.discogs.com/artist/%s');

# MusicBrainz's `artist/<mbid>?inc=aliases+artist-rels+url-rels` reply, built
# from the pack, plus the number of groups the artist is credited on:
# ($reply, $ngroups), or () when the pack does not hold the artist. The reply
# carries what API::_artistReadParsed reads: id, name, aliases (name, locale,
# primary; MusicBrainz's order, by name), relations (member-of-band and
# collaboration both ways, with `eponymous`; the service urls). No
# `release-groups`: the pack has no group lists.
sub artistRead {
    my ($class, $mbid) = @_;
    my @r = _try('artist read', sub {
        my ($h) = @_;
        my $id = _artistId($h, $mbid);
        return () unless defined $id;
        my ($gid, $name, $ng) = $h->selectrow_array('SELECT gid, name, ngroups FROM artist WHERE id = ?', undef, $id);
        my @aliases = map { { name => $_->[0], locale => (length $_->[1] ? $_->[1] : undef),
                              primary => ($_->[2] ? 1 : undef) } }
                      sort { $a->[0] cmp $b->[0] || $a->[1] cmp $b->[1] }
                      @{ $h->selectall_arrayref('SELECT name, locale, prim FROM artist_alias WHERE artist = ?', undef, $id) };
        my @rels;
        # MusicBrainz's order within a type: format 3 by each relation's own
        # key, format 2 by its rank over the whole table (the same order).
        my $order = ($info && ($info->{format} // 0) >= 3) ? 'r.skey' : 'r.ord';
        for my $dir ([ 'forward', 'a', 'b' ], [ 'backward', 'b', 'a' ]) {
            my ($d, $me, $other) = @$dir;
            # Format 4 finds the backward links through its artist_rel_b table
            # (format 3 had an index on b); the rows and their order are the same.
            my $q = (_f4() && $me eq 'b')
                  ? 'SELECT r.kind, r.epon, t.gid, t.name FROM artist_rel_b x'
                  . ' JOIN artist_rel r ON r.a = x.a AND r.b = x.b AND r.kind = x.kind'
                  . " JOIN artist t ON t.id = r.a WHERE x.b = ? ORDER BY r.kind, $order"
                  : "SELECT r.kind, r.epon, t.gid, t.name FROM artist_rel r JOIN artist t ON t.id = r.$other"
                  . " WHERE r.$me = ? ORDER BY r.kind, $order";
            for my $x (@{ $h->selectall_arrayref($q, undef, $id) }) {
                push @rels, { type => $KIND{ $x->[0] }, direction => $d, 'target-type' => 'artist',
                              attributes => $x->[1] ? [ 'eponymous' ] : [],
                              artist => { id => _uuid($x->[2]), name => $x->[3] } };
            }
        }
        for my $u (@{ $h->selectall_arrayref('SELECT svc, ext FROM artist_url WHERE artist = ? ORDER BY svc, ext', undef, $id) }) {
            my $fmt = $SVC_URL[ $u->[0] ] or next;
            push @rels, { 'target-type' => 'url', url => { resource => sprintf($fmt, $u->[1]) } };
        }
        return ({ id => _uuid($gid), name => $name, aliases => \@aliases, relations => \@rels }, $ng);
    });
    return @r ? @r : ();
}

# A collaboration's vetting data (API::_vetCollabs): { members => how many
# artists collaborate on it, groups => release groups credited to it }, or
# undef when the pack does not hold it.
sub collaboration {
    my ($class, $mbid) = @_;
    return _try('collaboration', sub {
        my ($h) = @_;
        my $id = _artistId($h, $mbid);
        return undef unless defined $id;
        my ($n) = $h->selectrow_array('SELECT count(DISTINCT a) FROM ' . (_f4() ? 'artist_rel_b' : 'artist_rel')
                                      . ' WHERE b = ? AND kind = 2', undef, $id);
        my ($g) = $h->selectrow_array('SELECT ngroups FROM artist WHERE id = ?', undef, $id);
        return { members => $n || 0, groups => $g || 0 };
    });
}

# What the pack holds for these release groups: { mbid => { boot => 0|1,
# discogs => 'master:<id>' | 'release:<id>' | undef, aliases => [..],
# editions => [..], urls => { kind => url } } } for each group it has a row
# for; a group it has no row for has none of them (as of the dump). undef when
# no pack is open.
sub groups {
    my ($class, $mbids) = @_;
    return _try('groups', sub {
        my ($h) = @_;
        my (%out, %byId);
        my @ids = grep { defined _blob($_) } @{ $mbids || [] };
        while (my @chunk = splice(@ids, 0, IN_CHUNK)) {
            # Format 4: candidates by the gid's first 8 bytes, kept only when the
            # FULL gid is one asked for.
            my $rows = _f4()
                ? $h->selectall_arrayref('SELECT r.id, r.gid, r.boot, r.discogs FROM rgx_gk k JOIN rgx r ON r.id = k.id'
                                         . ' WHERE k.gk IN (' . join(',', map { _prefix($_) } @chunk) . ')'
                                         . ' AND r.gid IN (' . join(',', map { _blob($_) } @chunk) . ')')
                : $h->selectall_arrayref('SELECT id, gid, boot, discogs FROM rgx WHERE gid IN ('
                                         . join(',', map { _blob($_) } @chunk) . ')');
            for my $r (@$rows) {
                my $m = _uuid($r->[1]);
                $byId{ $r->[0] } = $out{$m} = {
                    boot => $r->[2] ? 1 : 0,
                    discogs => (!defined $r->[3] ? undef
                                : $r->[3] > 0 ? "master:$r->[3]" : 'release:' . (-$r->[3])),
                    aliases => [], editions => [], urls => {},
                };
            }
        }
        my @rid = sort { $a <=> $b } keys %byId;
        while (my @chunk = splice(@rid, 0, IN_CHUNK)) {
            my $in = join(',', @chunk);
            push @{ $byId{ $_->[0] }{aliases} },  $_->[1] for @{ $h->selectall_arrayref("SELECT rg, name FROM rg_alias WHERE rg IN ($in) ORDER BY rg, name") };
            push @{ $byId{ $_->[0] }{editions} }, $_->[1] for @{ $h->selectall_arrayref("SELECT rg, title FROM rg_edition WHERE rg IN ($in) ORDER BY rg, title") };
            $byId{ $_->[0] }{urls}{ $_->[1] } = $_->[2]    for @{ $h->selectall_arrayref("SELECT rg, kind, url FROM rg_url WHERE rg IN ($in)") };
        }
        return \%out;
    });
}

# THE SAME-NAME LIST (format 2), in the shape API::getArtistCandidates builds
# from MusicBrainz's artist search: [ { mbid, name, score, disambiguation,
# country, type, en } ] for every artist whose _nameKey is $key, the most
# release groups first (Simon, 2026-10-16, chose album count over MusicBrainz's
# search ranking, which it cannot reproduce: the same top act for 787 of 964
# shared names in his library; where they differ MusicBrainz's top mostly has
# FEWER groups, 134 of 159). `score` is the group count, so every reader that
# orders or breaks ties by score follows the same ranking. [] when no artist has
# the key; undef when no format-2 pack is open, or a format-3 pack whose probe
# names key differently here. $key is the CALLER's _nameKey (a character
# string), matched as the builder wrote it (LMS-Discography-Pack's
# builder/namekeys.pl, which runs this plugin's own _norm).
sub sameName {
    my ($class, $key) = @_;
    return undef unless defined $key && length $key;
    return _try('same name', sub {
        my ($h) = @_;
        return undef unless $names && !$names->{nokeys};
        # Format 4 holds the description, type and country on the artist row.
        my ($cols, $join) = _f4() ? ('a.comment, a.type, a.country', '')
                                  : ('i.comment, i.type, i.country', ' LEFT JOIN artist_info i ON i.artist = a.id');
        my $rows = $h->selectall_arrayref(
            "SELECT a.id, a.gid, a.name, a.ngroups, $cols,"
          . " (SELECT al.name FROM artist_alias al WHERE al.artist = a.id AND al.locale = 'en' AND al.prim = 1"
          . '  ORDER BY al.name LIMIT 1)'
          . " FROM artist_name n JOIN artist a ON a.id = n.artist$join"
          . ' WHERE n.key = ? ORDER BY a.ngroups DESC, a.name, a.id', undef, $key);
        return [ map { {
            mbid           => _uuid($_->[1]),
            name           => $_->[2],
            score          => $_->[3] + 0,
            disambiguation => $_->[4],
            country        => $_->[6],
            type           => (defined $_->[5] ? $names->{artist_type}{ $_->[5] } : undef),
            (defined $_->[7] ? (en => $_->[7]) : ()),
        } } @$rows ];
    });
}

# THE RELEASE MAP (format 2): { release-mbid => { rg => group mbid, type =>
# primary type, secondary => [ secondary types ] } } for the given releases the
# pack maps (those of shared groups, and from format 3 those credited to an
# artist their group is not); the rest are absent. In the shape
# API::_setRelType reads from MusicBrainz's `release-group`. undef when no
# format-2 pack is open.
sub releases {
    my ($class, $mbids) = @_;
    return _try('releases', sub {
        my ($h) = @_;
        return undef unless $names;
        my %out;
        my @ids = grep { defined } map { _blob($_) } @{ $mbids || [] };
        while (my @chunk = splice(@ids, 0, IN_CHUNK)) {
            my $rows = $h->selectall_arrayref('SELECT gid, rg, type, sec FROM release WHERE gid IN ('
                                              . join(',', @chunk) . ')');
            for my $r (@$rows) {
                my $sec = $r->[3] || 0;
                $out{ _uuid($r->[0]) } = {
                    rg        => _uuid($r->[1]),
                    type      => (defined $r->[2] ? $names->{rg_type}{ $r->[2] } // '' : ''),
                    secondary => [ map { $names->{rg_sec}{$_} } grep { $sec & (1 << $_) && $names->{rg_sec}{$_} }
                                   sort { $a <=> $b } keys %{ $names->{rg_sec} } ],
                };
            }
        }
        return \%out;
    });
}

1;
