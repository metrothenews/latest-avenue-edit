#!/bin/bash
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Development mode on a Mac: run the app with all of its Python (scripts/, including the
# game view, control server and web UI) loaded straight from this repository, so edits to
# those files only need a restart of the app, not a rebuild.
#
# Makes a development copy of the installed app once, as an APFS clone (takes no extra disk
# space), with its scripts folder linked to the repository. The installed app in
# /Applications is not changed. C/C++ changes still need install_mac.command; run this
# again afterwards and the copy is refreshed.
#
# Usage: build_files/vse/run_dev_mac.sh [extra Blender arguments, e.g. --debug-python]
# Output goes to this Terminal and to ../latest-avenue-video-dev/dev.log.
#
# Options (environment variables):
#   VSE_APP=...      app to copy (default: /Applications/latest avenue video.app)
#   VSE_PORT=<n>     control server port (default 8792)

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${VSE_APP:-/Applications/latest avenue video.app}"
DEV_DIR="$(dirname "$REPO")/latest-avenue-video-dev"
DEV_APP="$DEV_DIR/latest avenue video dev.app"
LOG="$DEV_DIR/dev.log"

[ -d "$APP" ] || { echo "No app at $APP (run install_mac.command first)"; exit 1; }
VERSION_DIR="$(cd "$APP/Contents/Resources" && ls -d [0-9]*.[0-9]* | head -1)"

# (Re)make the copy when there is none or the installed app is newer.
if [ ! -d "$DEV_APP" ] || [ "$APP/Contents/MacOS/Blender" -nt "$DEV_APP/Contents/MacOS/Blender" ]; then
  echo "Making the development copy of $(basename "$APP")"
  rm -rf "$DEV_APP"
  mkdir -p "$DEV_DIR"
  cp -cR "$APP" "$DEV_APP" 2>/dev/null || ditto "$APP" "$DEV_APP"
  rm -rf "$DEV_APP/Contents/Resources/$VERSION_DIR/scripts"
  ln -s "$REPO/scripts" "$DEV_APP/Contents/Resources/$VERSION_DIR/scripts"
  /usr/libexec/PlistBuddy -c "Set :CFBundleName latest avenue video dev" "$DEV_APP/Contents/Info.plist" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName latest avenue video dev" "$DEV_APP/Contents/Info.plist" 2>/dev/null || true
fi

if pgrep -f "latest avenue video.*/Contents/MacOS/Blender" >/dev/null 2>&1; then
  echo "Another copy of the app is running; quit it first (both would use port ${VSE_PORT:-8792})."
  exit 1
fi

echo "Development mode: scripts from $REPO/scripts (built from $(cat "$APP/Contents/Resources/vse_version.txt" 2>/dev/null || echo '?'), repo at $(git -C "$REPO" rev-parse --short HEAD))"
echo "Log: $LOG"
"$DEV_APP/Contents/MacOS/Blender" "$@" 2>&1 | tee "$LOG"
