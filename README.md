# latest avenue video

A standalone video editor for latest avenue, built from Blender 5.3's Video Sequence Editor
with everything 3D switched off. Internal tool. It adds LUT color grading, ripple editing, a
keyboard-driven "game view" for making editing decisions fast, and an automation layer so
Claude can drive every step through MCP while a person can step in at any time.

- What changed from Blender: [VSE_CHANGES.md](VSE_CHANGES.md)
- Building on every platform: [BUILDING_VSE.md](BUILDING_VSE.md)
- The reel workflow the game view is being built toward: [doc/vse/reel_workflow.md](doc/vse/reel_workflow.md)
- Blender's own README: [README_BLENDER.md](README_BLENDER.md)

## Install or update on a Mac (Apple Silicon)

Needs Xcode (App Store) and Homebrew. Once:

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 https://github.com/metrothenews/latest-avenue-edit ~/latest-avenue-edit
```

Then double-click `install_mac.command`. First run: about 1.5 GB of downloads and 30–90
minutes of compiling. **To update, run it again** (a few minutes). The app runs offline.

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
| `tests/vse/test_vse.py` | **13 of 13 pass** on Linux: video export and import, LUT pixel values, ripple delete / cut / delete range, split, trim, color pass, crop zoom. |
| `tests/vse/test_mcp_e2e.py` | **Passes** on Linux: MCP client lists tools, edits, gets errors and rendered frames back; web UI routes respond. |
| Game view | Checked on Linux with simulated key presses on a virtual display (software rendering): preview, apply and advance, pass state, HUD drawing. **Not yet used by a person on real hardware.** |
| Theme | Applies with every property resolving. Only seen in screenshots; needs a visual pass against latestavenue.com. |
| Two-pane default layout | Not done when this was written (see step 3.1), unless a later commit says so. |
| macOS build, `install_mac.command` | **Never run on a Mac.** |
| Windows build | Never attempted. |
| App icon | Made and wired in; not seen in a built Mac or Windows app. |
| Web version of the game UI (`ui/`) | Routes tested; the page itself not opened in a browser against the editor. |

If a later commit message or this table says otherwise, trust the later one.

## Step 1: get it compiling and launching on this machine

On an Apple Silicon Mac, run `./install_mac.command` from Terminal and fix what breaks. On
other platforms follow [BUILDING_VSE.md](BUILDING_VSE.md).

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

1. **Two-pane default layout.** Wanted: one window, game view pane on the left, full
   timeline on the right, `F` for full-screen game view. The game view currently attaches
   to whatever preview pane the Video Editing template has. Edit the template's
   `scripts/startup/bl_app_templates_system/Video_Editing/startup.blend` (open the app,
   arrange the screen, File > Defaults > Save Startup File, copy the saved file over it) so
   the default layout is exactly those two panes.
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

3. Package the build from this machine:

   - **macOS**: `ditto -c -k --keepParent "/Applications/latest avenue video.app" "latest-avenue-video-$(cat VSE_VERSION)-macos-arm64.zip"`
     The app is only ad-hoc signed: on another Mac, right-click > Open the first time (or
     `xattr -dr com.apple.quarantine` on the app). Proper signing and notarization need an
     Apple Developer ID and are not set up.
   - **Windows**: zip the `bin` folder of the build as `latest-avenue-video-<version>-windows-x64.zip`.
   - **Linux**: `tar -cJf latest-avenue-video-<version>-linux-x64.tar.xz -C <build> bin`
     (build on Rocky Linux 8 for it to run on every distribution).

4. Create the release, or add this machine's file to it if it exists:

```bash
gh release create "v$(cat VSE_VERSION)" latest-avenue-video-*.zip --title "v$(cat VSE_VERSION)" --notes "what changed"
```

```bash
gh release upload "v$(cat VSE_VERSION)" latest-avenue-video-*.zip
```

5. To install a release on another machine: download the file for that platform from the
   repo's Releases page and unzip it. No compiling, works offline.

A later improvement: have the app check the Releases page for a newer `VSE_VERSION` and
offer the download.

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
  pushed and the file is lost. This matters for step 3.1: the edited `startup.blend` must be
  exempted (add its path there) before committing it.

## Map of the code we own

| Path | What |
|---|---|
| `build_files/cmake/config/vse_standalone.cmake` | Which Blender features are built, per platform |
| `build_files/vse/fetch_libs.py` | Downloads precompiled libraries for this platform |
| `install_mac.command` | Mac install and update |
| `scripts/modules/vse_standalone/api.py` | Automation tools |
| `scripts/modules/vse_standalone/server.py` | Local control server, 127.0.0.1:8792 |
| `scripts/modules/vse_standalone/game_view.py` | Game view pane |
| `scripts/modules/vse_standalone/color_pass.py`, `modes/`, `luts/` | Look/Color pass engine, night mode |
| `scripts/modules/vse_standalone/theme.py` | Theme from latestavenue.com tokens |
| `scripts/startup/vse_standalone_startup.py` | Starts everything; Automation panel |
| `tools/vse_mcp/vse_mcp.py` | MCP bridge (Python standard library only) |
| `tests/vse/` | Tests |
| `release/vse/icon/` | App icon sources (`la_1024.png`, `.icns`, `.ico`) |
