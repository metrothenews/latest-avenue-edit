# Reel workflow: from five inputs to a finished edit, one decision at a time

Reference: `henrytcwang` Instagram reel "the art of being chalant" (23.3 s, 24 fps, 1348×1078).
Goal: produce an edit of this kind from only

1. **footage** (a folder of clips),
2. **a script**,
3. **a voice recording** (the script read aloud, any number of takes),
4. **a music track**,
5. **a sound design library** (folders of SFX),

where **the machine prepares options and a person makes every editing decision** in the
game-ified interface: one decision on screen at a time, options on the number keys,
`space` to accept and move on, the same as the Color pass.

---

## 1. What the reference edit does

Measured with shot detection plus frame-by-frame review. Script (from the captions):
*"the art of being chalant. Serious. Nowadays we look up influencers who glorify being
nonchalant, and it makes us boring. Be unique, care about something, laugh too loud, try too
hard, 'cuz who cares. Become the very best version of yourself, because that's the art of being
chalant."*

### Shots (12)

| # | Time | Shot | What's done to it |
|---|---|---|---|
| 1 | 0.00–4.21 | Selfie, looking at phone (pink/brown room) | Push-in (scale ramp, wide to tight); title builds word by word; at ~3.5 s a **sticker cut-out** (subject isolated with a white/grey outline over the background) |
| 2 | 4.21–5.67 | At desk, hand on head, back to camera | **Giant red "serious" with white outline, behind the subject's head** (text-behind-subject) |
| 3 | 5.67–9.29 | Phone filming a laptop screen of influencer clips | B-roll; screen content changes several times inside the shot (cuts are in the source) |
| 4 | 9.29–9.79 | Desk, wide | **"BORING"** in a distressed display face, white |
| 5 | 9.79–10.17 | Same desk, other angle | "BORING" held across the cut (text carries over two shots) |
| 6 | 10.17–11.29 | Wide-angle face, screaming, headphones | Shaky handheld; "be **unique**", second word in an italic serif |
| 7 | 11.29–12.63 | Hands together, head bowed | "care about something"; outgoing transition with a big blurred word ("more") |
| 8 | 12.63–13.29 | Laughing, holding a camera (bright, cool) | "laugh too loud"; deliberately different look (bright white room) |
| 9 | 13.29–15.04 | Head down on desk, wide | "try too hard", "cuz", then **"who cares"** big (white/red, heavy shadow) |
| 10 | 15.04–18.00 | Desk seen through a doorway | Text in the dark door frame: "become / the very", then **"BEST version of yourself"** (mixed faces, red) |
| 11 | 18.00–19.17 | Profile close-up | "because that's the art", key words in orange/red |
| 12 | 19.17–23.29 | Same setup as shot 1 (**bookend**) | End card: "chalant" fades in grey → white → red → white, small red tagline underneath |

### Editing steps, from most basic to most complex

**Basic**
1. Conform: vertical-ish delivery (1348×1078 here; 1080×1350 / 1080×1920 for Reels), 24 fps.
2. Lay the voice-over (VO) and trim silences/breaths so it plays continuously.
3. Pick one shot per script phrase; cut on the phrase.
4. Choose in-points (the best moment of each clip).
5. Bookend: open and close on the same setup.
6. Music bed under the VO, ducked.

**Intermediate**

7. Cut timing, frame-accurate to words (cuts land on or just before stressed words).
8. Reframe and scale per shot (crops, punch-ins), including a slow push-in on shot 1.
9. Color: a consistent warm/pink look, with one deliberate exception (shot 8).
10. Word-by-word captions synced to the VO (small, white, bold, centered).
11. Emphasis words set big, in a different typeface and color (red/white), one per phrase.
12. Text that persists across a cut (shot 4 → 5).
13. Text placed in the frame's negative space (shot 10, door frame).

**Advanced**

14. Kinetic type: words appear one at a time on the VO timing, some with color changes over time (end card grey → white → red → white).
15. Mixed typography per phrase (sans + italic serif + distressed display).
16. Sound design: whooshes/pops/hits on cuts and emphasis words, risers into the final card (inferred from the edit style; confirm by ear).
17. Music edited to the VO: section choice, beat-aligned cuts, ending on a musical resolve.

**Complex** (needs a subject matte, i.e. AI segmentation of the person)

18. **Text behind subject** (shot 2: "serious" sits behind the head).
19. **Sticker cut-out** (shot 1: subject with an outline stroke over the background).

---

## 2. The workflow

Principle: **ingest is automatic, every editing decision is a pass in the game UI.**
Each pass works like the Color pass:

- One item on screen (a script line, a cut, a word, a clip).
- Up to **8 options** on `1`–`4` / `7`–`0`, previewed instantly (rendered by the editor).
- `⌫` = "none" option, `space` = accept and go to the next item, `←` `→` = move without
  deciding, `tab` = switch between linked passes.
- What you accept is written to the timeline immediately (one undo step per decision),
  so the full editor always shows the current state and you can step in at any time.
- Passes can be re-entered later; changing an earlier decision only redoes the passes it
  affects (e.g. changing a shot keeps its captions).

### Step 0: Ingest (automatic, no decisions)

| Input | Analysis | Produces |
|---|---|---|
| Script | Split into lines (sentences/phrases) and words; mark candidate emphasis words (capitalized, rare, last stressed word) | **Beats**: the units every later pass steps through |
| Voice recording | Speech-to-text with word timestamps, aligned to the script; take detection when lines were read more than once; silence/breath detection | Per word: start/end time, take; per line: takes |
| Footage | Shot detection inside long clips; per clip: thumbnails, motion, faces, brightness/color stats, on-camera speech transcript; **subject matte** (person segmentation) for clips used with complex effects | Searchable clip library with candidate moments |
| Music | Tempo, beats, downbeats, sections (intro/build/drop), loudness | Beat grid + candidate start offsets |
| SFX library | Index by folder/filename tags (whoosh, hit, pop, riser, impact, glitch, click) + duration + loudness | Tagged SFX pool |

### Step 1: VO pass (one item per script line)

- Options: the **takes** of that line, 1–8 (preview = audio plays, waveform shown).
- Second key layer (`tab`): **tightening**: gap after this line: keep / -25% / -50% / tight
  (ripple-deletes the silence with `ripple_delete_range`).
- Result: a clean VO track. Its word timings become the edit's timebase.

### Step 2: Shot pass (one item per beat)

- Options: 8 **candidate clips** for that line, ranked by match to the line (transcript match
  for A-roll; visual tags/script keywords for B-roll; variety vs. the previous shot), shown
  as looping previews at the exact length the beat needs.
- `⌫` = keep the previous shot running (no cut here), which is how shot 4 → 5 carry-overs or
  long takes happen.
- Special option on the last beat: "bookend" (reuse the opening setup).

### Step 3: Moment pass (one item per placed shot)

- Options: 8 candidate **in-points** across the clip (best faces, peaks of motion, start of
  action), previewed as loops. `←` `→` nudge by 6 frames, `shift` + arrows by 1 frame.

### Step 4: Cut pass (one item per cut)

- Options: cut offset relative to the word: −6, −4, −2, 0, +2, +4 frames, **snap to beat**,
  **snap to word start**. Preview loops ±1 s around the cut with audio.

### Step 5: Framing pass (one item per shot)

- Options: scale presets (1.0, 1.15, 1.34, fill), **push-in** (slow ramp over the shot),
  **punch-in on the emphasis word**, **center on face**, and **shake** for energetic shots.
- Same as the Crop tab, per shot, with keyframed versions.

### Step 6: Look and Color passes (existing)

- The current Color tab: Look LUTs, then Color LUTs, per shot. Shot 8's "different look"
  is simply a different choice here.

### Step 7: Caption pass (one item per line)

- Words are placed automatically on the VO timing (word-by-word, small white bold).
- Options per line: **caption style** presets 1–8: plain kinetic, big red display with
  outline ("serious"), distressed white ("BORING"), sans + italic serif ("be *unique*"),
  heavy shadow ("who cares"), negative-space placement ("become the very"), mixed red/white
  ("BEST version"), end card.
- Second layer (`tab`): **emphasis word**: `←` `→` moves the highlight between words of the
  line, `space` accepts.
- Third layer: **position**: 8 positions (thirds grid + "in negative space", detected from
  the frame).

### Step 8: Effects pass (one item per beat that has an emphasis)

- Options: none, **text behind subject**, **sticker cut-out**, flash frame, blur-out word
  transition ("more"), color pop on the word, hold text over the next cut, glitch.
- Effects that need a subject matte render it on first use (cached per clip).

### Step 9: Music pass

- Item 1: **where the music starts**: 8 candidate offsets on downbeats, previewed under the VO.
- Item 2: **ending**: resolve the music on the final card (time-stretch the last bar or cut to
  the outro): 4–8 candidates.
- Item 3: **ducking level** under the VO: 4 presets.

### Step 10: Sound design pass (one item per cut and per emphasis word)

- Options: 8 SFX from the library chosen by tag for that moment (cut → whoosh/swipe,
  emphasis word → hit/pop, end card → riser/impact), previewed in context. `⌫` = none.
- Second layer: level (−12, −6, 0 dB) and offset (−2, 0, +2 frames).

### Step 11: Mix and export

- Options: 4 mix presets (VO-forward, balanced, music-forward, loud/short-form normalized
  to −14 LUFS); export presets (Reels 1080×1920, 4:5 1080×1350, square).

---

## 3. What the engine needs (and what already exists)

| Capability | Status |
|---|---|
| Timeline, cuts, trims, ripple delete / cut / delete range, markers | **Done** (tools in `vse_standalone.api`) |
| LUT look + color passes, previews rendered by the editor | **Done** (`color_pass`) |
| Crop/zoom all clips | **Done**; per-shot framing presets and keyframed push-in: to build |
| Text strips (font, size, color, outline, shadow, box, position) | In the editor; word-by-word caption generator and style presets: to build |
| Generic "pass" framework in the game UI (item list, 8 options, preview, accept) | To build; the Color pass becomes one instance of it |
| Speech-to-text with word timestamps, script alignment, take detection | To build (local model, e.g. Whisper; runs on the user's machine) |
| Shot detection, thumbnails, face/motion stats | To build (FFmpeg is in the editor; faces need a small model) |
| Music beat/downbeat/section analysis | To build (numpy, bundled) |
| SFX library index and tagging | To build (filenames + loudness; optional audio-tagging model) |
| Subject matte (person segmentation) for text-behind-subject and sticker cut-out | To build (segmentation model, writes a mask sequence the Mask modifier uses) |
| Audio ducking, loudness normalization | Partly (strip volume + keyframes); presets to build |

Build order that gets a full first edit soonest: pass framework → VO pass → shot pass →
cut pass → caption pass → music pass → SFX pass → framing → effects (matte last).

All analysis runs locally inside the app, so nothing about a user's footage leaves their
machine, and every pass is also a set of tools, so an agent can propose options or run a
pass end to end when that's wanted.
