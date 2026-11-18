# Status

Updated by the documentation worker from the builder's pushed commits (all branches). Last
checked: 2026-11-18 ~07:30 UTC: `origin/main` at `dcfe4331`; builder at
`claude/assembly-pipeline` `ff12b07c`; website at latest-avenue `claude/edit-design` `43475e7`
(reported by the coordinator, not checked from here).

**Deadline:** a working consumer-facing app by noon Eastern today (17:00 UTC).

## Team and branches

| Worker | Session | Repo / branch | Job |
|---|---|---|---|
| Builder | `session_01RU4b83zvxMK3x6KUdbRCGx` | latest-avenue-edit, `claude/assembly-pipeline` | The code |
| Documentation | `session_01RU4b7z6i76RXPGnDfyCTUq` | latest-avenue-edit, `docs/dev-plan` | These docs |
| Design | `session_01RU4b7zATq5Twqv5ypgg6Mc` | latest-avenue, `claude/edit-design` | The `/edit` website flow |

A coordinating session in the latest-avenue repo (`session_01RU4b82aSXQhYSqYKHD9jnW` since
~07:30 UTC, replacing `session_01RU4b7z56N1Bxev4ADDqxV8`) relays Sam's answers and updates.
The PC worker has been stood down.

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

"Done in the container" means it runs and passes tests in a Linux Docker container with no
GPU (`cloud/Dockerfile`, `Dockerfile.test`) on generated media. All suites and
`docker run --rm latest-avenue-edit:test` passed there (builder commits up to `ff12b07c`).
It has not been run on Sam's footage, on the PC, or on a GPU.

### Builder (latest-avenue-edit, `claude/assembly-pipeline`, not merged to `main`)

Done in the container:

- Script parsing: screenplays and numbered ad/social scripts.
- Media info (ffprobe), filename slate parsing (takes from file names).
- Sound sync: timecode, then waveform, then clap.
- Transcription (faster-whisper), take-to-line matching, shot grouping (colour signature
  only; MobileCLIP couldn't be installed in the sandbox).
- The editing script, the Blender lay-out into a sequence, ffmpeg render.
- Sequence export: OTIO, FCPXML, FCP7 XML, EDL. Each was read back OK; none has been opened
  in Resolve, Premiere or Final Cut yet.
- Music mode: beat tracking, lyric lines start shots. Known issue: a song at ~170 BPM locks
  to half speed.
- Google Drive client: fetch a folder, upload. Tested against a stand-in Drive server only.
- Editor tools for the site: `save_upload`, `start_assembly` (with `song` / `lyrics`),
  `set_clip_sync`, `take_thumbnails`; background jobs with job ids and polling.

Not tested: the GPU path, Windows `cloud/pc/test.ps1` / `install.ps1`, a real Drive token.

### Website (latest-avenue, `claude/edit-design`, merged into `claude/great-johnson-glhijj`)

A guided `/edit` flow: what are you making → footage → script or song → assembly → review
(with confidence and sync labels) → sound panel → export to Drive ("latest avenue exports").
The live sequencer moved to `/edit/sequencer`. The assistant chat is kept but off by
default. The build passes; a signed-in check on a phone is still to do.

## Next

- Run on real clips from Sam's "culture clips collections" (one by one, downscaled to 140p,
  never committed).
- Signed-in phone check of the website flow.
- Open the exported sequences in Resolve, Premiere and Final Cut.
- GPU and Windows runs on the PC; a real Drive token.
- Fix the half-speed tempo lock at ~170 BPM.
