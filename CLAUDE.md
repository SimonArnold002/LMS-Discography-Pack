# LMS-Discography-Pack

Builds and publishes Discography's MusicBrainz pack. The plugin side (Pack.pm,
PackUpdate.pm) lives in LMS-Discography; the pack format is a CONTRACT between
the two: Pack.pm's `%FORMAT_TABLES` must list exactly the tables build_pack.py
writes for that format, and the device's delta apply (PackUpdate.pm) must do
what `make_delta.apply()` does.

## Standing rules
- **Nothing that touches the real dump runs on Simon's Mac** (Simon, 2026-10-16:
  "this cannot run on my mac"): no dump download, no real build, no delta
  measurement, dev runs included. They run on the GitHub runner (Build pack,
  run by hand with `dump`/`force` for development). The tests on the made-up
  dump (tests/fixture.py, a few rows, under a second) MAY run locally (Simon,
  2026-10-16).
- Never commit, push or tag without being asked (fleet rule).
- The name keys come from DSC's own `Sources::_norm` (builder/namekeys.pl loads
  LMS-Discography's Discography/Sources.pm); never port `_norm` to Python.
- Changing what a table holds or its columns = a new FORMAT (build_pack.py's
  FORMAT, Pack.pm's `%FORMAT_TABLES`, a new manifest-v<n>.json).

## Format 3 (2026-10-16)
- `artist_rel.skey`: each relation's own sort key (link_order, begin, end,
  ended, link id; big-endian bytes, a missing date part as the field's
  maximum), replacing format 2's global rank `ord`, which renumbered the
  whole table when one link was added.
- `meta.dump`: the fullexport directory name; deltas go from one to the next.
- `namekey_probe`: names + the keys this build's `_norm` gave them; the device
  recomputes them and turns the same-name list off on a mismatch.
- The `release` map (2026-10-16, same table, so not a new format): every
  release of a shared group, AND a release whose own credit names an artist its
  group's credit does not (Centro-Matic's "South San Gabriel Songs/Music"; the
  DSC session counted 26,865 in the 2026-10-14 dump, unconfirmed until the first
  build here logs it). A credit that only spells the group's artists
  differently is left out.

## Format 4 (2026-10-24, on the `format4` branch until verify.yml passes)
- Why: a delta applied in place splits B-tree pages (31 MB from one 0.57 MB
  delta, measured); a copy of each table in ONE statement packs them full
  again (a copy in steps does not: ~10 points less full), and that needs a
  file with no secondary index. So: `artist_gk` (first 8 gid bytes, id)
  replaces the unique artist gid index, and `rgx` is keyed by (gk, id) itself
  (one probe per group; nothing reads a group by its id); the plugin checks
  the full gid;
  `artist_rel_b` is a table; `artist_info` is folded into `artist`; no ANALYZE.
  378.2 MB against format 3's 415.3 MB (2026-10-21 dump).
- `verify.yml` (format4 branch, or by hand) proves it before it ships: the same
  dump in both formats through the plugin's own Pack.pm, every lookup, every id
  (`verify/pack_equiv.pl`), plus the rows (`verify/compare_tables.py`) and the
  table-by-table rewrite's size. `verify/plugin/Pack.pm` is a COPY of
  LMS-Discography's working Pack.pm under test (reads format 4); refresh it
  when Pack.pm changes. `verify/plugin/Pack-0.56.96.pm` is the one users run
  today (reads format 3); each runs in its own process, so the speed compares
  today's plugin with tomorrow's.
- Pushing format 4 to main makes the scheduled build publish manifest-v4 and
  stop updating manifest-v3: only once verify passes and the plugin side
  (PackUpdate FORMAT 4 + the rewrite) is built.

## The device side (LMS-Discography, PackUpdate.pm)
- Reads manifest-v3.json, takes the deltas when the chain from its pack's dump
  is complete and together under half the whole pack, else the whole pack;
  checks size + SHA-1 of every file gzipped and unzipped; applies a delta with
  exactly `make_delta.apply()`'s statements, in one transaction. Its suite
  (tools/t_packupdate.pl, section G) applies a delta made by THIS repo's
  make_delta.py when the two repos sit side by side: keep `apply()` and the
  device in step, and run that suite after changing either.
- The device also checks each put_/del_ table's columns before applying:
  SQLite reads a double-quoted unknown column as a string, so a delta of the
  wrong shape would otherwise apply as junk.

## Decisions (Simon, 2026-10-16)
- One release per dump; the newest 8 kept (monthly prune); the whole pack on
  the newest two only.
- The device updates at a time the user sets (default 05:00 + a per-install
  offset of up to 30 min), first download by button only.
- The builder lives here, not in LMS-Discography; the run fetches only DSC's
  Sources.pm (ref: repository variable DSC_REF, else `dev`; DSC's `main` holds
  docs only until its first release).
