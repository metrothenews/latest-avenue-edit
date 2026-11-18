# Script supervisor notes: what they will add, and where it goes

**Not built yet.** This page lists what the notes can contain, what can go wrong reading them,
and where each piece will sit in the editing script, so the format doesn't have to change when
they are added. The slots already exist in every `*.edit.json` (all empty):

- `supervisor_notes` at the top: `{"status": "reserved", "source": null, "takes": {}}`
- `media[clip].supervisor` on every clip: `{circled, slate, take_number, camera, rating, notes, continuity}`
- `takes[n].supervisor` on every candidate take of every line: `null` for now

## What the notes are

A PDF (or a scan of handwritten sheets) from the script supervisor, usually per shooting day.
The usual parts:

1. **Daily progress report**: scenes shot, pages, set-ups, minutes of screen time.
2. **Editor's daily log / camera log**: one row per take: scene, set-up (shot letter), take,
   camera roll and clip name, timecode in/out, lens, duration, and a comment.
3. **Circled takes**: the takes the director wants printed or used ("circle 3 and 5"). Often a
   circle drawn around the take number, sometimes an asterisk or "P" (print), "NG" (no good).
4. **Lined script**: the script with vertical lines down the page, one per set-up, showing
   which lines each set-up covers. A wavy line means the character is off screen there.
5. **Facing pages**: notes opposite each script page, per take: what changed, what went
   wrong, line changes ("ad-lib: 'there's a diner' instead of 'there is a diner'").
6. **Continuity notes**: props, wardrobe, hair, which hand a cup is in, eyelines, screen
   direction, a door open or shut, wet or dry.

## How each part will be used

| From the notes | Where in the editing script | Effect |
|---|---|---|
| Circled take | `media[clip].supervisor.circled = true`, copied to `takes[n].supervisor.circled` | Ranks first among takes of similar score; a circled take is the default choice unless it's missing the line |
| NG / false start | `rating = "NG"` | Ranks last, still listed |
| Slate: scene, set-up, take, camera | `media[clip].supervisor.slate`, `take_number`, `camera` | Joins notes rows to clips (see matching below); groups set-ups exactly instead of by look |
| Lined script coverage | per line: which set-ups cover it | Fills in takes that the transcription missed (quiet line, overlap) and marks off-screen coverage (wavy line) as sound-only |
| Line changes / ad-libs | `takes[n].supervisor.notes` | Shown next to the take; explains a low match score instead of hiding the take |
| Continuity notes | `media[clip].supervisor.continuity` | Warnings when two adjacent lines come from takes with a conflicting note (cup in left hand vs right) |
| Timecode in/out per take | `media[clip].supervisor` | Cross-checks the clip's own timecode; splits a clip holding several takes (roll-through) |

Scoring stays code: a circled take gets a fixed bonus added to its match score, NG a fixed
penalty, and the existing near-tie rule (stay on the previous line's take) still applies.

## Matching a notes row to a clip, in order of trust

1. **Clip name or camera roll + clip number** in the log (`A001_C002`) equals the file name.
2. **Timecode**: the row's timecode in/out contains the clip's start timecode (`ffprobe`
   reads it; same camera).
3. **Spoken slate**: the assembly already hears "scene one, take two" at the head of a take
   (`media[clip].slate`, see `heard_slate` in `editing_script.py`).
4. **Script lines covered**: the row's scene, and the lines the lined script gives the set-up,
   against the lines the take was matched to.
5. **Order**: rows and clips for one scene, in shooting order.

A row that matches no clip, or a clip claimed by two rows, is reported, never guessed.

## Contingencies to handle

- **Scanned or handwritten sheets.** `pdftotext` returns nothing on an image-only PDF.
  Then: OCR (Tesseract, `pdftoppm` + `tesseract`, still no language model). Circles drawn by
  hand don't survive OCR: look for a circle around a number with image processing (a closed
  contour around a digit's box), or ask the user to confirm circled takes in the game view.
- **Every supervisor's layout differs.** Rows are found by patterns, not fixed columns:
  `\b(\d+[A-Z]*)\s*[-/ ]?\s*([A-Z])?\s*(?:T|TK|TAKE)?\s*(\d+)\b` for scene / set-up / take,
  timecodes `\d\d:\d\d:\d\d[:;]\d\d`, clip names `[A-Z]\d{3}_?C\d{3}`.
- **Scene numbering.** Inserted scenes (`12A`), omitted scenes ("OMITTED"), scenes renumbered
  between drafts. The screenplay parser keeps scene numbers as text (`"number": "12A"`).
- **Set-up letters** skip I and O; "12B" may be a set-up of scene 12 or scene 12B. Prefer the
  log's own column when there is one.
- **Pickups ("PU") and series takes**: one clip, several attempts at the same line. The
  matcher already finds repeats inside a take; the notes say which attempt was circled.
- **Wild lines / wild sound**: sound only, no picture; they become audio-only takes.
- **Multiple cameras** (A and B camera on one take): two clips, one slate. Same `take_number`,
  different `camera`. Both become takes of the line; the circle applies to both.
- **Second unit / inserts / B-roll** without dialogue: rows with no lines; they become
  cover options for the scene's action lines.
- **Notes for days not shot / footage without notes**: both are normal. Anything without a
  note keeps its computed rank.
- **Conflicts**: the notes say take 3 is circled but take 3 doesn't contain the line (wrong
  scene, or a mislabelled slate). Keep the match result, show the conflict.
- **Several notes files** (one per day): merged by scene and take; a later note for the same
  take wins and the earlier one is kept in `notes`.

## When it's built

A `read_supervisor_notes(filepath, editing_script)` tool: `pdftotext -layout`, the patterns
above, matching rows to clips in the order above, filling the slots, re-ranking. No model,
except possibly OCR for scans. Its test: a generated notes PDF for the sample shoot in
`tests/vse/assembly/make_samples.py` (circle take 2 of scene 1, NG the close-up), checking the
ranks change accordingly.
