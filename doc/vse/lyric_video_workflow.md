# Lyric video workflow: from an audio clip and a lyric sheet to a finished "new style" video

Makes a vertical lyric video in the house "new style": a 3:4 picture between black bars, a new
clip for every lyric line, and the lyrics building up word by word in time with the song.

First used for Assignment 17 (three IMPULSIVE videos, November 2026). Everything here ran on
the Intel Mac; nothing in it is Mac-specific except the default font path.

**Inputs**

1. an **audio clip** of the song (wav, mp3, ...): the video is as long as the audio;
2. the **lyric sheet** as plain text, one lyric line per line (`[Section]` lines are ignored);
3. a **clip library**: a folder of video clips, sorted into subfolders by subject.

**Output**: an MP4 (H.264 + AAC, 1080 × 1920, 30 fps) and the editor project (`.blend`) it
was exported from, which opens in the app for changes by hand.

## The two steps

```
audio + lyric sheet ──align_lyrics.py──▶ song.lyrics.json ──make_lyric_video.py──▶ video.mp4
   (own Python environment,                (word timings,        (the editor, without        + project.blend
    speech recognition)                     sheet's exact words)  a window)
```

### One-time setup

The lyric timing step needs a speech recognizer (faster-whisper), which is too large to ship
inside the editor, so it lives in its own Python environment. Put it and its models on a drive
with space (the models are 0.5–3 GB each):

```bash
python3 -m venv /Volumes/Leverett/latest-avenue-edit-working/lyrics-env
/Volumes/Leverett/latest-avenue-edit-working/lyrics-env/bin/pip install faster-whisper
export VSE_LYRICS_MODELS=/Volumes/Leverett/latest-avenue-edit-working/models
```

`ffmpeg` must be on the PATH (it decodes the audio). The first run with a model downloads it
into `VSE_LYRICS_MODELS`; after that it works offline.

### Step 1: time the lyrics

```bash
lyrics-env/bin/python ~/latest-avenue-edit/tools/vse_lyrics/align_lyrics.py \
    audio1.wav lyrics.txt -o audio1.lyrics.json
```

What it does:

1. **Listens.** Transcribes the audio with a start and end time for every word.
2. **Finds the place in the sheet.** Matches what it heard against the whole lyric sheet and
   finds the part that was sung (a verse, a chorus, any stretch).
3. **Corrects.** Every heard word is replaced by the sheet's exact word; the timing is kept.
   Recognizers mishear singing a lot ("hold on" for "Who knew"); the sheet wins every time.
   Words the recognizer missed get a time between their neighbours. Text it invents in
   silence ("thanks for watching") is dropped.
4. **Completes lines.** If the match starts or ends mid-line and there is singing it could not
   place there, the rest of the line (or the neighbouring line) is taken from the sheet.
5. **Cleans the text.** Look-alike letters from other alphabets (a Cyrillic "е" in "perfect",
   common in lyrics copied from the web) are replaced and reported.

It prints what it heard, the lyric lines it settled on, and every correction with its time.
**Read that printout**: it is the review step. The line list should be exactly the lines sung
in the clip.

Options:

| Option | Use |
|---|---|
| `--model` | `small.en` (fast), `medium.en` (default), `large-v3` (best, slow on a CPU), or a folder with your own converted model |
| `--models-dir` | Where models are kept (default: `VSE_LYRICS_MODELS`) |
| `--lead-in N` / `--lead-out N` | Force N more sheet words before / after the match when the automatic choice is wrong (`0` to forbid extending) |
| `--save-heard f.json` / `--heard f.json` | Save the recognizer's output, and re-run the matching from it without listening again (seconds instead of a minute) |
| `--no-prompt` | Don't give the recognizer the sheet's vocabulary as a hint |

**Checking the timing.** Run a second model and compare; on the IMPULSIVE clips `small.en`
and `medium.en` agreed to within 0.07 s on a typical word:

```bash
... align_lyrics.py audio1.wav lyrics.txt -o check.json --model small.en
```

**The timing file** (`.lyrics.json`):

```json
{
 "duration": 16.5,
 "words": [{"text": "Too", "line": 2, "start": 0.3, "end": 0.62, "heard": "too", "matched": true}, ...],
 "lines": [{"text": "Too soon? I'm ready, babe", "start": 0.3, "end": 2.1, "words": [0, 1, 2, 3, 4], "complete": true}, ...],
 "corrections": [{"time": 2.26, "heard": "hold", "sheet": "Who"}, ...],
 "stats": {"words": 34, "timed_from_audio": 33, "heard_exactly": 25, "confidence": 0.74}
}
```

It is plain JSON: to fix a word's time by hand, edit its `start` and build again.

#### About a fine-tuned (LoRA) recognizer

None is needed for this, and none is included. The lyric sheet already says what the words
are, so the recognizer only has to be right about *when*; matching against the sheet fixes
the *what*, including words it gets badly wrong. A model fine-tuned on this artist's voice
would raise `heard_exactly` and help where no sheet exists. If one is trained later, merge
the LoRA into the base model, convert it with `ct2-transformers-converter`, and pass the
folder as `--model`; nothing else changes.

### Step 2: build and export the video

```bash
APP="/Applications/latest avenue video.app/Contents/MacOS/Blender"
"$APP" -b --factory-startup --python ~/latest-avenue-edit/tools/vse_lyrics/make_lyric_video.py -- \
    --audio audio1.wav --lyrics audio1.lyrics.json \
    --library "/Volumes/Leverett/video editor resources/culture clips collections" \
    --output out/video1.mp4 --project out/video1.blend \
    --used out/used_clips.json --report out/video1.report.json --seed 7
```

What it does:

1. Sets the project to 1080 × 1920, 30 fps, as long as the audio.
2. **Plans the cuts**: a new clip at every lyric line; a line longer than 3.2 s gets one more
   cut at a word near its middle.
3. **Picks clips** from the library: subjects are mixed by subfolder name (`people`, `cars`,
   `urban`, ...; `--weights people=3,cars=1.5` sets how often each comes up), never two in a
   row from the same subject when there is a choice, and never a clip listed in `--used`.
   A clip too short for a slot is kept for a shorter one; a slot nothing fits is cut in two.
4. Places each clip scaled and cropped to fill the **3:4 picture** (1080 × 1440, with 240 px
   black bars above and below).
5. Adds the **lyrics**: one text strip per word, lowercase, Arial Narrow, very light grey at
   90 % opacity, stretched 15 % tall. Each line is laid out as a justified block (half the
   frame wide, near the top of the picture); words appear in place one frame before they are
   sung, and the block is replaced when the next line starts.
6. Saves the project, exports the MP4, and adds the clips it used to `--used`, so the next
   video gets different footage.

Run it once per audio with the same `--used` file and a different `--seed`. A different seed
gives a different set of clips; to swap one bad clip, list the clips you want with `--clips a.mp4
b.mp4 ...` (in order) instead of `--library`.

**Look at the result before sending it**: a contact sheet is the quick way.

```bash
ffmpeg -i out/video1.mp4 -vf "fps=1.5,scale=180:-1,tile=14x2" -frames:v 1 sheet.jpg
```

### Changing the look

`--style '{"font_size": 80, "block_top": 0.3}'` overrides any of these (values are for a
1080-wide frame and scale with the frame):

| Key | Default | Meaning |
|---|---|---|
| `font_size` | 96 | Text size |
| `stretch_x` | 0.955 | Slight squeeze, standing in for the references' tight letter spacing (text strips have no letter-spacing control) |
| `stretch_y` | 1.07 | Vertical stretch of the text |
| `weight` | 0.02 | Thickens the strokes with a hairline edge in the text's own colour; `0` for the plain font |
| `color` | `[0.98, 0.98, 0.98, 0.9]` | Text colour and opacity |
| `max_chars` | 14 | A row holds at most this many characters, spaces included (the references' line-break rule) |
| `block_width` | 0.48 | Width of the text block, as a share of the frame width (0.40–0.57 in the references, hand-set per phrase there) |
| `block_top` | 0.16 | Top of the block, as a share of the picture height from its top |
| `block_align_y` | `TOP` | `CENTER` centers the block in the picture, as the references do in their short picture band |
| `line_pitch` | 1.27 | Distance between rows, in text sizes (122 px) |
| `shadow_blur`, `shadow_color` | 0.3, black at 60 % | Faint soft shadow, as in the references |
| `outline_width` | 0 | A dark outline. Off: the references have none |
| `lowercase` | true | Show the lyrics in lowercase, without punctuation (apostrophes stay) |
| `lead_frames` | 1 | How many frames early a word appears (the references: 30–40 ms early) |

## Inside the editor, and for agents

The same steps are tools of the editor (`scripts/modules/vse_standalone/lyric_video.py`), so
they work from the control server at `http://127.0.0.1:8792`, from the MCP bridge, and from
scripts via `vse_standalone.api.call`:

| Tool | What it does |
|---|---|
| `make_lyric_video(audio, lyrics, clips, output, project, ...)` | The whole video in an empty project |
| `add_picture_clips(clips, picture_aspect)` | Clips one after another, each filling the picture area (`0.75` = 3:4 with bars; `0.5625` = full 9:16 frame) |
| `add_lyric_words(lyrics, style, ...)` | The timed lyric text on its own, over whatever is on the timeline |

`add_lyric_words` on its own is the way to put timed lyrics on an edit made by hand: cut the
picture yourself in the app, then call it once.

## Precision pass (17 November 2026): what changed and why

Feedback on the first three videos: lyric accuracy good; timing questionable; font close but
not close enough; an outline the references do not have; word spacing and line breaks not
like the references; clip variety should follow the subject folders by rule.

### Timing: three sources instead of one

```bash
# once: a second environment, for the vocal separator (PyTorch needs Python <= 3.12 on Intel)
/usr/local/bin/python3.11 -m venv stems-env
stems-env/bin/pip install "torch==2.2.2" "torchaudio==2.2.2" "numpy<2" demucs soundfile

# per audio
stems-env/bin/python  tools/vse_lyrics/separate_vocals.py audio1.wav -o audio1.vocals.wav
lyrics-env/bin/python tools/vse_lyrics/align_lyrics.py audio1.wav lyrics.txt \
    --vocals audio1.vocals.wav -o audio1.lyrics.json
```

1. **Vocal separation** (`separate_vocals.py`, Demucs, MIT licence). On the full mix, word
   starts were no closer to audio onsets than random times were: the beat drowns the voice.
   On the isolated voice both methods below improve.
2. **Forced alignment** (`forced_align.py`, wav2vec 2.0 base 960h, Apache-2.0, run with
   onnxruntime, 380 MB download). The words are known, so the model only places them, at
   20 ms resolution. Its confidence on singing is modest (median 0.15 on the mix, 0.37 on the
   voice), so it is not trusted alone.
3. **Voice level.** A word cannot start in silence. When recognizer and forced aligner
   disagree by more than 0.25 s, the one in silence loses; otherwise the one where the voice
   rises more wins. A word that follows a silence moves back to where the voice comes in.

`align_lyrics.py` prints which source timed how many words and every word moved by more than
0.2 s; the same is in the file under `"timing"`, and each word carries `source`,
`recognizer_start`, `forced_start` and `forced_confidence`. `--no-forced` gives the old
behaviour.

**Checking by ear**: `make_lyric_video.py --sync-check review.mp4` exports a second copy with
a tick on every sung word (the `add_sync_ticks` tool). A tick off its word is a mistimed word:
edit that word's `start` in the `.lyrics.json` and build again.

Measured from the references: words appear 30–40 ms before they are sung (one frame), and
the references themselves scatter by about ±70 ms per word.

### Style: measured from the four reference videos

See the style table above. The line-break rule (14 characters per row) reproduces 44 of 45
breaks checked in the references. Rendering a reference phrase through `add_lyric_words`
and laying it beside the reference frame matched word widths within about 1 % and the row
pitch exactly. Still different: the references' block width changes per phrase with no rule
found (it looks hand-set); 48 % is their average.

### Clips: chosen by subject folder, by rule

`make_lyric_video.py` now plans the clips before building (`plan_clips`):

1. **Subjects** are the folder names the clips sit in (`people`, `cars`, `urban`, `transit`,
   `misc luxury`, `fireworks`).
2. **Quotas** (`subject_quotas`): each video's slots are shared out by `--weights` (default
   `people=2`, the rest 1) with largest-remainder rounding; every subject gets at least one
   slot when there are enough slots. Leftover slots go to subjects that are behind their
   share over the earlier videos of the batch.
3. **Order** (`subject_order`): each subject's clips are spread evenly through the video,
   never the same subject twice in a row; the video opens on `--first` (default `people`,
   as the references do).
4. **Clip within the subject**: long enough for its slot, from the collection used least so
   far, then a seeded random choice. Never a clip an earlier video used.

The report (`--report`) lists the quota, the plan and the subject of every cut. The `--used`
file is now `{"videos": {name: [clips]}}`, so rebuilding one video frees its old clips.

### Where this stopped

- The three Assignment 17 videos were being rebuilt with all of the above when the session
  ended; outputs in `/Volumes/Leverett/latest-avenue-edit-working/assignment17/out/`
  (`out/review/` holds the tick copies, `out/v1_first_pass/` the first versions).
  **Not yet looked at frame by frame, and not listened to.** Check the contact sheets and
  the tick copies first.
- `tests/vse/test_vse.py`: 16 of 16 pass. No tests yet for `forced_align.py`,
  `refine_timing`, `plan_clips` or `add_sync_ticks`.
- Models and both Python environments are on the removable drive
  (`.../latest-avenue-edit-working/{models,lyrics-env,stems-env}`), not in the repository.

## What is not done yet

- **No headline.** The TikTok examples have a caption above the picture ("this song is gonna
  make 2026 feel like..."); the Assignment 17 brief gave no caption text, so none is added.
- **No look pass on the clips** (the examples' footage has a VHS feel; the library clips are
  used as they are). The Color pass / LUT tools apply if wanted.
- **Clips are picked by subject folder and length only**, not by what is in them. Check the
  contact sheet.
- **One-click button in the app.** The tools exist; there is no panel for them yet.
- Timing has not been checked by a person listening (use the tick copies).

## Files

| File | Purpose |
|---|---|
| `tools/vse_lyrics/align_lyrics.py` | Step 1: lyric timing and correction against the sheet |
| `tools/vse_lyrics/make_lyric_video.py` | Step 2 from the command line: clip picking, build, export |
| `scripts/modules/vse_standalone/lyric_video.py` | The editor tools and the "new style" layout values (`STYLE`) |
| `tests/vse/test_vse.py` | `test_lyrics_align_corrects_to_sheet`, `test_lyric_layout`, `test_make_lyric_video` |
