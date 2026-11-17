# What differs from Blender v5.3.0

The first commit is Blender v5.3.0 unchanged; `git diff 16edb42a` shows everything below.

## Engine (C++)

| Area | Files | Change |
|---|---|---|
| Build | `build_files/cmake/config/vse_standalone.cmake` | 3D, render engines, simulation and 3D I/O off; video, audio, OTIO on; per-platform sections |
| Library builder | `build_files/build_environment/CMakeLists.txt`, `cmake/download.cmake` | `-DVSE_DEPS_ONLY=ON` skips the libraries listed in `build_files/vse/deps_vse.cmake` (used for Intel Mac self-built libraries); off by default |
| Startup | `windowmanager/intern/wm_files.cc` | Always start in the Video Editing app template |
| Menus | `makesrna/intern/rna_screen.cc`, `editors/screen/workspace_edit.cc` | 3D editors and the General workspace set hidden from menus (still valid for scripts and old files) |
| LUT modifier | `sequencer/SEQ_lut.hh`, `sequencer/intern/lut.cc`, `sequencer/intern/modifiers/MOD_lut.cc`, `makesdna/DNA_sequence_types.h`, `makesrna/intern/rna_sequencer.cc` | Strip modifier type `LUT` (`.cube` 1D/3D, intensity, clamp, mask input) |
| Ripple | `editors/space_sequencer/sequencer_edit.cc` | `sequencer.ripple_cut`, `sequencer.ripple_delete_range` (Blender already had `ripple_delete`, `ripple_trim`) |

## Application layer (Python, `scripts/modules/vse_standalone/`)

| Module | Purpose |
|---|---|
| `api.py` | Automation tools: every editing step as a function with plain arguments |
| `server.py` | Local control server (JSON-RPC on 127.0.0.1:8792), also serves the web UI |
| `color_pass.py` | Look/Color LUT pass and Crop zoom engine; `modes/`, `luts/` hold the night mode |
| `game_view.py` | The game view pane inside the editor (keys, HUD, full-screen) |
| `theme.py` | latest avenue theme generated from the site's design tokens |
| `shell.py`, `ui/` | Optional web version of the game UI |
| `scripts/startup/vse_standalone_startup.py` | Starts the server, theme, game view; Automation panel |

Other: `tools/vse_mcp/` (MCP bridge), `tests/vse/` (tests), `build_files/vse/fetch_libs.py`, `build_files/vse/build_libs_macos_x64.sh`,
`doc/vse/reel_workflow.md`, removed the 2D Animation / Sculpting / Storyboarding / VFX templates.

## Upgrading to a newer Blender

Rebase the second commit onwards onto the new Blender tag. The engine changes are small and
localized; the application layer only uses Blender's public Python API.
