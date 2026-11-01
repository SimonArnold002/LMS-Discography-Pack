#!/usr/bin/perl
# TODAY'S ANSWERS, for the hosted pack's check. Runs the plugin's own Pack.pm
# (and the Sources.pm whose _norm keys names) on today's pack, built from the
# same dump, and answers a query list (builder/hosted.py `queries`) one JSON
# line per query, in order: {"r": answer}, or {"skip": 1} for a lookup Pack.pm
# does not have (the hosted pack's new ones). builder/hosted.py `compare` then
# holds every hosted answer against these.
#
# Usage: perl verify/hosted_pack_answers.pl <dsc dir> <today's pack.db> <queries.jsonl> <answers.jsonl>
#   <dsc dir> holds Discography/Pack.pm + Discography/Sources.pm
use strict;
use warnings;

my ($dsc, $file, $queries, $out) = @ARGV;
die "usage: hosted_pack_answers.pl <dsc dir> <pack.db> <queries.jsonl> <answers.jsonl>\n" unless $out;
-f "$dsc/Discography/Pack.pm" && -f "$dsc/Discography/Sources.pm" or die "no Pack.pm/Sources.pm under $dsc/Discography\n";

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
use File::Temp ();
use JSON::PP ();

my $tmp = File::Temp->newdir();
mkdir "$tmp/Plugins";
symlink Cwd::abs_path("$dsc/Discography"), "$tmp/Plugins/Discography" or die "symlink: $!";
unshift @INC, "$tmp";
$CACHEDIR = "$tmp/cache";
mkdir $CACHEDIR; mkdir "$CACHEDIR/DiscographyPack";
symlink Cwd::abs_path($file), "$CACHEDIR/DiscographyPack/pack-under-test.db" or die "symlink: $!";
require Plugins::Discography::Sources;
{   # API::_nameKey as the plugin has it, without loading API.pm (as verify/pack_equiv.pl).
    no strict 'refs'; no warnings 'redefine';
    *{'Plugins::Discography::API::_nameKey'} = sub {
        my ($name) = @_; $name = defined $name ? $name : '';
        utf8::encode($name) if utf8::is_utf8($name);
        return Plugins::Discography::Sources::_norm($name);
    };
}
require Plugins::Discography::Pack;
%PREF = (mb_pack => 1, mb_pack_file => 'pack-under-test.db');
my $PACK = 'Plugins::Discography::Pack';
$PACK->reload;
die "$file did not open: " . ($PACK->info->{refused} // '?') . "\n" unless $PACK->available;

my %CALL = (
    artistRead    => sub { [ $PACK->artistRead($_[0]) ] },
    collaboration => sub { $PACK->collaboration($_[0]) },
    groupCount    => sub { $PACK->groupCount($_[0]) },
    sameName      => sub { $PACK->sameName($_[0]) },
    groups        => sub { $PACK->groups($_[0]) },
    releases      => sub { $PACK->releases($_[0]) },
);

my $J = JSON::PP->new->canonical->utf8->allow_nonref;
open my $in, '<:raw', $queries or die "$queries: $!";
open my $fh, '>:raw', $out or die "$out: $!";
my ($n, $answered) = (0, 0);
while (my $line = <$in>) {
    next unless $line =~ /\S/;
    my $q = $J->decode($line);
    my $code = $CALL{ $q->{f} };
    if ($code) {
        print {$fh} $J->encode({ r => $code->(@{ $q->{a} }) }), "\n";
        $answered++;
    } else {
        print {$fh} qq({"skip":1}\n);
    }
    $n++;
}
close $fh;
require Digest::MD5;
my $md5 = do { open my $p, '<:raw', "$dsc/Discography/Pack.pm" or die; Digest::MD5->new->addfile($p)->hexdigest };
printf "Pack.pm md5 %s on today's pack (format %s, dump %s): %d queries, %d answered, the rest are lookups it does not have\n",
    $md5, $PACK->info->{format}, $PACK->info->{dump} // '?', $n, $answered;
