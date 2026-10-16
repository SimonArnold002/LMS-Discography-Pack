#!/usr/bin/perl
# The pack's same-name key: DSC's OWN _nameKey (Sources::_norm on the name's
# UTF-8 octets), computed by the real code, so the pack's same-name set folds
# names exactly as the plugin compares them. A Python port would drift the first
# time the shared matcher changes (it is fleet-synced; see CLAUDE.md).
#
# stdin:  <id> TAB <name> per line (UTF-8)
# stdout: <id> TAB <key>  per line (UTF-8; an empty key is skipped)
#
# Usage (from build_pack.py): perl builder/namekeys.pl <LMS-Discography checkout> < names.tsv > keys.tsv
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
while (my $line = <STDIN>) {
    chomp $line;
    my ($id, $name) = split /\t/, $line, 2;
    next unless defined $name && length $name;
    my $k = $norm->($name);
    next unless defined $k && length $k;
    utf8::encode($k) if utf8::is_utf8($k);
    print "$id\t$k\n";
}
