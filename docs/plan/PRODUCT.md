# The final product

Read this first. It describes what latest avenue video is being built into, from Sam's
handwritten plan. Build order is in [ROADMAP.md](ROADMAP.md), current state in
[STATUS.md](STATUS.md), guesses made along the way in [DECISIONS.md](DECISIONS.md), and
questions only Sam can answer in [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md), and
the steps to put it live in [GO_LIVE.md](GO_LIVE.md).

## In one paragraph

Give the editor a folder of footage and audio plus the screenplay, and it produces an
**assembly cut**: an **editing script** that reads like the screenplay, where each line is
bound to the takes that cover it, in best-guess order, with alternative takes ready to swap.
The user edits that document like a screenplay; the footage moves with the text. When they
submit it, the real sequencer (the Blender-based editor in this repo) opens with the cut laid
out, ready for quick crop / colour in the game view and then fine editing the normal way.

## Who it is for and when

- **Deadline:** a working consumer-facing app by noon Eastern, 2026-11-18 (17:00 UTC).
- **Consumer-facing** for now means intuitive for a non-technical person, still behind Sam's
  sign-in. (Assumption; opening it to the public is a separate decision Sam hasn't made.)
- **Content it must handle:** scripted monologue-driven social media content, scripted short
  films, ads/commercials, music videos, interviews/docs. So the assembly cut must handle both
  dialogue-driven and music-driven edits.
- **Runs on** Sam's Windows PC with an NVIDIA RTX graphics card: CUDA when available, CPU
  fallback.
- **Sam's media is never committed** to the repository.

## Principles

- **Code and algorithms first.** Every step is done programmatically wherever possible.
  The whole premise is *simple and lightweight*: not dependent on LLMs, heavily dependent on
  code and algorithms.
- **The LLM is a last-resort fallback.** It has **no chat feature**. It lives inside the
  pipeline and is called only at specific points where code can't decide (for example,
  ordering footage when no script was given). When called it uses the MCP tools
  strategically, then hands control back.
- **Native libraries before anything else.** ffmpeg/ffprobe do media work. The
  Blender-based code is used only when it is time to actually sequence.
- **Small open-source models.** Transcription and image recognition use the most
  lightweight open-source models that do the job. Sam wrote "LoRA-based"; the plan is small
  base models first, and LoRA fine-tuning only later if they miss on his footage.
- **Every feature is an editor tool.** Each step is exposed as a tool (MCP bridge, and the
  gateway's `POST /api/rpc`) so the website's `/edit` page (latest-avenue repo) can call it.

## Inputs

| Input | Formats | Needed? |
|---|---|---|
| Video and photos | Standard formats | Yes |
| Audio | Standard formats | Yes (may be in the video files) |
| Script | PDF | Optional (see "No script provided") |
| Script supervisor notes | PDF | Advanced, later (see step 3) |

## Workflow

Each step lists what "done" means.

### 1. Import: pick media from Google Drive (controllable)

- **Drive browser with multi-select.** The user drags clips, selections or whole folders
  into an **Add Media** list. Every drag has a button equivalent for touch: drag is never
  the only way (on a phone, Browse and Add Media are tabs).
- **Edit the list before starting.**
- **During the import:** pause, resume, cancel, and edit the list live (add, remove, and
  remove the file currently downloading).
- **In the editor:** a persistent, resumable queue: one file at a time, each download
  resumed with HTTP Range requests, duplicates skipped by Drive file id, recovery when the
  Drive sign-in token expires (`needs_token`), and after a restart the queue comes back
  paused.
- Optional 140p proxies after download.
- **Tools:** `import_start`, `import_status`, `import_pause`, `import_resume`,
  `import_edit`, `import_cancel`, `remove_media`, `list_media` (contract documented by the
  builder in `cloud/TOOLS.md`). The older `fetch_drive_folder` stays as an alias.

Done when: the user can build the list, start, pause, resume, edit and cancel an import
from both a computer and a phone; an interrupted or restarted import resumes without
re-downloading finished files; and the editor has every imported video, photo and audio
file with its technical metadata (duration, frame rate, resolution, codec, audio channels,
timecode if present), read by ffprobe.

### 1b. Sync sound (when there is separate audio)

Audio varies: sometimes a separate recorder, sometimes camera audio only. When there is
separate audio, it is synced to the video by, in order of preference: timecode, then
waveform matching (against the camera's scratch audio), then the clap/slate. Skipped when
there is only camera audio.

Done when: every separately recorded audio file is paired with its video clip and offset,
or listed as unsynced.

### 2. Upload or select the script (PDF)

Done when: the PDF is turned into structured screenplay data (scenes with headings, action,
character, dialogue, parentheticals, transitions), each element with a stable ID, by
`pdftotext -layout` plus screenplay formatting rules, with no LLM.

### 3. Upload or select script supervisor notes (PDF) — not built yet

Not built now. Contingencies to keep in mind so it slots in later:

- What the notes carry: circled takes, slate (scene/setup/take) numbers, continuity notes,
  timing, comments per take.
- The editing script's data reserves a place for this now (per take: slate, circled flag,
  supervisor notes), left empty until the feature exists.
- Rule when it is added: **a circled take wins**; otherwise the step-4 scoring decides.

Telling takes apart (now, without notes): Sam uses all of these at different times, so each
is a fallback for the one before: timecode/metadata, file names, clapperboard, and nothing
formal at all. With no guidance it should still make very good guesses.

Done (later) when: notes are parsed into per-take records and matched to clips by slate.

### 4. Assembly cut

Basic image recognition and audio transcription, then an editing script that resembles the
provided script, with several possibilities for similar takes so the user can swap them, and
footage placed in best-guess order. Image recognition also identifies clips that are likely
part of one sequence (same setup / scene) and cuts them together on that basis.

Done when: from the command line, a footage folder plus a script PDF produce an editing
script JSON in which every script line that was shot points to a ranked list of candidate
takes with in/out times, unmatched footage is listed separately, and a person can check the
result against the footage.

### 5. Edit the editing script like a screenplay

Done when: the user can edit the editing script as a screenplay-style document (reorder,
delete, swap a line's take for one of its alternatives, trim), and the timeline metadata
moves with the text.

### 6. Submit: open the sequencer with the cut laid out

Done when: submitting the editing script opens the Blender-based sequencer with the clips
on the timeline in the editing script's order, with the chosen in/out points and synced audio.

### 7. Optional quick crop / quick colour

In the existing game-ified view (game view). Done when: the user can run crop and colour
passes over the assembled cut in the game view.

### Then

The user fine-tunes the edit the traditional way and exports to Google Drive either a
rendered video or a sequence file for **DaVinci Resolve, Premiere Pro or Final Cut Pro**
(proposed: OpenTimelineIO at the core, with FCPXML / FCP7 XML / EDL adapters). Done when a
render and a sequence that opens in each of the three editors land in the user's Drive.

## No script provided

Transcribe the audio and use image recognition to assemble a script, then have the LLM give
its best guess at how to reorder everything. This is one of the few places the LLM is used.

## Music-driven edits

Music videos are cut to the song, not to dialogue. First test: sync lyrics to the song
(Sam's "assignment 17" IMPULSIVE takes and lyric sheet). The existing lyric tools in
`tools/vse_lyrics/` already time a lyric sheet against audio.

## The built-in LLM

A local model on the PC (no API key), used only as a last-resort fallback.

## Proposed stack (to be confirmed by the builder)

| Job | Proposed tool |
|---|---|
| Media info, audio extraction, scene detection, thumbnails, renders | ffprobe / ffmpeg |
| Script parsing | `pdftotext -layout` + screenplay rules |
| Word-timed transcription | faster-whisper or whisper.cpp (faster-whisper is already used in `tools/vse_lyrics/align_lyrics.py`) |
| Matching takes to script lines | rapidfuzz + sequence alignment |
| Grouping shots into setups / sequences | MobileCLIP or SigLIP image embeddings |
| Editing script | JSON |
| Sequencing | Blender (this repo's editor) only |
| Sync of separate audio | timecode, then waveform matching, then clap/slate (tools not chosen yet) |
| Sequence export to other editors | OpenTimelineIO core with FCPXML / FCP7 XML / EDL adapters, for Resolve, Premiere, Final Cut (an `export_otio` tool already exists in `api.py`; it depends on the build having Blender's OTIO operator) |

Research still to do: the most lightweight open-source image recognition models and
datasets, and the same for audio transcription.

## Test material

In Sam's Google Drive folder "Video Editing Resources" (never copied into the repository):

- "culture clips collections": 100+ short mp4s grouped by subject (e.g. cars, people).
- "audio/Nov 2026/assignment 17": takes of the song IMPULSIVE, plus a "song lyrics" doc.
  First music test: sync the lyrics to the song.
- "docs/Ad Scripts for Assignment N": real ad scripts.

Little dialogue footage exists yet; the builder's `tests/vse/assembly/make_samples.py`
generates a synthetic dialogue shoot for tests.

## Out of scope for now

- Parsing script supervisor notes (step 3): only the data slot and the rules are planned.
- LoRA fine-tuning of the recognition models.
- A chat interface to the LLM.
- Automated final editing decisions beyond the assembly cut: fine editing stays with the user.

## Glossary

- **Take**: one recording of a shot, from "action" to "cut".
- **Setup**: one camera position / framing; a setup is usually shot in several takes.
- **Circled take**: a take the director marked as preferred; recorded in the script
  supervisor's notes.
- **Slate**: the board (or its metadata) at the start of a take giving scene, setup and take
  numbers; used to identify clips.
- **Editing script**: this product's central document: the screenplay with each line bound
  to candidate takes and their timeline metadata (file, in/out, sync). Stored as JSON.
- **Assembly cut**: the first rough cut, every scripted moment in script order using a
  best-guess take.
- **Sequence**: a timeline of clips in an editor; here, what the Blender sequencer shows
  after submit, and what is exported to other editors.
