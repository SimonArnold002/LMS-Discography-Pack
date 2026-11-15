# LMS-Discography-Pack

Builds a compact data set from MusicBrainz's free (CC0) full dump, for the
[Discography](https://github.com/SimonArnold002/LMS-Discography) plugin for Lyrion Music Server.

The build (`.github/workflows/build.yml`, started by hand) downloads a dump, builds the tables
(`builder/build_pack.py`, `builder/measure_extra.py`), lays the same records out by key (`builder/keyed.py`), cuts
the result into parts with a manifest (`builder/load_parts.py`) and publishes them as a release `data-<dump>`.

Tests, on a made-up dump (a few rows, no download): `python3 -m unittest discover -s tests`. They need perl and a
checkout of LMS-Discography beside this one (or `$DSC_DIR`) for the name keys.

## Data

MusicBrainz data is CC0 (public domain). See https://musicbrainz.org/doc/About/Data_License.
