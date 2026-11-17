#!/bin/bash
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Build the libraries for an Intel Mac into lib/macos_x64. Blender 5.x publishes no Intel
# macOS libraries, so this compiles them from source with Blender's dependency builder,
# limited to what the video editor uses (build_files/vse/deps_vse.cmake).
#
# Takes several hours the first time. Running it again resumes where it stopped. Only needs
# re-running when library versions change (a Blender upgrade). Called by install_mac.command.
#
# Options (environment variables):
#   VSE_DEPS_BUILD_DIR=...   where to compile (default: ../latest-avenue-video-deps)

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DEPS_BUILD="${VSE_DEPS_BUILD_DIR:-$(dirname "$REPO")/latest-avenue-video-deps}"
LIBDIR="$REPO/lib/macos_x64"
LOG="$DEPS_BUILD/deps.log"

[ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "x86_64" ] || {
  echo "This script is for Intel Macs."; exit 1; }

# Same value as `make deps` in GNUmakefile, for reproducible archives.
export SOURCE_DATE_EPOCH=1745584760

mkdir -p "$DEPS_BUILD"
echo "Building libraries in $DEPS_BUILD (log: $LOG), installing to $LIBDIR"

# Blender's dependency builder is maintained with CMake 3.31 (macos/macos_setup.sh pins
# 3.31.6); CMake 4 refuses several libraries' older build files (e.g. ogg). Use a private
# copy of that version for the libraries only, from Kitware's package on PyPI.
CMAKE_WANTED=3.31.6
CMAKE_ENV="$DEPS_BUILD/cmake-$CMAKE_WANTED"
if [ ! -x "$CMAKE_ENV/bin/cmake" ]; then
  echo "Installing CMake $CMAKE_WANTED for the library build"
  python3 -m venv "$CMAKE_ENV"
  "$CMAKE_ENV/bin/pip" install --quiet "cmake==$CMAKE_WANTED"
fi
export PATH="$CMAKE_ENV/bin:$PATH"
cmake --version | head -1

# A build folder configured with another CMake has to be configured again (downloaded
# sources in packages/ and finished libraries are kept).
cache_version() {
  local f="$DEPS_BUILD/CMakeCache.txt" v=""
  for part in MAJOR MINOR PATCH; do
    v="$v.$(sed -n "s/^CMAKE_CACHE_${part}_VERSION:INTERNAL=//p" "$f")"
  done
  echo "${v#.}"
}
if [ -f "$DEPS_BUILD/CMakeCache.txt" ] && [ "$(cache_version)" != "$CMAKE_WANTED" ]; then
  echo "Build folder was configured with another CMake, configuring again"
  rm -rf "$DEPS_BUILD/CMakeCache.txt" "$DEPS_BUILD/CMakeFiles" "$DEPS_BUILD/build.ninja" "$DEPS_BUILD/build"
fi

if [ ! -f "$DEPS_BUILD/build.ninja" ]; then
  cmake -S "$REPO/build_files/build_environment" -B "$DEPS_BUILD" -G Ninja \
    -DVSE_DEPS_ONLY=ON \
    -DCMAKE_OSX_ARCHITECTURES=x86_64 \
    -DHARVEST_TARGET="$LIBDIR" 2>&1 | tee "$LOG"
  [ -f "$DEPS_BUILD/build.ninja" ] || { echo "Configuring the libraries failed. Log: $LOG"; exit 1; }
fi

if ! ninja -C "$DEPS_BUILD" install 2>&1 | tee -a "$LOG"; then
  echo "Building the libraries failed. Log: $LOG"; exit 1
fi

# Blender's build requires the library folder to be a git checkout (platform_apple.cmake).
if [ ! -d "$LIBDIR/.git" ]; then
  git -C "$LIBDIR" init -q
  git -C "$LIBDIR" -c user.name="local" -c user.email="local@localhost" commit -q --allow-empty \
    -m "Self-built Intel macOS libraries (build_files/vse/build_libs_macos_x64.sh)"
fi
echo "Libraries ready in $LIBDIR"
