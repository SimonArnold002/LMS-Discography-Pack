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

## The hosted pack test (2026-11-01, branch `hosted-test`, local, not pushed)
- What: the hosted pack for Cloudflare D1 (behind a Worker), built ONCE with its
  content fixed (Simon: "We really would not be rebuilding the DB more than
  once"), in TODAY's table layout. **Content (Simon, 2026-11-01, plan §10-§11):
  today's pack (format 3) + every artist's group list (rg, credit, artist_rg) +
  the whole release map + barcodes + streaming album ids + the alias / old-name
  keys (alias_name).** Out: layer C (the track match stays on ListenBrainz), a
  typo index (closest names stay MusicBrainz's). Rule: anything Discography reads
  from ListenBrainz or the community API today stays there; the group lists are
  for the untagged library artist's lookup (C2) only, not the page's first list.
  The bucketed "keyed" layout and files (Static Assets, R2) were dropped the same
  day. Plan, decisions, measured sizes: LMS-Discography `docs/hosted-pack-plan.md`.
  Branch made from `origin/measure-extra` (bcb51b4), so this file is that
  branch's (no format 4 section; format 4 lives on `format4`).
- "See if we lose anything ... if we do we go back to MB for the problematic ones"
  (Simon): three checks, each reported per lookup with examples:
  1. today's lookups through the hosted reader against Pack.pm on today's pack,
     exactly (`releases`: every Pack.pm entry, the whole map's others counted);
  2. the new lookups (artistList, aliasName, the whole map, barcode, svcAlbum)
     against `truth`: answers built from the dump's facts by other SQL;
  3. `mbcheck`: a sample asked of MusicBrainz: the alias search (names that are
     no act's own name) and the C2 title search, against the file. Asked of
     Simon's local mirror (`http://plex:5000/ws/2/`, Simon 2026-11-01: "use the
     local MB mirror not public MB please to expediate tests on the mb
     comparisions"), so it runs ON THE MAC with the run's artifact (the runner
     cannot reach the mirror; the workflow asks MusicBrainz nothing but the
     dumps). musicbrainz.org is still taken, MB_GAP apart; the builder's UA, no
     e-mail. Every difference is then looked up by id (`artist/<mbid>`, the
     database) and given its reason (`WHY`): merged since, an edit since, an act
     the file does not hold, a longer name matched, the search missing a name the
     act has. **The mirror's search index is stale, its database current**
     (2026-11-01: the index stops near artist row id 3,335,227, 2.84% of the
     pack's acts newer; none of the 20 newest found by `arid:`); the lookups are
     what keep its differences honest. Refuses a Sources.pm whose name keys are
     not the file's (namekey_probe).
- Files: `builder/hosted.py` (aliases, narrow, delta, grow, measure, dumps,
  queries, truth, pages, moments, compare, mbcheck, run, report), `worker/reader.mjs`
  (the Worker's reader, Pack.pm's answer shapes, plus artistList and aliasName),
  `worker/node-d1.mjs` (D1's interface over node:sqlite; rows_read a LOWER bound
  of D1's rows scanned), `worker/answers.mjs`, `worker/pages.mjs` (one cold page;
  its groups from the dump's facts, as the first list gives them),
  `verify/hosted_pack_answers.pl` (Pack.pm on today's pack), `tests/test_hosted.py`,
  `.github/workflows/hosted-test.yml` (input `every`).
- `verify/plugin/Pack.pm` is a COPY of LMS-Discography's working Pack.pm
  (0.56.107, md5 3515d996...; Pack.pm is not on any pushed DSC branch yet). The
  workflow pairs it with DSC_REF's Sources.pm (the name keys the published packs
  use) in `dscp/`; refresh the copy by hand when Pack.pm changes.
- The delta: gzipped JSON lines, per table the dels then the puts, BLOBs as
  base64; applied per row as an upsert or a delete, IN PLACE, no VACUUM (as the
  Worker will); proven old + delta == new before it counts.
- TWO LAYOUTS of the same content (Simon, 2026-11-01: one pack only, hosted or
  download, so the hosted layout is free; the keyed one "we still should not
  discount"): today's tables (narrow) and keyed (`entries` + `keyed`, built from
  the narrow file: kinds art, lst, grp, nam, ali, rel, bar, svc; JSON entries,
  bucketed by gid bits or SHA-1 of the key text, deflated, one row per bucket, no
  index). `run` builds keyed at 2^12/14/16 (jobs in parallel), keeps
  hosted-B-keyed16.db, and `compare` holds every file to the same answers (and,
  with no truth, the files against each other). Keyed is OUT for a download pack:
  the real update 20261024->20261028 would re-send 24-181 MB gz (plan §11 end).
  Tests 60 (21 in test_hosted.py); keyed mutants 12/12 (mut_keyed.py).
- ONE REQUEST PER PAGE (2026-11-01, LMS-Discography `docs/hosted-pack-plan.md` §12; Simon "yes" to measuring it):
  `worker/moments.mjs` builds each named page's whole answer (artist read, same-name list, collaborations' vetting,
  the extras of every group on the file's own list; variants: + up to 100 owned release ids from `pages` (the facts'
  f_release), + eponymous bands, + every forward band = an upper bound of the fold) and measures D1 queries, rows read,
  kB plain/gz per layout; `verify/decode_answers.pl` times Perl's memGunzip + JSON::XS decode, 5 runs each (raw ms;
  `hosted._spread` takes the slowest and the median; memGunzip EMPTIES its buffer, so each run gets a copy).
  `hosted.moments()` runs both and deletes the answers; `run` does it for narrow and every keyed size; report 3b.
  Needs perl JSON::XS (the workflow installs libjson-xs-perl; tests skip without it). TestMoments (5): the answer ==
  the separate lookups, both layouts; The Sugarcubes (backward links only) fold nothing; reads reset per variant;
  timings filed from their own file (Perl's step faked); the report flags layouts that differ. 66 tests; mutants
  21/21 (session scratchpad mut_moments.py).
- D1 MEASURED 2026-11-01 (throwaway database, deleted; LMS-Discography `docs/hosted-pack-plan.md` §11 end): an
  upsert writes 1 + one per index of its table, new or existing, changed or not; a delete by key writes 1 (no
  index entry); whole emptied pages are given back at once, part-empty ones never; no VACUUM. `grow` and
  `report` count that way (the report from the per-table puts, so a run made before the change reads right).
- The workflow publishes nothing and never touches Cloudflare. It commits
  `reports/hosted-<dump B>-run<run id>.txt` here (one per run: two runs of one dump clashed on one name, 2026-11-01) and keeps hosted-B.db, pages.json,
  queries.jsonl, truth.jsonl, aliases-B.tsv and the Sources.pm md5 14 days as an
  artifact for the local test (`hosted.py` docstring: THE LOCAL TEST, which
  includes mbcheck on the mirror).
- First mbcheck result (2026-11-01), the alias half only, on the PUBLISHED
  pack-20261028 + its alias list (built locally from the built pack, no dump):
  2,000 names, 37 s; 1,905 same first act, 6 lower, 70 none found (all the act
  having the name in the database: the stale index), 16 a longer name matched,
  2 merged since, 1 an empty act the pack never keeps. LMS-Discography
  `docs/hosted-pack-plan.md` §11, end.
- Tests: 15 in test_hosted.py (54 with the others), all passing locally
  2026-11-01, with the working Sources.pm and with the runner's pair;
  MusicBrainz stubbed (nothing in the suite asks it), its stub answering
  lookups by id too. The reader test pairs DSC's Sources.pm with
  verify/plugin/Pack.pm itself (`plugin_dir`, as the hosted workflow does), so the
  Tests workflow, whose DSC checkout holds Sources.pm only, runs it too; skipped
  without Node 22.5+ or perl's DBD::SQLite. The made-up
  dump gained aliases for the checks: "Kukl Group" on both Kukls (an alias naming
  two acts) and Björk's "Kukl" (an alias that is another act's own name).
  Mutants (session scratchpad mut_hosted3.py, the unmutated suite checked first):
  24 of 26 caught; both survivors equivalent (the narrow reader's alias sort,
  which SQLite's key order gives; truth's `inlist` filter, which f_artist_rg
  already implies). The local-test commands give SAME on the made-up dump.
  The mirror change and the reasons (mut_hosted4-7.py): 18 more planted, all
  caught after two tests were added (report counts with distinct numbers; the
  title reasons shown).

## The bucket rebuild test (2026-11-02, branch `hosted-test`)
- Why: a keyed update re-sends every changed bucket WHOLE (20261028 -> 20261101 at
  16,384 a kind: 53,851 buckets, 476.9 MB gz, run 41350885542). Simon 2026-11-01:
  "we should look to test for that next", "as long as it doesnt run us over any
  usage limits in our subscription". So the builder sends only the ENTRIES that
  changed and the Worker rebuilds each changed bucket in D1.
- `builder/entry_delta.py make`: per changed bucket its puts (key, value JSON as
  stored), dels, and the SHA-256 of its CANONICAL text before and after (entries in
  key order = code points = UTF-8 bytes; `'{' + json key + ':' + value ... '}'`);
  hash lists of every bucket of A and B; PROVEN (A + the delta read back == B,
  every bucket, and the meta) before it is kept. `keyed_meta` repeats keyed()'s
  meta lines so hosted.py stays untouched (the suite holds them equal).
  `hashes` gives the same list from a KEYED file (the file loaded into D1).
- `rebuild/patch.mjs`: the Worker's rebuild of one bucket (Web APIs only, so Node
  runs it too): refuses a bucket that is not the line's before ('before'), a
  missing del, an after that does not hash right; a bucket already at the after is
  left alone (a retried batch writes nothing); an emptied bucket = delete the row.
  It re-serialises every value with JSON.stringify: all 17,747,916 values of the
  20261028 file come back byte for byte (Node 26, measured 2026-11-02).
  `rebuild/apply_node.mjs` applies a delta to a keyed file copy with it.
- `.github/workflows/entry-delta.yml`: dumps PINNED (A 20261028-002228, the file
  the Mac loads into D1; B 20261101-125824), its own concurrency group, triggered
  only by its own files (so a push does not start the hosted test); by hand only since
  its run 41428172063. Commits
  `reports/edelta-<B>-run<id>.txt`; artifact `edelta-<B>` (delta, hash lists,
  Node's result). MusicBrainz keeps two dumps: once a newer one is out, the pinned
  A is gone.
- The Cloudflare half runs from the Mac (session scratchpad, ids local only).
- Tests: tests/test_entry_delta.py, 12 (three of them the edges: a new bucket, an
  emptied one, a deleted entry, made on a copy of version 2's entries); mutants 16 of 16
  caught, one equivalent control survived (session scratchpad
  rebuild/mut_edelta.py). 80 in the whole suite.
- RESULTS 2026-11-02: runner run 41428172063 (report reports/edelta-20261101-125824-run41428172063.txt): the
  delta 26.2 MB gz (63.9 plain) against 476.9 MB re-sending whole buckets; proven; Node rebuild SAME. On
  Cloudflare (from the Mac, Simon's "yes"): 53,847 buckets rebuilt in D1 in 213 s, 0 refused, every one read
  back == B; CPU 159,079 ms from Workers Logs (2.95 ms a bucket); D1 shrank 1,255 -> 1,239 MB. Full figures:
  LMS-Discography docs/hosted-pack-plan.md §11, end.

## The self-applying update test (2026-11-02, branch `hosted-test`)
- Simon 2026-11-02 "yes": the Worker fetches the update from a GitHub release and applies it in scheduled chunks BY
  ITSELF (the last untested part of the hosted update path).
- `builder/entry_delta.py parts`: the delta's bucket lines shuffled (seed 1, so each part costs about the same) into
  gzipped parts (mtime 0: same lines, same bytes) + `edelta-<B>.json`, the manifest (from/to dumps, the meta line, each
  part's lines, bytes, SHA-256).
- `rebuild/updater.mjs` `tick(env)`: one scheduled run. No `dump` in meta = no database yet (a load writes its meta
  LAST). A lease (table `upd`, row 'lease', one conditional upsert) = one run at a time. Manifest from == dump: the
  next part (its place in `upd` 'run'), size + SHA-256 + lines checked (else fetched again next run, nothing
  written), applied PER_BATCH (50) buckets a D1 batch; a run cut off is redone (a rebuilt bucket is "already"); a
  refused bucket STOPS the update for good (`run.error`); every part in -> the meta line + the 'run' row's delete in
  one batch, so the dump changes only once all of it is there; dump == to -> 'current'. `rebuild/node-d1w.mjs` (a
  read-write D1 over node:sqlite, `failBatch` to cut a run off) and `rebuild/tick_node.mjs` run it in the suite.
- `.github/workflows/edelta-release.yml`: an entry-delta run's artifact as a TEST pre-release, tag
  `hosted-test-edelta-<B>` (never `pack-*`: the monthly prune and the plugin never see it), never "latest".
- Tests: TestParts (1), TestUpdater (7: part by part then the new dump, the meta waits for every part, a run cut off
  part-way, a damaged part fetched again with nothing written, a held lease, another dump, a wrong bucket stops it
  for good); 88 in the whole suite; mutants 15 of 15 (session scratchpad rebuild/mut_updater.py). Run in the local
  Workers simulator too (parts served over local HTTP): every bucket and the meta == B; /stop holds.
- RESULTS 2026-11-02 (pushed 2596b71; release run 41464609782, the pre-release's 15 files == a local `parts` rebuild
  byte for byte): on Cloudflare the Worker's OWN cron runs fetched and applied all 14 parts, 53,847 buckets, 0 refused,
  every one read back == B, meta == B. CPU 129,847 ms from Workers Logs (2.41 ms a bucket, 0.43% of the month an
  update); busiest run 15,876 ms of the cron's 30 s. About 44 minutes, parts 45-306 s each for the same work: placement
  moves fetch handlers only, so the cron runs far from D1 (location unmeasured; Workers Logs has none for a scheduled
  event). A cron trigger needs the account's workers.dev subdomain even on a custom domain. Full figures and the two
  fixes to measure (work next to D1, fewer trips): LMS-Discography docs/hosted-pack-plan.md §11, end.

## The spare-copy load test (2026-11-02, branch `hosted-test`)
- Why: the self-applying update left readers on a MIXED database for its whole run (44 minutes). Simon 2026-11-02: keep
  two copies only during an update, the old one serving until the new one is complete, then switch over and empty the
  old one; "yes" to loading the spare WHOLE from the new dump's file (no delta) and to this test.
- `builder/load_parts.py parts <keyed file> <out dir> <MB a part>`: binary records [1-byte kind index][4-byte key]
  [4-byte length][value], kinds in KINDS order, keys in order, cut at a record once a part reaches its size (same file,
  same bytes); `load-<dump>.json`: format `load-1`, dump, kinds, rows per table, the meta rows, each part's records,
  bytes, SHA-256. Refuses a file not keyed by 4-byte buckets or holding other tables.
- `rebuild/loader.mjs` `tick(env)` (env DB1, DB2, MANIFEST_URL): the copy that SERVES is the newest COMPLETE one (meta
  names a dump; the meta goes in last). A manifest newer than the serving dump: the other copy (DB1 when none serves) is
  dropped and recreated, then PARTS_PER_RUN (2) parts a run, upserts, BATCH_BYTES (3 MB) a D1 batch; a damaged part is
  fetched again next run; a part or table short of its rows STOPS the load before the meta; when every table holds the
  manifest's rows, the meta (= the switch). The copy that served before is emptied (meta first, then the tables)
  EMPTY_AFTER_MS (5 min) after the switch; readers keep their choice READ_CACHE_MS (30 s). The loader's rows (lease,
  'run', 'old') live in DB1's `upd`, never dropped. A switch cut off after its meta is finished by the next run; a load
  given up (the manifest back at the serving dump) has its half-loaded copy emptied. `serving(env)` is the reader's pick.
- `.github/workflows/load-release.yml`: builds dump B's keyed file, holds it to entry-delta run 41428172063's list of B
  (`entry_delta.py same`: every bucket the same content), lays it out in 32 MB parts and publishes the TEST pre-release
  `hosted-test-load-<B>` (never `pack-*`, never "latest"). Triggered by its own file only.
- Tests: tests/test_load_parts.py, 16 (TestLoadParts 3, TestLoader 13 through `rebuild/tick_load_node.mjs`); 104 in the
  suite; planted bugs 28 of 28 caught (session scratchpad rebuild/mut_loader.py). Run in the local Workers simulator
  too: the old copy served until the switch, the reader moved over after its 30 s, the old copy emptied, every bucket
  and the meta == B. The Cloudflare half runs from the Mac (session scratchpad `rebuild/run_load.sh`).
