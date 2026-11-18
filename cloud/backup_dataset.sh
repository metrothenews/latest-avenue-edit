#!/bin/bash
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Back up the dataset (chat, tool calls, manual edits; see cloud/DATASET.md) to the "dataset"
# branch of a private GitHub repository. Text only: no media, no models, no projects.
#
#   cloud/backup_dataset.sh <data folder> [repository URL]
#
# The repository defaults to this checkout's origin. Safe to run as often as you like: it
# commits only when something changed. It refuses to push if the repository is public (asked
# of GitHub with no sign-in) or if anything in the dataset looks like a credential.

set -euo pipefail

DATA="${1:?usage: backup_dataset.sh <data folder> [repository URL]}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
URL="${2:-$(git -C "$HERE" remote get-url origin)}"
BRANCH="${DATASET_BRANCH:-dataset}"
SOURCE="$DATA/dataset"
CLONE="$DATA/dataset-backup"

[ -d "$SOURCE" ] || { echo "Nothing to back up yet: $SOURCE does not exist."; exit 0; }

# Never publish it: the dataset holds what was asked and done, which is private.
# Asked with no sign-in: GitHub shows a private repository to nobody (404) and a public one
# to everybody (200), so this works without the gh CLI. If GitHub can't be asked, stop.
case "$URL" in *github.com*)
  SLUG="$(printf '%s' "$URL" | sed -E 's#^.*github\.com[:/]##; s#\.git$##')"
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 20 -A latest-avenue-dataset-backup \
          "https://api.github.com/repos/$SLUG" || true)"
  if [ "$CODE" = "200" ]; then
    echo "Stopped: $SLUG is a public repository. The dataset only goes to a private one."; exit 1
  elif [ "$CODE" != "404" ]; then
    echo "Stopped: could not ask GitHub whether $SLUG is private (got '$CODE'). Nothing was backed up."; exit 1
  fi
;; esac

# Anything that looks like a credential stops the backup. Nothing in the dataset should:
# the server never writes tokens to it. This is a second lock, not the first.
PATTERN='eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abprs]-[A-Za-z0-9-]{20,}'
if FOUND="$(grep -rIlE "$PATTERN" "$SOURCE" 2>/dev/null)" && [ -n "$FOUND" ]; then
  echo "Stopped: these files contain something that looks like a credential:"; echo "$FOUND"; exit 1
fi

if [ ! -d "$CLONE/.git" ]; then
  rm -rf "$CLONE"
  if git ls-remote --exit-code --heads "$URL" "$BRANCH" >/dev/null 2>&1; then
    GIT_LFS_SKIP_SMUDGE=1 git clone --quiet --single-branch --branch "$BRANCH" "$URL" "$CLONE"
  else
    git init --quiet -b "$BRANCH" "$CLONE"
    git -C "$CLONE" remote add origin "$URL"
  fi
fi
git -C "$CLONE" config user.name >/dev/null || git -C "$CLONE" config user.name "latest avenue video"
git -C "$CLONE" config user.email >/dev/null || git -C "$CLONE" config user.email "dataset@latestavenue.invalid"
git -C "$CLONE" pull --quiet --ff-only origin "$BRANCH" 2>/dev/null || true

# Mirror the dataset into the clone (text files only; the clone's .git and README stay).
find "$CLONE" -mindepth 1 -maxdepth 1 ! -name .git ! -name README.md -exec rm -rf {} +
(cd "$SOURCE" && find . -type f \( -name '*.jsonl' -o -name '*.json' \) -print0 | \
  while IFS= read -r -d '' file; do mkdir -p "$CLONE/$(dirname "$file")"; cp "$file" "$CLONE/$file"; done)
[ -f "$CLONE/README.md" ] || cp "$HERE/cloud/DATASET.md" "$CLONE/README.md"

git -C "$CLONE" add -A
if git -C "$CLONE" diff --cached --quiet; then
  echo "Dataset backup: nothing new."; exit 0
fi
COUNT="$(git -C "$CLONE" diff --cached --numstat | wc -l | tr -d ' ')"
git -C "$CLONE" commit --quiet -m "Dataset backup $(date -u +%Y-%m-%dT%H:%MZ) ($COUNT files changed)"
git -C "$CLONE" push --quiet origin "$BRANCH"
echo "Dataset backup: pushed $COUNT changed files to $BRANCH."
