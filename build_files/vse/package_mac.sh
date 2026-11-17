#!/bin/bash
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Package the installed Mac app as a release zip: latest-avenue-video-macos-<arch>.zip.
#
# Running the app writes Python caches (__pycache__/*.pyc) into the bundle, which breaks its
# ad-hoc signature; on another Mac a downloaded app with a broken signature is reported as
# "damaged". So this packages a copy with the caches removed, signed again and verified.
#
# Usage: build_files/vse/package_mac.sh [output folder]   (default: current folder)

set -euo pipefail

APP="${VSE_APP:-/Applications/latest avenue video.app}"
OUT="$(cd "${1:-.}" && pwd)"
case "$(uname -m)" in
  arm64) ARCH=arm64 ;;
  x86_64) ARCH=x64 ;;
  *) echo "Unknown processor $(uname -m)"; exit 1 ;;
esac
ZIP="$OUT/latest-avenue-video-macos-$ARCH.zip"

[ -d "$APP" ] || { echo "No app at $APP (run install_mac.command first)"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
COPY="$TMP/$(basename "$APP")"

ditto "$APP" "$COPY"
find "$COPY" -type d -name __pycache__ -prune -exec rm -rf {} +
xattr -cr "$COPY"
codesign --force --deep --sign - "$COPY"
codesign --verify --deep --strict "$COPY"

rm -f "$ZIP"
ditto -c -k --keepParent "$COPY" "$ZIP"

# Check the zip as a downloader gets it: unpacks, signature intact, starts.
CHECK="$TMP/check"
mkdir "$CHECK"
ditto -x -k "$ZIP" "$CHECK"
codesign --verify --deep --strict "$CHECK/$(basename "$APP")"
"$CHECK/$(basename "$APP")/Contents/MacOS/Blender" -b --factory-startup \
  --python-expr "import bpy; print('PACKAGE_OK', bpy.app.version_string)" 2>/dev/null | grep -q PACKAGE_OK

echo "$ZIP ($(du -h "$ZIP" | cut -f1)), sha256 $(shasum -a 256 "$ZIP" | cut -d' ' -f1)"
