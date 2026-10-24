#!/usr/bin/perl
# THE SAME ANSWERS FROM TWO FORMATS. Runs the plugin's own Pack.pm (and the
# Sources.pm whose _norm keys names) against a format-3 and a format-4 pack of
# ONE dump, and calls every lookup the plugin makes, for every id and key the
# format-3 file holds plus ids and keys it does not:
#   artistRead     every artist gid, every merged-away gid, misses
#   collaboration  every artist gid, misses
#   sameName       every name key, misses
#   groups         every group gid, 100 to a call (as a page asks), a third misses
#   releases       every release gid, 100 to a call, a third misses
# Each answer is encoded canonically and hashed; the two formats must give the
# same hash for every call, in the same order. Every call is timed too, so the
# report shows whether format 4 is slower anywhere.
#
# EACH FORMAT RUNS ITS OWN PLUGIN CODE, in its own process: format 3 with the
# Pack.pm users have today (the one shipped with it), format 4 with the new
# one. So "the same answers" is today's plugin against tomorrow's, and so is
# the speed.
#
# Usage: perl verify/pack_equiv.pl <dsc3 dir> <f3.db> <dsc4 dir> <f4.db> [sample]
#   <dscN dir> holds Discography/Pack.pm + Discography/Sources.pm
#   sample: test only every n-th id (default 1 = all; a local run on a small pack)
# Exit 0 = every answer the same; 1 = a difference (the first ones are printed).
use strict;
use warnings;

my ($dsc3, $f3, $dsc4, $f4, $every) = @ARGV;
die "usage: pack_equiv.pl <dsc3 dir> <f3.db> <dsc4 dir> <f4.db> [sample]\n" unless $dsc3 && $f3 && $dsc4 && $f4;
$every ||= 1;
for my $dsc ($dsc3, $dsc4) {
    -f "$dsc/Discography/Pack.pm" && -f "$dsc/Discography/Sources.pm" or die "no Pack.pm/Sources.pm under $dsc/Discography\n";
}

our (%PREF, $CACHEDIR);
BEGIN {
    for my $m (qw(Slim::Utils::Log Slim::Utils::Prefs Slim::Utils::Cache
                  Slim::Utils::PluginManager Slim::Utils::Timers
                  Slim::Control::Request Plugins::Discography::Plugin)) {
        (my $p = $m) =~ s{::}{/}g;
        $INC{"$p.pm"} = 1;
    }
    no strict 'refs';
    *{'Slim::Utils::Log::logger'}          = sub { bless {}, 'T::Log' };
    *{'Slim::Utils::Prefs::preferences'}   = sub { bless {}, 'T::Prefs' };
    *{'Slim::Utils::Cache::new'}           = sub { bless {}, 'T::Null' };
    *{'Plugins::Discography::DB::store'}   = sub { bless {}, 'T::Null' }; $INC{'Plugins/Discography/DB.pm'} = 1;
    *{'Plugins::Discography::Plugin::dbg'} = sub { };
    push @{'Slim::Utils::Log::ISA'},   'Exporter';
    push @{'Slim::Utils::Prefs::ISA'}, 'Exporter';
    @{'Slim::Utils::Log::EXPORT'}   = ('logger');
    @{'Slim::Utils::Prefs::EXPORT'} = ('preferences');
}
package T::Null;  our $AUTOLOAD; sub AUTOLOAD { return } sub DESTROY { }
package T::Log;   our $AUTOLOAD; sub AUTOLOAD { print STDERR "LOG: $_[1]\n" if $AUTOLOAD =~ /warn|error/; return } sub DESTROY { }
package T::Prefs;
sub get { my ($s, $k) = @_; return $main::CACHEDIR if $k eq 'cachedir'; return $main::PREF{$k} }
sub set { $main::PREF{ $_[1] } = $_[2]; 1 }
package main;

use Cwd ();
use DBI;
use Digest::MD5 ();
use File::Temp ();
use JSON::PP ();
use Storable ();
use Time::HiRes ();

my $tmp = File::Temp->newdir();
my $PACK = 'Plugins::Discography::Pack';

# In a child: load THIS dsc dir's plugin code and point it at one file.
sub loadPlugin {
    my ($dsc, $file) = @_;
    my $root = "$tmp/" . Digest::MD5::md5_hex($dsc . $file);
    mkdir $root; mkdir "$root/Plugins";
    symlink Cwd::abs_path("$dsc/Discography"), "$root/Plugins/Discography" or die "symlink: $!";
    unshift @INC, $root;
    $CACHEDIR = "$root/cache";
    mkdir $CACHEDIR; mkdir "$CACHEDIR/DiscographyPack";
    symlink Cwd::abs_path($file), "$CACHEDIR/DiscographyPack/pack-under-test.db" or die;
    require Plugins::Discography::Sources;
    {   # API::_nameKey as the plugin has it, without loading API.pm.
        no strict 'refs'; no warnings 'redefine';
        *{'Plugins::Discography::API::_nameKey'} = sub {
            my ($name) = @_; $name = defined $name ? $name : '';
            utf8::encode($name) if utf8::is_utf8($name);
            return Plugins::Discography::Sources::_norm($name);
        };
    }
    require Plugins::Discography::Pack;
    %PREF = (mb_pack => 1, mb_pack_file => 'pack-under-test.db');
    $PACK->reload;
}

# Run $code in a child process; its return value comes back through a file.
sub inChild {
    my ($code) = @_;
    my $out = "$tmp/child-" . rand() . '.st';
    my $pid = fork() // die "fork: $!";
    if (!$pid) { my $r = $code->(); Storable::nstore($r, $out); POSIX::_exit(0) }
    waitpid($pid, 0);
    die "child failed ($?)\n" if $? || !-f $out;
    return Storable::retrieve($out);
}
require POSIX;

# ---- the ids, from the format-3 file -------------------------------------
my $d = DBI->connect("dbi:SQLite:dbname=$f3", '', '', { RaiseError => 1, sqlite_unicode => 1 });
my $uuid = sub { my $h = unpack('H32', $_[0]); join '-', substr($h, 0, 8), substr($h, 8, 4), substr($h, 12, 4), substr($h, 16, 4), substr($h, 20) };
my $col  = sub { my ($sql) = @_; my $n = 0; [ grep { $n++ % $every == 0 } map { $_->[0] } @{ $d->selectall_arrayref($sql) } ] };
srand(20261024);
my $rnd  = sub { my @x = map { sprintf('%08x', int rand 2**32) } 1 .. 4; $uuid->(pack('H32', join '', @x)) };
my $miss = sub { [ map { $rnd->() } 1 .. $_[0] ] };
my %ids;
$ids{artist}   = [ (map { $uuid->($_) } @{ $col->('SELECT gid FROM artist ORDER BY id') }),
                   (map { $uuid->($_) } @{ $col->('SELECT gid FROM artist_redirect ORDER BY gid') }),
                   @{ $miss->(20000) } ];
$ids{key}      = [ @{ $col->('SELECT DISTINCT key FROM artist_name ORDER BY key') },
                   map { "zz no such name $_" } 1 .. 20000 ];
my $chunks = sub {
    my ($hits) = @_;
    my @out;
    my @h = @$hits;
    while (my @c = splice(@h, 0, 67)) { push @out, [ @c, @{ $miss->(33) } ] }
    return \@out;
};
$ids{groups}   = $chunks->([ map { $uuid->($_) } @{ $col->('SELECT gid FROM rgx ORDER BY id') } ]);
$ids{releases} = $chunks->([ map { $uuid->($_) } @{ $col->('SELECT gid FROM release ORDER BY gid') } ]);
$d->disconnect;
printf "ids: %d artists (with redirects and misses), %d name keys, %d group calls, %d release calls\n",
    scalar @{ $ids{artist} }, scalar @{ $ids{key} }, scalar @{ $ids{groups} }, scalar @{ $ids{releases} };

# ---- one pass over a file --------------------------------------------------
my $J = JSON::PP->new->canonical->utf8->allow_nonref;
my @CALLS = (
    [ artistRead    => 'artist',   sub { [ $PACK->artistRead($_[0]) ] } ],
    [ collaboration => 'artist',   sub { $PACK->collaboration($_[0]) } ],
    [ sameName      => 'key',      sub { $PACK->sameName($_[0]) } ],
    [ groups        => 'groups',   sub { $PACK->groups($_[0]) } ],
    [ releases      => 'releases', sub { $PACK->releases($_[0]) } ],
);

sub warm { my ($f) = @_; open my $fh, '<:raw', $f or die "$f: $!"; my $buf; 1 while read($fh, $buf, 1 << 20); close $fh }

sub pass {
    my ($dsc, $real) = @_;
    loadPlugin($dsc, $real);
    warm($real);
    my $info = $PACK->info;
    die "$real did not open: " . ($info->{refused} // '?') . "\n" unless $PACK->available;
    printf "pass %s with %s/Discography/Pack.pm (%s): format %s, dump %s, %s bytes, name keys %s\n",
        $real, $dsc, substr(Digest::MD5::md5_hex(do { local (@ARGV, $/) = ("$dsc/Discography/Pack.pm"); <> }), 0, 12),
        $info->{format}, $info->{dump}, $info->{bytes}, $info->{namekeys};
    my %r;
    for my $c (@CALLS) {
        my ($name, $set, $code) = @$c;
        my ($dig, $times, $hits) = ('', '', 0);
        my $t_all = Time::HiRes::time();
        for my $arg (@{ $ids{$set} }) {
            my $t0 = Time::HiRes::time();
            my $ans = $code->($arg);
            $times .= pack('d', Time::HiRes::time() - $t0);
            $dig   .= Digest::MD5::md5($J->encode($ans));
            $hits++ if ref $ans eq 'ARRAY' ? @$ans : ref $ans eq 'HASH' ? %$ans : defined $ans;
        }
        $r{$name} = { dig => $dig, times => $times, hits => $hits, total => Time::HiRes::time() - $t_all };
        printf "  %-14s %9d calls %9d answered  %7.1f s\n", $name, length($dig) / 16, $hits, $r{$name}{total};
    }
    return \%r;
}

$| = 1;
my $r3 = inChild(sub { pass($dsc3, $f3) });
my $r4 = inChild(sub { pass($dsc4, $f4) });

# ---- the same answers ------------------------------------------------------
my $bad = 0;
print "\nanswers:\n";
for my $c (@CALLS) {
    my ($name, $set, $code) = @$c;
    my ($a, $b) = ($r3->{$name}{dig}, $r4->{$name}{dig});
    my @diff = grep { substr($a, 16 * $_, 16) ne substr($b, 16 * $_, 16) } 0 .. length($a) / 16 - 1;
    $bad += @diff;
    printf "  %-14s %9d calls, %d differ%s\n", $name, length($a) / 16, scalar @diff,
        $r3->{$name}{hits} == $r4->{$name}{hits} ? '' : " (answered: $r3->{$name}{hits} vs $r4->{$name}{hits})";
    my @show = @diff[0 .. ($#diff < 2 ? $#diff : 2)];
    next unless @show;
    my @args = map { $ids{$set}[$_] } @show;
    my @o3 = @{ inChild(sub { loadPlugin($dsc3, $f3); [ map { $J->encode($code->($_)) } @args ] }) };
    my @o4 = @{ inChild(sub { loadPlugin($dsc4, $f4); [ map { $J->encode($code->($_)) } @args ] }) };
    for my $k (0 .. $#args) {
        printf "    %s\n      3: %.400s\n      4: %.400s\n", ref $args[$k] ? "[$args[$k][0], ...]" : $args[$k], $o3[$k], $o4[$k];
    }
}

# ---- the speed -------------------------------------------------------------
sub stats {
    my @t = sort { $a <=> $b } unpack('d*', $_[0]);
    my $p = sub { $t[ int(@t * $_[0]) < @t ? int(@t * $_[0]) : $#t ] * 1000 };
    return ($p->(.5), $p->(.95), $p->(.99), $t[-1] * 1000);
}
print "\nspeed (ms per call: median / 95% / 99% / slowest; format 3 -> format 4):\n";
for my $c (@CALLS) {
    my $name = $c->[0];
    my @s3 = stats($r3->{$name}{times});
    my @s4 = stats($r4->{$name}{times});
    printf "  %-14s %6.3f / %6.3f / %6.3f / %7.2f  ->  %6.3f / %6.3f / %6.3f / %7.2f   total %6.1f s -> %6.1f s (%+.0f%%)\n",
        $name, @s3, @s4, $r3->{$name}{total}, $r4->{$name}{total},
        $r3->{$name}{total} ? 100 * ($r4->{$name}{total} / $r3->{$name}{total} - 1) : 0;
}
print "\n", ($bad ? "DIFFERENT: $bad answer(s) differ\n" : "SAME: every answer of format 4 equals format 3's\n");
exit($bad ? 1 : 0);
