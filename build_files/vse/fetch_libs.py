#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Fetch Blender's precompiled libraries for this platform, limited to what the video editor
needs (about 1.5 GB instead of 5+ GB for a full Blender checkout).

Works on Linux, macOS and Windows. Requires git and git-lfs.

Usage (from the repository root):

    python3 build_files/vse/fetch_libs.py              # current platform
    python3 build_files/vse/fetch_libs.py --platform windows_x64
"""

import argparse
import os
import platform
import re
import subprocess
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
LIB_URL = "https://projects.blender.org/blender/lib-{:s}.git"
SOURCE_LFS_URL = "https://projects.blender.org/blender/blender.git/info/lfs"

# Library folders the video editor build uses. Everything else (USD, OSL, Embree, OpenVDB,
# Alembic, MaterialX, OpenSubdiv, ...) is only needed for 3D and is skipped.
LIBS = (
    "brotli", "dbus", "deflate", "eigen", "epoxy", "expat", "ffmpeg", "fftw3", "fmt",
    "freetype", "fribidi", "harfbuzz", "imath", "jpeg", "libdeflate", "mesa", "openal",
    "opencolorio", "openexr", "openimageio", "openjph", "opentimelineio", "png", "pugixml",
    "python", "rubberband", "sdl", "shaderc", "sndfile", "tbb", "thorvg", "vulkan",
    "wayland", "wayland-protocols", "webp", "xml2", "zlib", "zstd",
    # Windows / macOS specific.
    "boost", "openjpeg", "pthreads", "sse2neon", "gmp", "abseil",
)


# Platforms without precompiled libraries (see BUILDING_VSE.md).
SELF_BUILT = {"macos_x64"}


def current_platform():
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    if sys.platform == "darwin":
        return "macos_" + arch
    if sys.platform == "win32":
        return "windows_" + arch
    return "linux_" + arch


def blender_version_tag():
    """The release tag matching this source tree, from BKE_blender_version.h."""
    path = os.path.join(ROOT, "source", "blender", "blenkernel", "BKE_blender_version.h")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    version = int(re.search(r"#define BLENDER_VERSION (\d+)", text).group(1))
    patch = int(re.search(r"#define BLENDER_VERSION_PATCH (\d+)", text).group(1))
    return "v{:d}.{:d}.{:d}".format(version // 100, version % 100, patch)


def run(cmd, cwd=None):
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=cwd)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--platform", default=None, help="e.g. linux_x64, macos_arm64, windows_x64")
    parser.add_argument("--tag", default=None, help="library tag (default: matches the source)")
    args = parser.parse_args()

    plat = args.platform or current_platform()
    tag = args.tag or blender_version_tag()
    dest = os.path.join(ROOT, "lib", plat)

    if plat in SELF_BUILT:
        # Blender 5.x publishes no Intel macOS libraries; build_libs_macos_x64.sh builds them.
        print("{:s}: libraries are built locally, fetching source-tree files only".format(plat))
    else:
        if not os.path.isdir(os.path.join(dest, ".git")):
            if os.path.isdir(dest) and os.listdir(dest):
                sys.exit("{:s} exists and is not a git checkout".format(dest))
            if os.path.isdir(dest):
                os.rmdir(dest)
            env_skip = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1")
            print("+ git clone", LIB_URL.format(plat), dest, flush=True)
            subprocess.check_call(
                ["git", "clone", "--depth", "1", "--branch", tag, LIB_URL.format(plat), dest],
                env=env_skip)

        run(["git", "lfs", "install", "--local"], cwd=dest)
        include = ",".join(name + "/**" for name in LIBS)
        run(["git", "lfs", "pull", "--include=" + include], cwd=dest)

    # Source-tree LFS files (startup.blend, icons, fonts, ...), excluding test data.
    # LFS has to be enabled in this repository too, not only in the library checkout above
    # (without it `git lfs pull` skips the checkout and leaves pointer files).
    run(["git", "lfs", "install", "--local"], cwd=ROOT)
    run(["git", "config", "lfs.url", SOURCE_LFS_URL], cwd=ROOT)
    run(["git", "lfs", "pull", "--exclude=tests/**"], cwd=ROOT)
    print("Libraries ready in", dest)


if __name__ == "__main__":
    main()
