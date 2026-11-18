# Status

Updated by the documentation worker from the builder's pushed commits (all branches). Last
checked: 2026-11-18 ~13:10 UTC: `origin/main` at `dcfe4331`; builder at
`claude/assembly-pipeline` `7772e174`; website at latest-avenue `claude/great-johnson-glhijj`
`d2b6c96` (reported by the coordinator, not checked from here).

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

Three levels below: **works on real footage**, **tested in the container only** (Linux
Docker, no GPU, generated media), and **untested**. Nothing has been used by Sam through the
live site yet.

### Works on real footage (measured)

- Picture grouping on 71 of Sam's culture clips (numbers only, no media committed):
  MobileCLIP2-S0 at the default 0.80 gave 41 groups, 94% purity, 90% pair precision. The
  model-free colour signature lumped most night footage together (59% purity, 19% pair
  precision). **The picture model must be installed on the PC for good grouping.**

### Tested in the container only

Builder (latest-avenue-edit `claude/assembly-pipeline`, not merged to `main`):

- Script parsing (screenplays and numbered ad scripts), media info, file-name slates, sound
  sync (timecode → waveform → clap), transcription, take-to-line matching, the editing
  script, Blender lay-out, ffmpeg render, music mode (beat tracking, lyric lines; a ~170 BPM
  song locks to half speed), Drive client (against a stand-in Drive server).
- Sequence exports (OTIO, FCPXML, FCP 7 XML, EDL), checked without the target apps
  (`assembly/check_exports.py`): FCPXML against Apple's FCPXML 1.8 DTD, FCP 7 XML structure,
  CMX 3600 EDL rules. Every export of both test shoots passes; the checkers catch a
  deliberately broken file of each kind.
- The whole `/edit` flow over the gateway's `/api/rpc`, the way the website calls it
  (`tests/vse/test_site_flow.py`, run by `cloud/test_container.sh` against the real gateway
  and editor with a generated test certificate): 401 without a token, editor start on the
  first signed-in call, upload → read script → assembly job → editing script → edit → choose
  take → thumbnails → clip sync → lay-out → render → four exports, then a music video from an
  uploaded song and lyric sheet. All passed; editor up 9 s after the first call.
- `HF_HUB_DISABLE_XET=1` set in the image and passed by the PC scripts, so models download
  behind strict proxies.

Website (latest-avenue, `claude/great-johnson-glhijj` @`d2b6c96`): the guided `/edit` flow
(what are you making → footage → script or song → assembly → review with confidence and sync
labels → sound panel → export to Drive "latest avenue exports"), live sequencer at
`/edit/sequencer`, assistant chat off by default. Earlier tested at 390 px against the real
editor running headless, Drive stubbed. Build passes.

### Untested

- Exports opened in DaVinci Resolve, Premiere Pro or Final Cut Pro.
- A real Google sign-in from latestavenue.com/edit, and a real Drive token.
- The GPU path (CUDA) on the PC; the Windows scripts `cloud/pc/test.ps1` / updated
  `install.ps1`.
- The picture model (MobileCLIP2-S0) installed and running on the PC.
- The site merged to its main branch and deployed.

## Repository visibility

The coordinator cloned latest-avenue-edit anonymously over HTTPS (no credentials) at
06:26 UTC, so the repository is **public**. The README's "private repo" lines are out of
date. Sam to confirm he wants it public (see OPEN_QUESTIONS.md). Sam's media is never
committed either way.

## Next

See [GO_LIVE.md](GO_LIVE.md): update the PC's editor, merge and deploy the site, test with a
real sign-in. After that: open the exports in the three editors, fix the half-speed tempo
lock at ~170 BPM.
