# LMS-Discography-Pack

The MusicBrainz pack for the [Discography](https://github.com/SimonArnold002/LMS-Discography)
plugin for Lyrion Music Server: a small read-only SQLite file built from
MusicBrainz's free (CC0) full dump. With it, Discography's artist pages need no
MusicBrainz requests.

The plugin downloads it only when you ask (Settings > Discography >
MusicBrainz pack > Download), then keeps it current at a time you choose,
fetching a small update after each new dump.

## What runs here

| Workflow | When | What |
|---|---|---|
| Build pack | every 6 hours | When MusicBrainz has published a newer dump (twice a week): streams the dump, builds the pack, makes a delta from the last pack, proves old + delta == new, and publishes a release `pack-<dump>` plus `manifest-v<format>.json` |
| Prune releases | 1st of each month | Keeps the newest 8 releases, deletes the rest |
| Tests | every push | The builder's tests on a made-up dump |

Only the newest two releases keep the whole pack; older ones keep just their
delta. A device further behind than the oldest delta downloads the whole pack.

## Data

MusicBrainz data is CC0 (public domain). See https://musicbrainz.org/doc/About/Data_License.
