# Roadmap

Build order. Status is one of: not started / in progress / done. The first steps are testable
from the command line, before any user interface. See [PRODUCT.md](PRODUCT.md) for what
"done" means for each workflow step.

| # | Step | Status |
|---|---|---|
| 1 | Script parsing: PDF → structured screenplay JSON (`pdftotext -layout` + rules), CLI | not started |
| 2 | Media inventory: folder → ffprobe metadata per file, CLI | not started |
| 3 | Audio extraction and word-timed transcription per clip (ffmpeg + faster-whisper / whisper.cpp), CLI | not started |
| 4 | Match takes to script lines (rapidfuzz + sequence alignment), ranked candidates, CLI | not started |
| 5 | Shot grouping: scene detection, thumbnails, image embeddings (MobileCLIP / SigLIP) to group setups and sequences, CLI | not started |
| 6 | Assembly cut: editing script JSON in best-guess order with alternatives (reserved slot for supervisor notes), CLI | not started |
| 7 | Expose steps 1–6 as editor tools (MCP bridge, gateway `/api/rpc`) | not started |
| 8 | Submit: build the sequence in the Blender sequencer from the editing script | not started |
| 9 | Screenplay-style editor for the editing script on the `/edit` page (latest-avenue repo) | not started |
| 10 | Google Drive: connect, pick the footage folder and script | not started |
| 11 | Quick crop / quick colour on the assembled cut in the game view | not started |
| 12 | Export a render or a sequence (OpenTimelineIO) to Google Drive | not started |
| 13 | No-script mode: transcript + image recognition → script, LLM best-guess order | not started |
| 14 | Script supervisor notes (circled takes, slates, continuity) | not started — planned only |
| 15 | LoRA fine-tuning of recognition models, only if base models miss on Sam's footage | not started — only if needed |
