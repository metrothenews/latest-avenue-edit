# latest avenue video

A standalone video editor for latest avenue, built from Blender 5.3's Video Sequence Editor
with everything 3D switched off. Internal tool. It adds LUT color grading, ripple editing, a
keyboard-driven "game view" for making editing decisions fast, and an automation layer so
Claude can drive every step through MCP while a person can step in at any time.

- What changed from Blender: [VSE_CHANGES.md](VSE_CHANGES.md)
- Building on every platform: [BUILDING_VSE.md](BUILDING_VSE.md)
- The reel workflow the game view is being built toward: [doc/vse/reel_workflow.md](doc/vse/reel_workflow.md)
- Blender's own README: [README_BLENDER.md](README_BLENDER.md)

## Install a finished version (no compiling)

Each version is on the repo's **Releases** page, one file per kind of computer:

| Computer | File |
|---|---|
| Mac with Apple Silicon (M1 or later) | `latest-avenue-video-macos-arm64.zip` |
| Mac with an Intel chip | `latest-avenue-video-macos-x64.zip` |
| Windows PC | `latest-avenue-video-windows-x64.zip` |

The newest one is always at
`https://github.com/metrothenews/latest-avenue-edit/releases/latest/download/<file>` (sign in to
GitHub with an account that can see this private repo), or from Terminal:

```bash
gh release download --repo metrothenews/latest-avenue-edit --pattern "*macos-x64*"
```

Mac: unzip, drag the app into Applications, and the first time right-click it > **Open** (it
is not signed with an Apple Developer ID yet). Windows: unzip anywhere and run `blender.exe`
(SmartScreen: **More info > Run anyway**). Needs macOS 13 or Windows 10 or newer.
The Mac app draws with Metal; each build is only known to work on the machine it was built
and checked on, so other models (especially other graphics chips) are untested until opened.
The repo is private: downloading needs a GitHub account with access, or send the zip.

## Install or update from source on a Mac

Needs Xcode (App Store; on an Intel Mac, Xcode 26.x, since Xcode 27 is Apple Silicon only) and
Homebrew. Once:

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 https://github.com/metrothenews/latest-avenue-edit ~/latest-avenue-edit
```

Then double-click `install_mac.command`. First run on Apple Silicon: about 1.5 GB of
downloads and 30–90 minutes of compiling. On an Intel Mac the libraries are compiled first
(several more hours, once). **To update, run it again** (a few minutes). The app runs offline.

**Development mode** (for changing the app's Python: game view, control server, web UI):

```bash
~/latest-avenue-edit/build_files/vse/run_dev_mac.sh
```

This runs a copy of the installed app (`../latest-avenue-video-dev`, an APFS clone, so it takes
no extra space) whose `scripts` folder is linked to the repository. Edit files under `scripts/`,
then quit and run the command again; no rebuild needed. Output goes to the Terminal and
`../latest-avenue-video-dev/dev.log`. C/C++ changes still need `install_mac.command`.

---

# Handoff: instructions for a Claude session running on the local machine

Read this whole section before doing anything. It was written by a cloud session (Linux,
no Mac available) for a later session that runs on Sam's own computer.

## Rules

- **Never mark something as working unless you ran it.** The status table below says what
  has and hasn't been run. Update it in the same commit as the work.
- Commit small, push to `main` on `metrothenews/latest-avenue-edit`. Push with
  `git push --no-verify` (see "Large files" below for why).
- The look follows latestavenue.com: dark, grey glass, white text, pill buttons, Inter, no
  saturated accent, no animation. The brand name is always lowercase: "latest avenue".
- Everything a person can do must also be a tool in `scripts/modules/vse_standalone/api.py`
  (explicit JSON arguments, strips addressed by name, one undo step per change). New tools
  appear in the MCP bridge automatically.
- Editing decisions are made by a person in the game view, not automated. Automation
  prepares options.
- Don't delete Blender code to "slim" the app. Features are switched off in
  `build_files/cmake/config/vse_standalone.cmake` and hidden in menus; that keeps upgrades to
  newer Blender versions a simple rebase.

## Status when this was written

| Item | State |
|---|---|
| Linux x64 build | **Builds, links and launches** (GCC 14, Ubuntu 24.04). |
| `tests/vse/test_vse.py` | **20 of 20 pass** on the Intel Mac (16 of 16 on Linux, before the four lyric timing / clip planning tests were added): video export and import, LUT pixel values, ripple delete / cut / delete range, split, trim, color pass, crop zoom. |
| `tests/vse/test_mcp_e2e.py` | **Passes** on Linux and on the Intel Mac (10 checks, the preview-frame one skipped by design with no clips): MCP client lists tools, edits, gets errors and rendered frames back; web UI routes respond. |
| Game view | Checked on Linux with simulated key presses on a virtual display (software rendering): preview, apply and advance, pass state, HUD drawing. **Not yet used by a person on real hardware.** |
| Theme | Applies with every property resolving. Only seen in screenshots; needs a visual pass against latestavenue.com. |
| Two-pane default layout | **Done**: game view left, timeline right, `F` full-screen; seen in screenshots on Linux. |
| Pane arrangement | **Works on the Intel Mac** (development mode): View > Arrangement in either pane switches between Side by Side and Stacked (preview on top, timeline below); remembered in `vse_layout.json` in the config folder. Tested by script in the GUI (switching both ways, restart); not yet checked by eye. |
| Lyric videos ("new style") | **Works on the Intel Mac**: `align_lyrics.py` times a lyric sheet against an audio clip and corrects misheard words to the sheet; `make_lyric_video.py` / the `make_lyric_video` tool builds and exports the 3:4 word-by-word video. Used for Assignment 17 (three IMPULSIVE videos, 1080×1920, 30 fps); frames checked by eye by Claude, timing cross-checked with two recognizer models, not yet listened to by a person. See `doc/vse/lyric_video_workflow.md`. |
| Startup | No Blender splash. On a machine without a working Vulkan driver a "Failed to load using Vulkan, using OpenGL" notice appears (seen on the GPU-less cloud machine); expected to be absent on real hardware, unverified. |
| macOS Apple Silicon build, `install_mac.command` | **Never run on an Apple Silicon Mac.** |
| macOS Intel build | **Builds, installs and launches** on an Intel Mac (i9-9880H, macOS 26.7.3, Xcode 26.5) via `install_mac.command`, libraries self-built. No compile errors in our C++ under Apple Clang 21. The control server answers at 127.0.0.1:8792 after launch. Checked by eye on the Intel Mac by Sam ("looks great"): the list asked about was no splash, the "la" Dock icon, the two-pane layout, the game view HUD and the Automation panel showing the server URL. |
| Windows build | Never attempted. No install script yet (see "Building on each kind of computer"). |
| Releases | **v0.1.0 published** (2026-11-17) with the Intel Mac file only (`latest-avenue-video-macos-x64.zip`, built and checked on the Intel Mac; the `releases/latest/download/` link works). Published early on purpose: the Apple Silicon and Windows links give an error until those files are uploaded to v0.1.0 with `gh release upload`. |
| App icon | Made and wired in; not seen in a built Mac or Windows app. |
| Cloud editor (`cloud/`) | **Works in Docker on Linux, never deployed.** The app runs with no screen in a container and is streamed to the site's `/edit` page behind the owner's Firebase sign-in. Image builds; gateway security test passes (38 checks); page tested in a browser against the container with test certificates. `cloud/deploy.sh` (Cloud Run) has not been run: no Google Cloud access where it was built. See [cloud/README.md](cloud/README.md). |
| PC as the server (`cloud/pc/`) | **Container tested on Linux; the Windows installer has never run.** `cloud/pc/install.ps1` runs the cloud editor's container in Docker Desktop on a Windows PC, reachable through a tunnel, so the site's `/edit` page uses the PC's compute. The container was run the way the script runs it (local-only port, host data folder, restart) and passes the security and restart tests; the PowerShell script, Docker Desktop on Windows and Tailscale Funnel are untested. See [cloud/pc/README.md](cloud/pc/README.md). |
| Web version of the game UI (`ui/`) | Routes tested; the page itself not opened in a browser against the editor. |

If a later commit message or this table says otherwise, trust the later one.

## Step 1: get it compiling and launching on this machine

Follow the part of "Building on each kind of computer" below for the machine you are on, and
fix what breaks.

Likely first failures and where to look:

- **Compile errors in our C++** (it compiles with GCC 14 on Linux; Clang or MSVC may be stricter): `source/blender/sequencer/intern/lut.cc`,
  `source/blender/sequencer/intern/modifiers/MOD_lut.cc`,
  `source/blender/editors/space_sequencer/sequencer_edit.cc` (search "VSE Standalone"),
  `source/blender/makesrna/intern/rna_screen.cc`, `rna_sequencer.cc` (search `LUT`).
- **`makesdna` errors**: `LUTModifierData` in `source/blender/makesdna/DNA_sequence_types.h`
  must stay 8-byte aligned with no implicit padding.
- **macOS icon**: `Info.plist` now points at `la.icns` and no longer names the Liquid Glass
  icon. If the Dock still shows Blender's icon on macOS 26, delete `Assets.car` from the
  installed app's `Contents/Resources` in `install_mac.command`, or regenerate it in Xcode.
- **Configure errors about a missing library**: add its folder name to `LIBS` in
  `build_files/vse/fetch_libs.py` and run that script again.

## Building on each kind of computer

Three builds are wanted: Apple Silicon Mac, Intel Mac and Windows x64, each built on a
machine of that kind (one build can't be made for another). Each one ends in a zip on the
same GitHub release (see "Releasing a version"). Several machines can work at once: they all
push to `main`, so `git pull --rebase` before every push, and keep commits small so the
status table merges cleanly.

Before starting on any machine, check prerequisites and say plainly what is missing rather
than working around it.

### Apple Silicon Mac (easiest)

1. Xcode from the App Store, opened once (license accepted): `xcodebuild -version` works.
   Homebrew installed. About 15 GB free.
2. Clone as in "Install or update from source on a Mac", then run `./install_mac.command`
   from Terminal in the background and check on it (first run 30–90 minutes).
3. On a failure, read the log path it prints, fix the cause in our code (map at the end),
   run it again: it resumes. Apple Clang is stricter than GCC; fixes found on the Intel Mac
   apply here too, so `git pull --rebase` first.
4. Run the tests (Step 2) and check by hand. Package as `latest-avenue-video-macos-arm64.zip`.

### Intel Mac (slowest: libraries are compiled locally)

Blender 5.x publishes no Intel Mac libraries, so `install_mac.command` compiles them first
with `build_files/vse/build_libs_macos_x64.sh`, limited to what the video editor uses
(`build_files/vse/deps_vse.cmake`). That takes several hours once; they go to
`lib/macos_x64` and the build folder `~/latest-avenue-video-deps`. Keep both: they only need
rebuilding when the library versions change (a Blender upgrade). Learned on the first Intel
Mac:

- **Xcode:** the App Store's Xcode 27 is Apple Silicon only. Download Xcode 26.x (universal)
  from <https://developer.apple.com/download/all/>, then unpack with
  `cd /Applications && xip --expand ~/Downloads/Xcode_26*.xip` and run
  `sudo xcodebuild -license accept`. `install_mac.command` uses `/Applications/Xcode.app`
  even when `xcode-select` still points at the Command Line Tools.
- **Command Line Tools must be current too** (Homebrew refuses to build otherwise, and it
  builds everything from source on Intel):
  `sudo softwareupdate --install "Command Line Tools for Xcode 26.6-26.6"` (use the newest
  label `softwareupdate --list` shows).
- **Don't `brew install git-lfs` on Intel:** Homebrew compiles LLVM and Rust for it (hours).
  Install the official binary from <https://github.com/git-lfs/git-lfs/releases> instead
  (`git-lfs-darwin-amd64-*.zip`, check it against `sha256sums.asc`, copy `git-lfs` to
  `/usr/local/bin`). `meson` from Homebrew isn't needed either.
- If Homebrew stops with "directories are not writable", the folder it names can be
  replaced by a copy you own (`mv dir dir.root-owned && mkdir dir && cp -pR dir.root-owned/. dir/`)
  instead of using `sudo chown`.

Then as for Apple Silicon from step 2. Package as `latest-avenue-video-macos-x64.zip`.

### Windows x64 (never attempted)

There is no install script for Windows yet; writing one (`install_windows.bat` next to
`install_mac.command`, same steps and options) is part of this job.

1. Prerequisites: Visual Studio 2022 with "Desktop development with C++", Git for Windows,
   Git LFS (included with Git for Windows), CMake, Python 3, about 20 GB free. Check whether
   the PC is x64 or ARM64 (`echo %PROCESSOR_ARCHITECTURE%`); the libraries differ.
2. Clone with `GIT_LFS_SKIP_SMUDGE=1` as above (`set GIT_LFS_SKIP_SMUDGE=1` in cmd), then
   `python build_files\vse\fetch_libs.py` (fetches `lib/windows_x64`, about 1.5 GB).
3. Build as in [BUILDING_VSE.md](BUILDING_VSE.md) section 3 from an "x64 Native Tools Command
   Prompt for VS 2022", with `-C build_files\cmake\config\vse_standalone.cmake`.
4. Expect new compile errors from MSVC in our C++ (it differs from both GCC and Clang), and
   Windows issues in the Python layer: file paths, launching processes and the control
   server in `tests/vse/test_mcp_e2e.py` and `tools/vse_mcp/` have only run on Linux.
5. Run the tests (Step 2; the binary is `bin\blender.exe`) and check by hand. Package the
   `bin` folder as `latest-avenue-video-windows-x64.zip`.

## Step 2: run the tests and fix the Python layer

From the build folder (on macOS the binary is `bin/Blender.app/Contents/MacOS/Blender`):

```bash
bin/blender -b --factory-startup --python ../latest-avenue-edit/tests/vse/test_vse.py
```

```bash
python3 ../latest-avenue-edit/tests/vse/test_mcp_e2e.py bin/blender
```

`test_vse.py` makes its own media and checks: video export and import, LUT pixel values,
ripple delete / cut / delete range, the color pass, crop zoom. Fix the code until both pass.
Then launch the app normally and check by hand, in this order:

1. It opens in the Video Editing layout, no 3D editors in the editor-type menu.
2. Add a clip, add a **LUT** modifier (strip Modifiers tab), pick a `.cube` from
   `scripts/modules/vse_standalone/luts/`.
3. **Game view**: mouse over the preview pane, keys `1`–`4` `7`–`0` preview LUTs, `space`
   applies and advances, `tab` switches pass, `F` full-screen, `esc` leaves. Check the HUD
   draws correctly (`scripts/modules/vse_standalone/game_view.py`).
4. The theme (`scripts/modules/vse_standalone/theme.py`): `theme.apply(report=True)` in the
   Python console prints theme property names that don't exist in this build; fix those.
5. Automation panel in the sequencer sidebar shows the server URL; connect Claude with
   `claude mcp add video-editor -- python3 <repo>/tools/vse_mcp/vse_mcp.py`.

## Step 3: remaining work, in order

1. **Game view polish.** The two-pane default layout is in (it is the Video Editing
   template's `startup.blend`, exempted from LFS in `.gitattributes`). Still to do: the
   picture sits behind the top bar and the LUT keys instead of being fitted between them;
   the timeline pane opens zoomed in instead of showing the whole edit; the sidebar with
   strip properties is hidden by default.
2. **Own settings folder.** The app still uses Blender's config folder
   (`~/Library/Application Support/Blender/5.3` on macOS), so it shares preferences with a
   real Blender 5.3 on the same machine. Change the folder name in
   `source/blender/blenkernel/intern/appdir.cc` and the bundle identifier in
   `release/darwin/Blender.app/Contents/Info.plist`.
3. **Rename visible "Blender" strings** (window title, splash, About, menus).
4. **Crop as a game-view pass** (it is a field in the Automation panel for now).
5. **The reel workflow passes** in [doc/vse/reel_workflow.md](doc/vse/reel_workflow.md),
   starting with a generic pass framework in `game_view.py`, then VO, shot, cut, caption,
   music and SFX passes.
6. **Intel Mac and Windows builds** (see BUILDING_VSE.md).

## Releasing a version (so other machines can install without compiling)

Versions are `MAJOR.MINOR.PATCH` in the `VSE_VERSION` file, tagged `v<version>`.
An installer has to be built **on the kind of machine it is for**; each machine that builds
uploads its file to the same GitHub release.

1. All tests pass on this machine. Bump `VSE_VERSION`, update the status table, commit.
2. Tag and push:

```bash
git tag "v$(cat VSE_VERSION)" && git push --no-verify origin main --tags
```

3. Package the build from this machine. File names carry no version number, so the
   `releases/latest/download/<file>` links in "Install a finished version" always point at
   the newest one (the version is in the release's tag and title):

   - **Mac (either chip)**: `build_files/vse/package_mac.sh` makes
     `latest-avenue-video-macos-arm64.zip` or `-macos-x64.zip` in the current folder. Don't
     zip `/Applications/latest avenue video.app` directly: running the app writes Python
     caches into it, which breaks its signature, and another Mac then calls the download
     "damaged". The script packages a cleaned, re-signed copy and checks the zip.
     The app is only ad-hoc signed: on another Mac, right-click > Open the first time (or
     `xattr -dr com.apple.quarantine` on the app). Proper signing and notarization need an
     Apple Developer ID and are not set up.
   - **Windows**: zip the `bin` folder of the build as `latest-avenue-video-windows-x64.zip`.
   - **Linux**: `tar -cJf latest-avenue-video-linux-x64.tar.xz -C <build> bin`
     (build on Rocky Linux 8 for it to run on every distribution).

4. The first machine creates the release as a **draft**, so the "latest" links keep pointing
   at the previous complete release meanwhile; each other machine adds its file:

```bash
gh release create "v$(cat VSE_VERSION)" latest-avenue-video-*.zip --draft --title "v$(cat VSE_VERSION)" --notes "what changed"
```

```bash
gh release upload "v$(cat VSE_VERSION)" latest-avenue-video-*.zip
```

5. When all three files (Apple Silicon, Intel, Windows) are attached, publish it:

```bash
gh release edit "v$(cat VSE_VERSION)" --draft=false
```

A later improvement: most updates only change the Python layer (`scripts/modules/vse_standalone/`,
`scripts/startup/`), which needs no compiling. The app could check the Releases page on
launch and fetch just those files, with a full download only when the engine changed.

## Build times, updates and decisions

Background for planning, from the first Intel Mac build. Times are estimates unless marked
as measured; replace them with measured ones as machines finish. The Intel Mac compiled far
faster than estimated, so the other estimates are likely high too.

**Why compiling takes hours when installing Blender doesn't.** An installed Blender is
already compiled by Blender's build servers. This app is a modified Blender with no
published build, so the first machine of each kind compiles it from source (millions of
lines of C/C++). Blender publishes its ~40 support libraries (codecs, Python, image formats)
precompiled for Apple Silicon, Windows and Linux, but not for Intel Macs since Blender 5.0,
so an Intel Mac also compiles those.

| Machine | Libraries | App | First build in all |
|---|---|---|---|
| Apple Silicon Mac | downloaded (~1.5 GB) | 30–90 min | about 1–2 hours |
| Intel Mac (8-core i9) | compiled: **~45 min measured** | **~25 min measured** | **~70 min of compiling measured**, plus tool installs and fixes (a few hours in all the first time) |
| Windows x64 | downloaded (~1.5 GB) | 1–2 hours | about half a day the first time (no script yet, MSVC fixes) |

**What needs recompiling after a change.** The long build happens once per machine, as long
as the build folders are kept (`~/latest-avenue-video-build`, and on Intel `lib/macos_x64`
and `~/latest-avenue-video-deps`; together roughly 15–25 GB).

- Python layer (game view, automation tools, control server, theme, color pass, LUTs,
  Automation panel, MCP bridge): no compiling. Running the installer again copies the files,
  seconds to a minute or two.
- Engine C++ (LUT modifier, ripple operators, menus): only the changed files recompile, a
  few minutes.
- Hours again only for: a newer Blender release (libraries too, if their versions changed),
  deleting the build folders (`VSE_CLEAN=1` deletes the app build folder, not the
  libraries), or the first build on a new kind of machine.

Other people's machines never compile: they download the zip for their kind of computer from
Releases. Each new version means a new zip per platform (built on each machine, minutes once
its build folders exist) until the app can fetch Python-only updates itself (see "Releasing
a version").

**Why this stays on Blender 5.3 rather than moving to 4.5** (4.5 is the last Blender with
Intel Mac libraries, so it would skip the Intel library build). Decided against because:

- It saves only the library build. The app compile takes as long on 4.5.
- Our engine code targets 5.x internals: the LUT modifier uses the per-file strip modifier
  layout (`sequencer/intern/modifiers/`) and `api.py` uses `sequencer_scene`, both newer
  than 4.5 as far as we know. Porting means rewriting and re-testing them, which needs
  attention, where the library build runs unattended.
- It loses the video editor improvements of Blender 5.0–5.3, and turns the upgrade plan
  (rebase onto each new Blender) backwards: moving forward later means porting again.
- 4.5's long-term support ends around mid-2027 (from memory, unverified), and Apple ends
  Intel support with macOS 27, so the Intel runway is limited either way.

4.5 would run on Apple Silicon and Windows as well; that wasn't the concern.

## Large files

Blender keeps big binaries (startup file, icons, fonts, test data) in Git LFS on
**Blender's** server; this repo only holds pointers, and `.lfsconfig` sends LFS downloads
there. Consequences:

- Clone with `GIT_LFS_SKIP_SMUDGE=1` (otherwise git downloads gigabytes of test data), then
  run `python3 build_files/vse/fetch_libs.py`, which fetches only what the build needs.
- Push with `--no-verify`, which skips the LFS upload step (we have nothing to upload, and
  can't upload to Blender's server).
- **Don't add new files that match an LFS pattern** in `.gitattributes` (`*.png`, `*.blend`,
  `*.icns`, …) unless their path is exempted at the bottom of `.gitattributes`, as
  `release/vse/**` and `scripts/modules/vse_standalone/**` are. Otherwise only a pointer is
  pushed and the file is lost. The edited Video Editing `startup.blend` is exempted this way; do the same for any
  new file like it.

## Map of the code we own

| Path | What |
|---|---|
| `build_files/cmake/config/vse_standalone.cmake` | Which Blender features are built, per platform |
| `build_files/vse/fetch_libs.py` | Downloads precompiled libraries for this platform |
| `build_files/vse/run_dev_mac.sh` | Development mode on a Mac: runs the app with `scripts/` from the repo |
| `install_mac.command` | Mac install and update |
| `scripts/modules/vse_standalone/api.py` | Automation tools |
| `scripts/modules/vse_standalone/server.py` | Local control server, 127.0.0.1:8792 |
| `scripts/modules/vse_standalone/game_view.py` | Game view pane |
| `scripts/modules/vse_standalone/layout.py` | Pane arrangement (side by side / stacked) |
| `scripts/modules/vse_standalone/lyric_video.py` | Lyric video tools: 3:4 picture clips, word-by-word lyric text, `make_lyric_video` |
| `tools/vse_lyrics/` | Lyric timing from audio (`align_lyrics.py`, with `separate_vocals.py` and `forced_align.py` for precision), and the command-line lyric video builder with subject-based clip planning (`make_lyric_video.py`); workflow in `doc/vse/lyric_video_workflow.md` |
| `scripts/modules/vse_standalone/color_pass.py`, `modes/`, `luts/` | Look/Color pass engine, night mode |
| `scripts/modules/vse_standalone/theme.py` | Theme from latestavenue.com tokens |
| `scripts/startup/vse_standalone_startup.py` | Starts everything; Automation panel |
| `tools/vse_mcp/vse_mcp.py` | MCP bridge (Python standard library only) |
| `tests/vse/` | Tests |
| `cloud/` | Cloud editor: gateway (sign-in check, stream relay), container image, Cloud Run deploy script |
| `release/vse/icon/` | App icon sources (`la_1024.png`, `.icns`, `.ico`) |
