# Assembly cut: footage + script in, a cut out, by code

The plan: make the editor as programmatic as possible. Code and plain algorithms do the work;
native tools (ffmpeg, ffprobe, pdftotext) wherever they can; small open models only where
code can't hear or see (speech to text, what a frame looks like); the editor's built-in
language model only as a last resort, with no chat, called once and only through tools. The
Blender sequencer is used when it's time to actually sequence.

```
Google Drive folder ──► footage, photos, audio            script PDF        supervisor notes PDF
                          │                                  │                  (reserved, not built)
                ffprobe: length, fps, timecode, tracks       │
                          │                       pdftotext -layout + rules
          ffmpeg audio ─► faster-whisper (base.en)          │
          word timings ─────────────► fuzzy sequence alignment (rapidfuzz) ◄─┘
                                              │ takes per line, ranked
     ffmpeg shots + thumbnails ─► MobileCLIP2-S0 ─► look-alike groups (scenes, cover)
                                              ▼
                               editing script  (*.edit.json)
             screenplay text ◄──► footage per line, in/out, alternates
                                              ▼  submit
                         Blender sequencer: one strip per line, markers per scene
                                              ▼
            game view: crop / colour (optional) ─► fine-tune by hand ─► export (ffmpeg / Drive)
```

## Status

| Step | What | State |
|---|---|---|
| 1 | Media from a folder; ffprobe info | **Built, tested** (`assembly/probe.py`). Google Drive download **not built** (see below) |
| 2 | Screenplay PDF → scenes, action, characters, dialogue | **Built, tested** (`assembly/screenplay.py`): headings with INT/EXT/location/time and scene numbers, cues with (V.O.)/(O.S.), (CONT'D) dropped, parentheticals, transitions, page numbers and (MORE)/(CONTINUED) removed; layout PDFs and flat Fountain/plain text. Tested on a generated PDF and a Fountain sample, **not yet on a real production script** |
| 3 | Script supervisor notes | **Reserved, not built.** Contingencies and slots: [script_supervisor_notes.md](script_supervisor_notes.md) |
| 4 | Assembly: transcription, matching, ranking, grouping | **Built, tested** on the generated shoot (`assembly/transcribe.py`, `match.py`, `visual.py`, `pipeline.py`): every take found for every line at the right time, flubbed and cut-short takes ranked below clean ones, scene 1 and 2 clips told apart, B-roll and a still grouped with their scene, spoken slates read. **Not yet run on real footage** |
| 5 | Editing script (JSON), text round trip | **Built, tested** (`assembly/editing_script.py`): moved lines keep their footage, shortened lines are re-trimmed to the words left, deleted lines drop, a typed-in line is found in what was said on set, round trip of unchanged text changes nothing |
| 6 | Lay out in the sequencer | **Built, tested headless** in the Linux build (`assembly_tools.lay_out_editing_script`): frame rate from footage, strips cut to in/out with no gaps, sound on the channel above, a marker per scene, re-laying replaces the old strips. **Not yet looked at by a person** |
| 7 | Crop/colour, fine-tune, export | Crop and colour: the existing game view and tools, unchanged. Direct ffmpeg render of an editing script: **built, tested** (`assembly/render.py`). Export of a sequence to Drive: **not built** |
| — | No script | **Built, tested**: rough script from speech (pause-split lines, slates dropped) by look-alike group in timecode order, alternates from the other takes; the language model asked once for the scene order (tested against a stand-in server, not a real model) |

## Running it

The screenplay and editing-script parts are standard library only, so they run in the
editor's own Python. Transcription and picture grouping need their own environment:

```bash
python3 -m venv assembly-env
assembly-env/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch   # optional, for MobileCLIP
assembly-env/bin/pip install -r tools/vse_assembly/requirements.txt
# system: ffmpeg, poppler-utils (pdftotext); espeak-ng only for the tests
```

From the command line:

```bash
A="assembly-env/bin/python tools/vse_assembly/assemble.py"
$A script script.pdf                                   # what the parser read (JSON)
$A assemble footage/ --script script.pdf -o cut.edit.json --render cut.mp4
$A text cut.edit.json > cut.txt                        # screenplay text; edit it
$A apply cut.edit.json cut.txt                         # footage follows the edited lines
$A take cut.edit.json S1-3 1                           # play line S1-3 from its 2nd-best take
$A summary cut.edit.json
$A render cut.edit.json -o cut.mp4
```

From the editor (and through MCP / the gateway's `/api/rpc`), with `VSE_ASSEMBLY_PYTHON`
pointing at `assembly-env/bin/python`:

| Tool | Does |
|---|---|
| `assembly_status` | Which Python and packages the assembly will use |
| `probe_media` | ffprobe info for a file or a whole folder |
| `read_screenplay` | The parsed screenplay |
| `start_assembly` → `assembly_job` | Build an editing script in the background; poll for progress |
| `get_editing_script` | Summary, screenplay text, every line with its takes, or the whole JSON |
| `edit_editing_script` | Replace the text; footage follows |
| `choose_take`, `set_line_cut` | Swap a line's take; fine-tune its in/out |
| `lay_out_editing_script` | Put the cut on the timeline ("submit") |
| `render_editing_script` | ffmpeg render of the editing script, no timeline needed |

Tests: `python3 tests/vse/test_assembly.py` (parser only, no packages);
`assembly-env/bin/python tests/vse/test_assembly.py --full --keep DIR` (everything, on a
generated shoot: a two-scene screenplay PDF, five takes spoken by espeak-ng with a flubbed line,
a close-up, a cut-short take, a voice-over WAV, B-roll and a still); then
`blender -b --factory-startup --python tests/vse/test_assembly_editor.py -- DIR` (the editor
tools on that shoot). In a network that blocks Hugging Face's file CDN, set
`HF_HUB_DISABLE_XET=1` for the first model download.

## How matching works (no model)

1. **Which scene**: the take's words against each scene's vocabulary, rare words weighted up
   (an IDF score), so the alignment only looks at the best few scenes.
2. **Which lines, where**: word-by-word local alignment of the take's words against those
   scenes' dialogue. Word similarity is Levenshtein (rapidfuzz), exact for words of three
   letters or fewer ("cut" is not "but"); digits are spelled out ("10" = "ten"). Skipping a
   whole script line is free (a close-up covers one character), skipping words inside a line
   costs, extra spoken words (slate, "action", ad-libs) cost a little. What's left over is
   aligned again (pickups). A reworded ending runs on to the end of the utterance.
3. **Rank**: coverage × accuracy × recognizer confidence, minus a little per extra word.
   Near ties (within 0.02) go to the take the previous lines play from, for fewer cuts.
   Circled takes will add a bonus here (step 3).
4. **In/out**: first and last word, plus 0.2 s before and 0.3 s after, never into the
   neighbouring line's words.
5. **Action lines**: if the lines either side play from the same take, the beat plays
   through from that take; otherwise the scene's cover (clips in its look-alike group with no
   dialogue, longest shot first). Voice-over plays over cover picture.

## Model choices

Smallest model that does the job, base models first; LoRA fine-tuning only if real footage
shows the need.

**Speech: faster-whisper `base.en`** (74 M parameters, int8 on CPU, ~140 MB). Measured here
(4 cores): a 22 s take in 2–3 s after the model is loaded. Word timestamps built in. On the
test voices `tiny.en` (39 M) misheard too much ("last must", "an iron") and broke the ranking,
so it isn't the default; `small.en` (244 M) is the step up for noisy location sound. Also
considered: whisper.cpp (same models in C++, no Python; the way to ship transcription inside
the editor later), Moonshine tiny/base (27 M/61 M, faster on edge devices, but no native word
timestamps), distil-whisper small (166 M), Parakeet TDT 0.6B (accurate, but 600 M and NeMo).
Showing Whisper the character names as a prompt made it invent words in the silence after a
take, so no prompt is used; names are matched fuzzily instead.

**Pictures: MobileCLIP2-S0** through open_clip (11.4 M parameters in the image encoder,
measured). On the test shoot it put the close-up with its wide shots (0.95 similarity), kept
the two scenes apart (≤ 0.63), and grouped a still photo with its scene (0.87). Also
considered: SigLIP2 B/16 (86 M image tower, more accurate, eight times larger), DINOv2-small
(21 M, very good "same place" features but no text side; MobileCLIP's text side allows "find
shots of the diner" later). Without torch installed, a model-free colour signature (4×4
average colours + histogram) is used; it grouped the test shoot the same way except the still.
torch is most of the install size: exporting the image encoder to ONNX and running it with
onnxruntime would remove it (next steps).

**Language model**: only for the scene order of a rough cut without a script, one request to
the OpenAI-compatible server already used by the editing assistant (`LLM_BASE_URL`, or
`VSE_ASSEMBLY_LLM_URL`). Without one, timecode order.

## Next steps, in order

1. **Real footage and a real script.** Run the full pipeline on a real shoot; tune
   `MATCH_MIN`, the skip costs, `UTTERANCE_GAP` and the look thresholds on it; add the cases
   that break to `tests/vse/test_assembly.py`.
2. **Google Drive in and out.** A `fetch_drive_folder(folder_id)` tool that downloads a folder
   into the media folder (Drive API v3 `files.list` / `files.get?alt=media`, using an access
   token with `drive.readonly` handed over by the site's Google sign-in, never stored), skipping
   files already there by size and checksum; and an upload of renders and the editing script.
3. **Container**: add poppler-utils and the assembly environment to `cloud/Dockerfile` and set
   `VSE_ASSEMBLY_PYTHON`, so the cloud editor can run the assembly; keep models in `/data/models`.
4. **The site's /edit page** (latest-avenue repo): editing-script view calling
   `get_editing_script` / `edit_editing_script` / `choose_take`, thumbnails from
   `media[].shots[].thumbnail`, and "Submit" calling `lay_out_editing_script`.
5. **Script supervisor notes** (step 3, [script_supervisor_notes.md](script_supervisor_notes.md)).
6. **Speaker turns**: when two characters overlap in one take, split by speaker (a small
   speaker-embedding model) before alignment. Not needed for clean takes.
7. **ONNX image encoder** to drop torch; whisper.cpp to run transcription without the extra
   Python environment.
8. **LoRA** fine-tuning of the recognizer only if real material is consistently misheard
   (accents, names), using the corrected editing scripts as training data.
