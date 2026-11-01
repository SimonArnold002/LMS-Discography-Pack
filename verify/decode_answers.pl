#!/usr/bin/perl
# How long LMS would be held decoding one page answer (worker/moments.mjs writes
# them; LMS-Discography docs/hosted-pack-plan.md §12). LMS runs one event loop, so
# a reply is decoded in one go and nothing else runs meanwhile (the fleet rule:
# a page build must not hold LMS). LMS's HTTP client takes the reply gzipped and
# inflates it, then the plugin decodes the JSON with JSON::XS (LMS's
# JSON::XS::VersionOneAndTwo); both are timed 5 times each, in ms, on THIS
# machine's CPU (not the user's server's); builder/hosted.py takes the slowest and
# the median.
#
# Usage: perl verify/decode_answers.pl <dir>   -> one JSON line on stdout
use strict;
use warnings;
use JSON::XS ();
use Compress::Zlib ();
use Time::HiRes ();

my $dir = shift or die "usage: decode_answers.pl <dir>\n";
opendir(my $dh, $dir) or die "$dir: $!\n";
my @files = sort grep { /\.json\z/ } readdir $dh;
closedir $dh;

my $js = JSON::XS->new->utf8;
my %out;
for my $f (@files) {
    open(my $fh, '<:raw', "$dir/$f") or die "$f: $!\n";
    my $raw = do { local $/; <$fh> };
    close $fh;
    my $gz = Compress::Zlib::memGzip($raw);
    my (@dec, @inf);
    for (1 .. 5) {
        my $buf = $gz;                 # memGunzip empties the buffer it is given
        my $t = Time::HiRes::time();
        my $plain = Compress::Zlib::memGunzip($buf);
        push @inf, Time::HiRes::time() - $t;
        die "$f: gunzip gave other bytes\n" unless defined $plain && $plain eq $raw;
        $t = Time::HiRes::time();
        my $v = $js->decode($plain);
        push @dec, Time::HiRes::time() - $t;
        die "$f: not an object\n" unless ref $v eq 'HASH';
    }
    my $ms = sub { return [ map { sprintf('%.4f', 1000 * $_) + 0 } @_ ] };
    $out{$f} = { bytes => length $raw, gz => length $gz, gunzip_ms => $ms->(@inf), decode_ms => $ms->(@dec) };
}
print JSON::XS->new->canonical->encode({ perl => "$^V", json_xs => $JSON::XS::VERSION, files => \%out }), "\n";
