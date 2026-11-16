#!/bin/bash
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Install or update "latest avenue video" on a Mac. Double-click this file, or run it in
# Terminal. Running it again updates the app: it pulls the latest code, rebuilds only what
# changed (a few minutes) and replaces the app in /Applications.
#
# The first run downloads about 1.5 GB of libraries and compiles for 30-90 minutes.
# After that the app runs fully offline.
#
# Options (environment variables):
#   VSE_NO_PULL=1     do not pull new code, just rebuild what is here
#   VSE_APP_DIR=...   install somewhere other than /Applications
#   VSE_CLEAN=1       delete the build folder first (full rebuild)

set -euo pipefail

APP_NAME="latest avenue video"
REPO="$(cd "$(dirname "$0")" && pwd)"
BUILD="${VSE_BUILD_DIR:-$(dirname "$REPO")/latest-avenue-video-build}"
APP_DIR="${VSE_APP_DIR:-/Applications}"
LOG="$BUILD/install.log"

say_step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31mStopped: %s\033[0m\n' "$1"; exit 1; }

cd "$REPO"

# ---------------------------------------------------------------------------
say_step "Checking this Mac"

[ "$(uname -s)" = "Darwin" ] || fail "this installer is for macOS."
ARCH="$(uname -m)"
if [ "$ARCH" != "arm64" ]; then
  fail "this Mac has an Intel chip. Blender no longer publishes Intel Mac libraries, so they have to be built first (several hours). See section 5 of BUILDING_VSE.md."
fi

if ! xcodebuild -version >/dev/null 2>&1; then
  fail "Xcode is needed to compile the app. Install Xcode from the App Store, open it once to accept the license, then run this again."
fi
echo "Xcode: $(xcodebuild -version | head -1)"

if ! command -v brew >/dev/null 2>&1; then
  # Homebrew may be installed but not on PATH for a double-clicked script.
  [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
fi
command -v brew >/dev/null 2>&1 || fail "Homebrew is needed for the build tools. Install it from https://brew.sh, then run this again."

MISSING=""
for tool in cmake ninja git-lfs; do
  command -v "$tool" >/dev/null 2>&1 || MISSING="$MISSING $tool"
done
if [ -n "$MISSING" ]; then
  say_step "Installing build tools:$MISSING"
  # shellcheck disable=SC2086
  brew install $MISSING
fi

FREE_GB=$(df -g "$REPO" | awk 'NR==2 {print $4}')
[ "$FREE_GB" -ge 15 ] || fail "about 15 GB of free disk space is needed, this disk has ${FREE_GB} GB."

# ---------------------------------------------------------------------------
if [ "${VSE_NO_PULL:-0}" != "1" ]; then
  say_step "Getting the latest code"
  if [ -n "$(git status --porcelain)" ]; then
    echo "You have local changes, keeping them and skipping the update of the code."
  else
    BEFORE="$(git rev-parse HEAD)"
    # Large files come from Blender's server via fetch_libs.py, not on pull.
    GIT_LFS_SKIP_SMUDGE=1 git pull --ff-only || echo "Could not pull (offline?), building the code that is here."
    AFTER="$(git rev-parse HEAD)"
    if [ "$BEFORE" = "$AFTER" ]; then
      echo "Already up to date ($(git rev-parse --short HEAD))."
    else
      echo "Updated $(git rev-parse --short "$BEFORE") -> $(git rev-parse --short "$AFTER"):"
      git log --oneline "$BEFORE..$AFTER" | head -20
    fi
  fi
fi

# ---------------------------------------------------------------------------
if [ -z "$(find lib/macos_arm64/ffmpeg -name 'libavcodec.a' -size +1000k 2>/dev/null)" ] || \
   [ "$(wc -c < release/datafiles/startup.blend)" -lt 1024 ]; then
  say_step "Downloading libraries (about 1.5 GB, first run only)"
  python3 build_files/vse/fetch_libs.py --platform macos_arm64
fi

# ---------------------------------------------------------------------------
[ "${VSE_CLEAN:-0}" = "1" ] && rm -rf "$BUILD"
mkdir -p "$BUILD"

if [ ! -f "$BUILD/build.ninja" ]; then
  say_step "Configuring"
  cmake -S "$REPO" -B "$BUILD" -G Ninja \
    -C "$REPO/build_files/cmake/config/vse_standalone.cmake" \
    -DCMAKE_BUILD_TYPE=Release 2>&1 | tee "$LOG" | grep -E "^CMake (Error|Warning)|Configuring done|Generating done" || true
  [ -f "$BUILD/build.ninja" ] || fail "configuring failed. Full log: $LOG"
fi

say_step "Compiling (first time: 30-90 minutes; updates: a few minutes)"
if ! ninja -C "$BUILD" install 2>&1 | tee -a "$LOG" | grep --line-buffered -E "^\[[0-9]+/[0-9]+\]|error|FAILED" | awk '
    /^\[[0-9]+\/[0-9]+\]/ { n++; if (n % 50 == 1) { split($1, a, /[\[\]\/]/); printf "\r  %s of %s steps", a[2], a[3]; fflush() } next }
    { print "\n" $0 }'; then
  fail "compiling failed. Send Claude the last lines of: $LOG"
fi
echo

BUILT="$BUILD/bin/Blender.app"
[ -d "$BUILT" ] || fail "the build finished but $BUILT is missing. Log: $LOG"

# ---------------------------------------------------------------------------
say_step "Installing to $APP_DIR"
DEST="$APP_DIR/$APP_NAME.app"
if pgrep -f "$DEST/Contents/MacOS" >/dev/null 2>&1; then
  fail "\"$APP_NAME\" is running. Quit it and run this again (the build is kept, this will take seconds)."
fi
mkdir -p "$APP_DIR"
rsync -a --delete "$BUILT/" "$DEST/"
/usr/libexec/PlistBuddy -c "Set :CFBundleName $APP_NAME" "$DEST/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName $APP_NAME" "$DEST/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string $APP_NAME" "$DEST/Contents/Info.plist" 2>/dev/null || true
git rev-parse --short HEAD > "$DEST/Contents/Resources/vse_version.txt"
# Changing Info.plist invalidates the build's ad-hoc signature; sign again for this Mac.
codesign --force --deep --sign - "$DEST" >/dev/null 2>&1 || true

printf '\n\033[1mDone.\033[0m "%s" (%s) is in %s.\n' "$APP_NAME" "$(git rev-parse --short HEAD)" "$APP_DIR"
echo "To update later, run this file again."
open -R "$DEST" 2>/dev/null || true
