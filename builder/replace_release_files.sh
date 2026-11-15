#!/bin/sh
# Replaces the files of an existing GitHub release with those in a folder (a build run again for the same dump).
#   sh builder/replace_release_files.sh <tag> <folder>
# Every file of the folder is uploaded with --clobber, then every release file the folder does not hold is deleted.
# A failed listing or delete fails the script (so the workflow step); a failed listing deletes nothing.
# A file name GitHub would change on upload (anything outside [A-Za-z0-9._-], a leading or trailing period) is refused
# before anything is sent: the release would list it under its new name, and the sweep below would delete it. A
# dotfile is one (and the upload's glob would skip it).
# What gh does (its source, pkg/cmd/release/shared/upload.go): a file of the same name is DELETED first, then its
# replacement uploaded, 5 files at a time. So while this runs a reader can find a part missing, or a part or the
# manifest newer than the other: whoever reads a release checks each part against the manifest's size and SHA-256.
# A stale file is deleted only once every upload is done.
set -eu
tag=$1
dir=$2
# The characters spelled out: a range ([a-z]) follows the locale in some shells (macOS's sh, bash 3.2: 'é' in [a-z]).
# The dotfiles' globs as well: `*` skips them. A glob that matches nothing stays as written, and is skipped.
for f in "$dir"/* "$dir"/.[!.]* "$dir"/..?*; do
  [ -e "$f" ] || continue
  name=${f##*/}
  case "$name" in
    .*|*.|*[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]*)
      echo "replace_release_files: GitHub would rename '$name': refused, nothing sent" >&2; exit 1 ;;
  esac
done
gh release upload "$tag" "$dir"/* --clobber
# The listing as an assignment of its own: `set -e` ignores a failed substitution in a `for` list (dash, bash, zsh).
# Then read a line at a time: one name a line, never split on blanks nor expanded as a glob.
names=$(gh release view "$tag" --json assets --jq '.assets[].name')
printf '%s\n' "$names" | while IFS= read -r a; do
  [ -n "$a" ] || continue
  [ -f "$dir/$a" ] || gh release delete-asset "$tag" "$a" --yes
done
