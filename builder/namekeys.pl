#!/usr/bin/perl
# The pack's same-name key: DSC's OWN _nameKey (Sources::_norm on the name's
# UTF-8 octets), computed by the real code, so the pack's same-name set folds
# names exactly as the plugin compares them. A Python port would drift the first
# time the plugin's matcher changes.
#
# stdin:  <id> TAB <name> per line (UTF-8)
# stdout: <id> TAB <key>  per line (UTF-8; an empty key is skipped)
#
# Usage (from build_pack.py): perl builder/namekeys.pl <LMS-Discography checkout> < names.tsv > keys.tsv
#        perl builder/namekeys.pl <LMS-Discography checkout> --named-after < pairs.tsv > ids  (below)
# Only <checkout>/Discography/Sources.pm is loaded (its own `use` of the
# plugin's DB.pm and Slim's modules is stubbed below).
use strict;
use warnings;

my $dsc = shift @ARGV or die "usage: namekeys.pl <LMS-Discography checkout>\n";
-f "$dsc/Discography/Sources.pm" or die "no Discography/Sources.pm under $dsc\n";

BEGIN {
    for my $m (qw(Slim::Utils::Log Slim::Utils::Prefs Slim::Utils::Cache
                  Slim::Utils::PluginManager Slim::Utils::Timers
                  Slim::Control::Request Plugins::Discography::Plugin)) {
        (my $p = $m) =~ s{::}{/}g;
        $INC{"$p.pm"} = 1;
    }
    no strict 'refs';
    *{'Slim::Utils::Log::logger'}          = sub { bless {}, 'T::Null' };
    *{'Slim::Utils::Prefs::preferences'}   = sub { bless {}, 'T::Null' };
    *{'Slim::Utils::Cache::new'}           = sub { bless {}, 'T::Null' };
    *{'Plugins::Discography::DB::store'} = sub { bless {}, 'T::Null' }; $INC{'Plugins/Discography/DB.pm'} = 1;
    *{'Plugins::Discography::Plugin::dbg'} = sub { };
    push @{'Slim::Utils::Log::ISA'},   'Exporter';
    push @{'Slim::Utils::Prefs::ISA'}, 'Exporter';
    @{'Slim::Utils::Log::EXPORT'}   = ('logger');
    @{'Slim::Utils::Prefs::EXPORT'} = ('preferences');
}

package T::Null;
our $AUTOLOAD;
sub AUTOLOAD { return }
sub DESTROY  { }

package main;

use File::Temp ();
my $tmp = File::Temp->newdir();
mkdir "$tmp/Plugins" or die "mkdir: $!";
require Cwd;
symlink Cwd::abs_path("$dsc/Discography"), "$tmp/Plugins/Discography" or die "symlink: $!";
unshift @INC, "$tmp";
require Plugins::Discography::Sources;
my $norm = \&Plugins::Discography::Sources::_norm;

# API::_nameKey hands _norm the name's UTF-8 octets; _norm decodes them and
# returns a CHARACTER string (measured 2026-10-16: the same key for octets or
# characters in, flagged whenever non-ASCII). Written out as UTF-8, it is the
# same text the plugin binds under sqlite_unicode.
binmode STDIN;
binmode STDOUT;
local $/ = "\n";
# --named-after: stdin <id> TAB <artist name> TAB <band name>; stdout the <id> of every line whose band is NAMED AFTER
# the artist by the plugin's own rule (Sources::_namedAfter on the two _norm'd names: the band's name begins with his
# whole name and adds to it, a leading "the" ignored).
# The rule is the plugin's Sources::_namedAfter where the checkout has it. A checkout from before the plugin had it
# as a sub of its own gets the copy below, the same lines (--named-after=own asks for the copy whatever the checkout
# holds: the tests hold the two to the same answers).
sub own_named_after {
    my ($an, $bn) = @_;
    s/^the // for $an, $bn;
    return 0 unless length $an && length $bn && $bn ne $an;
    return index("$bn ", "$an ") == 0 ? 1 : 0;
}
if (@ARGV && $ARGV[0] =~ /^--named-after(=own)?$/) {
    my $na = (!$1 && Plugins::Discography::Sources->can('_namedAfter')) || \&own_named_after;
    while (my $line = <STDIN>) {
        chomp $line;
        my ($id, $an, $bn) = split /\t/, $line, 3;
        next unless defined $bn && length $an && length $bn;
        my ($ka, $kb) = ($norm->($an), $norm->($bn));
        next unless defined $ka && length $ka && defined $kb && length $kb;
        print "$id\n" if $na->($ka, $kb);
    }
    exit 0;
}
while (my $line = <STDIN>) {
    chomp $line;
    my ($id, $name) = split /\t/, $line, 2;
    next unless defined $name && length $name;
    my $k = $norm->($name);
    next unless defined $k && length $k;
    utf8::encode($k) if utf8::is_utf8($k);
    print "$id\t$k\n";
}
