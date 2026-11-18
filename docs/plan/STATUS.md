# Status

Updated by the documentation worker from the builder's pushed commits (all branches). Last
checked: 2026-11-18 ~05:45 UTC: `origin/main` at `dcfe4331`, `origin/claude/assembly-pipeline`
at `067823cf`.

**Deadline:** a working consumer-facing app by noon Eastern today (17:00 UTC).

## Team and branches

| Worker | Session | Repo / branch | Job |
|---|---|---|---|
| Builder | `session_01RU4b83zvxMK3x6KUdbRCGx` | latest-avenue-edit, `claude/assembly-pipeline` | The code |
| Documentation | `session_01RU4b7z6i76RXPGnDfyCTUq` | latest-avenue-edit, `docs/dev-plan` | These docs |
| Design | `session_01RU4b7zATq5Twqv5ypgg6Mc` | latest-avenue, `claude/edit-design` | The `/edit` website flow |

A coordinating session in the latest-avenue repo relays Sam's answers and updates.

## What works now (before this plan)

From the README's status table, which has the details and caveats:

- The editor (Blender 5.3 VSE, 3D switched off) builds and runs on Linux and the Intel Mac;
  v0.1.0 released with the Intel Mac file. Apple Silicon and Windows builds never run.
- Editor tools in `scripts/modules/vse_standalone/api.py`, served by the control server
  (`POST /rpc`, 127.0.0.1:8792), the MCP bridge (`tools/vse_mcp/vse_mcp.py`) and the
  gateway (`POST /api/rpc`). Includes add/trim/split/ripple tools, LUTs, render, and
  `export_otio` (depends on the build having the OTIO operator).
- Game view (keyboard-driven passes, LUT colour pass, crop zoom).
- Lyric video tools with word-timed transcription via faster-whisper (`tools/vse_lyrics/`).
- Server container running on Sam's Windows PC (Docker, Tailscale Funnel); real Google
  sign-in from the site not yet done.
- A chat-based editing assistant (`cloud/agent.py`) tested on Linux with a small model only.

## The plan in [PRODUCT.md](PRODUCT.md)

Pushed by the builder to `claude/assembly-pipeline` (not merged to `main`), in
`scripts/modules/vse_standalone/assembly/`, command line in `tools/vse_assembly/assemble.py`.
The commit messages don't say whether any of it was run or tested; treat it as unverified
until a commit or the builder says so.

- `probe.py`: ffprobe media info.
- `screenplay.py`: `pdftotext -layout` + screenplay rules.
- `transcribe.py`: faster-whisper word timings, cached.
- `match.py`: takes matched to script lines by fuzzy word-by-word sequence alignment
  (rapidfuzz, or difflib without it), with coverage / accuracy / confidence scores; handles
  pickups. No model.
- `visual.py`: ffmpeg shot detection, thumbnails, MobileCLIP2-S0 embeddings (or a model-free
  colour signature), clustering into set-up groups.
- `editing_script.py`: the editing script (`*.edit.json`, version 1): per line the chosen
  take, in/out, ranked alternatives, cover picture for audio-only lines; per clip `slate`
  and `supervisor` fields (the reserved slot). Text round trip: export as screenplay text,
  edit, read back with footage following the lines.
- `pipeline.py`: probe → script → transcribe → match → group → editing script; without a
  script, a rough one from what was said, and one LLM request for scene order (`llm.py`).
- `render.py`: render an editing script straight to video with ffmpeg.
- Commands: `probe`, `script`, `assemble`, `text`, `apply`, `take`, `summary`, `render`.
- `tests/vse/assembly/make_samples.py`: generates a test shoot (script PDF, spoken takes via
  espeak-ng, V.O., B-roll, photo, `truth.json`). No test that runs the pipeline against it
  has been pushed yet.

## Half-built / not yet pushed

- Laying the editing script out on the editor's timeline: `editing_script.py` refers to
  `assembly_tools.lay_out`, which is not in the pushed commits.
- Not exposed as editor tools / `/api/rpc` yet.
- No sync step for separately recorded audio yet.
- No music-driven (lyrics) mode in the assembly yet; the older `tools/vse_lyrics/` exists.
- No Google Drive connection, no screenplay-style editor on `/edit` (design worker's job in
  latest-avenue), no Resolve / Premiere / Final Cut export.

## Next

Lay-out in the editor, editor tools for each step, a test run against `make_samples.py`
output, then the sync step and the website flow.
