# Building latest avenue video

A standalone video editor built from Blender's Video Sequence Editor, with everything 3D
switched off at build time. This file covers building it on every platform.

| Platform | Status | Libraries |
|---|---|---|
| Linux x64 | Built and tested | Blender's precompiled `lib/linux_x64` |
| Linux arm64 | Should work, not yet built | Blender's precompiled `lib/linux_arm64` |
| macOS Apple Silicon | Should work, not yet built | Blender's precompiled `lib/macos_arm64` |
| Windows x64 / arm64 | Should work, not yet built | Blender's precompiled `lib/windows_x64` / `windows_arm64` |
| macOS Intel | Build in progress (libraries not yet built) | Self-built `lib/macos_x64` (section 5) |

Everything we added on top of Blender is either C++ inside Blender's existing modules (built
the same way on every platform) or Python using only the standard library, so the platforms
differ only in toolchain and libraries.

## Mac: install and update in one step

On an Apple Silicon Mac with Xcode and Homebrew installed:

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 https://github.com/metrothenews/latest-avenue-edit ~/latest-avenue-edit
```

Then double-click `install_mac.command` in that folder (or run it in Terminal). It installs
the build tools, downloads the libraries, compiles, and puts **latest avenue video** in
`/Applications`. **To update, run it again:** it pulls the latest code, rebuilds only what
changed, and replaces the app. Once installed, the app runs offline.

## 1. Requirements

| | Linux | macOS | Windows |
|---|---|---|---|
| Compiler | GCC 14+ (or Clang 17+) | Xcode 16+ | Visual Studio 2022 (C++ workload) |
| Tools | CMake 3.10+, Ninja, git, git-lfs, Python 3 | same (`brew install cmake ninja git-lfs`) | CMake, git, git-lfs, Python 3 |
| Disk | ~12 GB (source + libs + build) | same | same |

Linux also needs the windowing and audio headers, e.g. on Ubuntu/Debian:

```bash
sudo apt install libx11-dev libxxf86vm-dev libxcursor-dev libxi-dev libxrandr-dev libxinerama-dev libxkbcommon-dev libxfixes-dev libegl-dev libgl-dev libwayland-dev wayland-protocols libdecor-0-dev libdbus-1-dev libpulse-dev libpipewire-0.3-dev
```

## 2. Get the libraries

From the repository root, on the machine you are building on:

```bash
python3 build_files/vse/fetch_libs.py
```

This fetches Blender's precompiled libraries for your platform into `lib/<platform>`, only the
ones the video editor needs (about 1.5 GB instead of 5+ GB). It also fetches the source
tree's large files (startup file, icons, fonts) from Blender's server.

## 3. Configure and build

Linux / macOS:

```bash
mkdir ../build_vse && cd ../build_vse
cmake -G Ninja -C ../blender/build_files/cmake/config/vse_standalone.cmake -DCMAKE_BUILD_TYPE=Release ../blender
ninja install
```

On Ubuntu 24.04 the default GCC is 13: install `gcc-14 g++-14` and prefix the cmake line with
`CC=gcc-14 CXX=g++-14`.

Windows (from a "x64 Native Tools Command Prompt for VS 2022"):

```bat
mkdir ..\build_vse && cd ..\build_vse
cmake -G Ninja -C ..\blender\build_files\cmake\config\vse_standalone.cmake -DCMAKE_BUILD_TYPE=Release ..\blender
ninja install
```

The app ends up in `build_vse/bin/`. On 4 cores a clean build takes about 1.5 hours; rebuilds
after small changes take a minute or two.

`build_files/cmake/config/vse_standalone.cmake` is the single place where features are
switched on or off, with per-platform sections (Metal + CoreAudio on macOS, WASAPI on
Windows, X11 + Wayland + PulseAudio/PipeWire on Linux).

## 4. Test

```bash
bin/blender -b --factory-startup --python ../blender/tests/vse/test_vse.py
python3 ../blender/tests/vse/test_mcp_e2e.py bin/blender
```

The first runs the editing features headless (it makes its own test media). The second
starts the editor with its control server and drives it through the MCP bridge.

## 5. macOS Intel

Blender 5.x no longer publishes Intel macOS libraries, and the last ones (Blender 4.5) are a
generation behind what this source needs (Python 3.11 vs 3.13, FFmpeg 7 vs 8), so they
can't be used without patching the engine. Instead the same library versions are built from
source on the Intel Mac with Blender's own dependency builder, limited to what the video
editor uses. `install_mac.command` does this on its own the first time (several hours);
to run only that step:

```bash
build_files/vse/build_libs_macos_x64.sh
```

It configures `build_files/build_environment` with `-DVSE_DEPS_ONLY=ON`, which skips the
libraries listed in `build_files/vse/deps_vse.cmake` (3D, rendering, simulation and other
platforms' GPU libraries; keep that list in step with `vse_standalone.cmake`). It compiles in
`../latest-avenue-video-deps` and installs to `lib/macos_x64`; running it again resumes. Both
only need rebuilding when the library versions change (a Blender upgrade). Tool and Xcode
notes from the first Intel build are in README.md, "Building on each kind of computer".

## 6. Linux on any distribution

A build made on a new distribution needs that distribution's C library (glibc) or newer to
run. Two ways to cover every distribution:

1. **Build on Rocky Linux 8** (glibc 2.28, what Blender's own Linux releases use), e.g. in a
   container. The precompiled libraries are already built for it.
2. **Flatpak**, as a fallback when a system lacks GPU or audio libraries: the runtime
   brings its own.

## 7. Packaging

`ninja install` produces a portable folder (`WITH_INSTALL_PORTABLE`) that runs from anywhere.
`make package` (Linux/macOS) or the CPack targets (Windows) produce a `.tar.xz`, `.dmg` or
`.zip`. Code signing and notarization for macOS and Windows are not set up yet.

## What was changed from Blender

See `VSE_CHANGES.md`.
