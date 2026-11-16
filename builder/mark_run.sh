#!/bin/sh
# Keeps the build's schedule alive: GitHub switches a public repository's scheduled workflows off once the repository
# has had no activity for 60 days, and a build publishes a release, never a commit.
#   sh builder/mark_run.sh <owner/repo> <days> [true]
# When the repository's newest commit is more than <days> days old (or the third argument is `true`: now, whatever its
# age), the file `last-run` at the repository's root is written with the time (UTC) as one commit, made through
# GitHub's API: nothing of the working folder is read or sent, and a commit made meanwhile is no clash.
# A question GitHub does not answer fails the script (so the workflow step); nothing is written then.
set -eu
repo=$1
days=$2
now=${3:-}
case "$repo" in */*) ;; *) echo "mark_run: <owner/repo>, please" >&2; exit 1 ;; esac
case "$days" in ''|*[!0-9]*) echo "mark_run: days, a whole number" >&2; exit 1 ;; esac
if [ "$now" != "true" ]; then
  # GNU date (the runner), else BSD's (macOS).
  since=$(date -u -d "$days days ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-"$days"d +%Y-%m-%dT%H:%M:%SZ)
  n=$(gh api "repos/$repo/commits?since=$since&per_page=1" --jq length)
  case "$n" in
    0) ;;
    1) echo "a commit in the last $days days: nothing written"; exit 0 ;;
    *) echo "mark_run: the commits since $since: '$n'?" >&2; exit 1 ;;
  esac
fi
at=$(date -u +%Y-%m-%dT%H:%MZ)
# The file's id when it is there (a change must name it), nothing when it is not. Asked as the folder's listing: a
# file that is not there is then an empty answer, and only a question that failed is a failure.
sha=$(gh api "repos/$repo/contents" --jq '.[] | select(.name == "last-run") | .sha')
content=$(printf '%s\n' "$at" | base64)
if [ -n "$sha" ]; then
  gh api -X PUT "repos/$repo/contents/last-run" -f message="last-run $at" -f content="$content" -f sha="$sha" --jq .commit.sha
else
  gh api -X PUT "repos/$repo/contents/last-run" -f message="last-run $at" -f content="$content" --jq .commit.sha
fi
echo "last-run written: $at"
