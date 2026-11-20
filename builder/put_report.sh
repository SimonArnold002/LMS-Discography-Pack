#!/bin/sh
# Writes one report file to a branch of this repository, as one commit made through GitHub's API: nothing of the
# working folder but the report is read or sent.
#   sh builder/put_report.sh <owner/repo> <branch> <reports/name.txt> <local file>
# The path must be a new file under reports/ (a run's reports carry its run id, so none is written twice). A question
# GitHub does not answer fails the script (so the workflow step).
set -eu
repo=$1
branch=$2
path=$3
file=$4
case "$repo" in */*) ;; *) echo "put_report: <owner/repo>, please" >&2; exit 1 ;; esac
case "$branch" in ''|*[!A-Za-z0-9._-]*) echo "put_report: a plain branch name, please" >&2; exit 1 ;; esac
case "$path" in reports/*.txt) ;; *) echo "put_report: a reports/<name>.txt path, please" >&2; exit 1 ;; esac
case "${path#reports/}" in *[!A-Za-z0-9._-]*) echo "put_report: a plain file name under reports/, please" >&2; exit 1 ;; esac
[ -s "$file" ] || { echo "put_report: $file is missing or empty" >&2; exit 1; }
content=$(base64 < "$file" | tr -d '\n')
gh api -X PUT "repos/$repo/contents/$path" -f message="$(basename "$path")" -f content="$content" -f branch="$branch" \
  --jq .commit.sha
echo "report written: $path on $branch"
