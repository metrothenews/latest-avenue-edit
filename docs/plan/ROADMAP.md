# Roadmap

Build order. Status is one of: not started / in progress / done. The first steps are testable
from the command line, before any user interface. Deadline for a working consumer-facing
app: noon Eastern, 2026-11-18. "Pushed, unverified" means code is on the builder's branch
but no commit says it was run. See [PRODUCT.md](PRODUCT.md) for what
"done" means for each workflow step.

| # | Step | Status |
|---|---|---|
| 1 | Script parsing: PDF → structured screenplay JSON (`pdftotext -layout` + rules), CLI | in progress (pushed, unverified) |
| 2 | Media inventory: folder → ffprobe metadata per file, CLI | in progress (pushed, unverified) |
| 3 | Audio extraction and word-timed transcription per clip (ffmpeg + faster-whisper / whisper.cpp), CLI | in progress (pushed, unverified) |
| 4 | Match takes to script lines (rapidfuzz + sequence alignment), ranked candidates, CLI | in progress (pushed, unverified) |
| 5 | Shot grouping: scene detection, thumbnails, image embeddings (MobileCLIP / SigLIP) to group setups and sequences, CLI | in progress (pushed, unverified) |
| 6 | Assembly cut: editing script JSON in best-guess order with alternatives (reserved slot for supervisor notes), CLI | in progress (pushed, unverified) |
| 6b | Sync separate audio: timecode → waveform matching → clap/slate; skipped with camera audio only | not started |
| 6c | Music-driven assembly: lyrics synced to the song (first test: IMPULSIVE, assignment 17) | not started |
| 7 | Expose steps 1–6 as editor tools (MCP bridge, gateway `/api/rpc`) | not started |
| 8 | Submit: build the sequence in the Blender sequencer from the editing script | not started |
| 9 | Screenplay-style editor for the editing script on the `/edit` page (latest-avenue repo) | in progress (design worker, latest-avenue `claude/edit-design`) |
| 10 | Google Drive: connect, pick the footage folder and script | not started |
| 11 | Quick crop / quick colour on the assembled cut in the game view | not started |
| 12 | Export to Google Drive: a render (ffmpeg render of the editing script: in progress, pushed, unverified) and sequences for Resolve, Premiere, Final Cut (OpenTimelineIO + FCPXML / FCP7 XML / EDL) | in progress |
| 13 | No-script mode: transcript + image recognition → script, LLM best-guess order | in progress (pushed, unverified) |
| 14 | Script supervisor notes (circled takes, slates, continuity) | not started — planned only |
| 15 | LoRA fine-tuning of recognition models, only if base models miss on Sam's footage | not started — only if needed |
