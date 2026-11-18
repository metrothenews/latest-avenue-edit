# Roadmap

Build order. Status is one of: not started / in progress / done. The first steps are testable
from the command line, before any user interface. Deadline for a working consumer-facing
app: noon Eastern, 2026-11-18. "Done (in container)" means it passes tests in a Linux container with no GPU on generated
media, not yet on Sam's footage or the PC. See [PRODUCT.md](PRODUCT.md) for what
"done" means for each workflow step.

| # | Step | Status |
|---|---|---|
| 1 | Script parsing: screenplays and numbered ad scripts → JSON, CLI | done (in container) |
| 2 | Media inventory (ffprobe) and filename slates, CLI | done (in container) |
| 3 | Audio extraction and word-timed transcription (faster-whisper), CLI | done (in container; GPU path untested) |
| 4 | Match takes to script lines (fuzzy sequence alignment), ranked candidates, CLI | done (in container) |
| 5 | Shot grouping: scene detection, thumbnails, embeddings / colour signature | done (MobileCLIP2-S0 measured on 71 real clips: 94% purity; needs the model installed on the PC) |
| 6 | Assembly cut: editing script JSON with alternatives and the supervisor slot, CLI | done (in container) |
| 6b | Sync separate audio: timecode → waveform → clap; skipped with camera audio only | done (in container) |
| 6c | Music-driven assembly: beat tracking, lyric lines start shots | done (in container; ~170 BPM locks to half speed) |
| 7 | Editor tools (MCP / `/api/rpc`), background jobs with polling | done (in container: whole site flow over `/api/rpc`) |
| 8 | Submit: lay the editing script out in the Blender sequencer | done (in container) |
| 9 | Guided `/edit` flow on the website, with review and sound panel (latest-avenue) | in progress (build passes; not deployed; real sign-in untested) |
| 10 | Google Drive: fetch the footage folder, upload exports | in progress (tested against a stand-in only) |
| 10b | Controllable import: Drive browser with multi-select, Add Media list (drag + buttons), pause / resume / cancel / live edit, persistent resumable queue, optional 140p proxies; tools `import_*`, `remove_media`, `list_media` | not started (requested by Sam 2026-11-18; no import tools on the builder's branch at `3121b027`) |
| 11 | Quick crop / quick colour on the assembled cut in the game view | not started |
| 12 | Export: ffmpeg render; OTIO / FCPXML / FCP7 XML / EDL for Resolve, Premiere, Final Cut | in progress (pass format checks incl. Apple's FCPXML DTD; not opened in the three editors) |
| 13 | No-script mode: transcript + image recognition → script, LLM best-guess order | in progress (pushed; not separately reported as tested) |
| 14 | Script supervisor notes (circled takes, slates, continuity) | not started — planned only |
| 15 | LoRA fine-tuning of recognition models, only if base models miss on Sam's footage | not started — only if needed |
| 16 | Run on Sam's real clips, on the PC with the GPU, and on Windows | in progress (grouping measured on 71 real clips in the container; PC and GPU not run) |
| 17 | Go live: update the PC, deploy the site, real sign-in ([GO_LIVE.md](GO_LIVE.md)) | not started |