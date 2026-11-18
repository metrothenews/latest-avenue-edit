# Editor tools for the website: the contract

How the site's `/edit` page (latest-avenue repo) drives the assembly-cut workflow. Every tool
is a JSON-RPC method on the gateway:

```
POST /api/rpc
Authorization: Bearer <Firebase ID token>
{"jsonrpc": "2.0", "id": 1, "method": "<tool>", "params": {...}}
-> {"jsonrpc": "2.0", "id": 1, "result": {...}}          on success
-> {"jsonrpc": "2.0", "id": 1, "error": {"code", "message"}}   message is meant for the user
```

`{"method": "tools/list"}` returns every tool with its JSON schema (generated from the code, so
it is always current). The same tools are in the MCP bridge for the local model.

Paths: relative paths are inside the editor's media folder (`/data/media` in the container).
Times are seconds in the clip unless named `frame`. Line ids look like `S1-3` (scene 1, third
element); clip ids are the file name without extension.

## Long-running work: jobs

Tools that can take more than a few seconds return at once with a job, and the page polls:

```
start_assembly(...)            -> {"job": "a1b2c3d4", "output": "...", "log": "..."}
job_status(job="a1b2c3d4")     -> {"job", "kind", "state": "running" | "done" | "failed",
                                   "seconds", "log": [last lines], "result": {...} | null}
```

Poll every 1-2 s. `log` lines are short plain-English progress ("heard A001_C002.mov (2.1s)
...") the page can show as they come. `result` is set when `state` is `"done"`. Jobs live
until the editor restarts. (`assembly_job` is the older name of `job_status`; both work.)

## The flow, in order

| Step | Tool | Params | Result |
|---|---|---|---|
| 0 | `assembly_status` | none | `{python, packages, transcription, image_model, device, tools}`: is the PC set up |
| 1 | `fetch_drive_folder` → job | `folder_id` (the id, or the folder's drive.google.com link), `access_token` (see "Google Drive sign-in" below), `dest` (optional, default `drive/<folder_id>`) | job `result`: `{folder, files: [{name, path, bytes, skipped, kind}], fetched}`. Google Docs come down as PDF. Then use `drive/<folder_id>` as `media_folder` |
| 1 | `probe_media` | `filepath` (file or folder) | `{folder, files: [{path, name, kind: video/audio/image, duration, fps, width, height, timecode, audio_tracks, error?}]}` |
| 2 | `save_upload` | `name` (e.g. `script.pdf`), `data_base64` (≤ 700 KB decoded per call), `folder` (default `uploads`), `append` (send big files in parts: first call without, the rest with) | `{filepath, relative, bytes}`; pass `relative` (e.g. `uploads/script.pdf`) to `read_screenplay` / `start_assembly` |
| 2 | `read_screenplay` | `filepath` (.pdf/.fountain/.txt), `include_text` | `{title, characters: [{name, lines}], scenes: [{id, heading, int_ext, location, time, elements: [{id, type, character?, text, parentheticals?, extensions?}]}], dialogue_lines}` |
| 3-4 | `start_assembly` → job | `media_folder`, `output` (`*.edit.json`), `script` (optional; screenplay or numbered ad script, .pdf/.txt), `model`, `image_backend`, `visual_grouping`, `model_order`, `song` (music video: the song file; same as `music`, which also takes `""` for the longest sound file in the folder), `lyrics` (lyric sheet, with a song) | job `result`: summary (below). Includes sound sync, transcription, matching, grouping. With `music`, the song sets the timeline and pictures cut on the beat |
| 5 | `get_editing_script` | `filepath`, `format`: `summary` / `text` / `lines` / `json` | see below |
| 5 | `edit_editing_script` | `filepath`, `text` (whole screenplay text) | `{kept, moved, retrimmed, new, removed, found: [line ids], summary}` |
| 5 | `choose_take` | `filepath`, `line`, `take` (index, 0 = best) | `{line, take, cut}` |
| 5 | `take_thumbnails` | `filepath`, `line`, `width` (16-640, default 160), `inline` (default true) | `{line, takes: [{take, clip, chosen, score, heard, thumbnail, time, picture_clip, data_url}]}`: one JPEG per take, as a `data:` URL the page can show directly |
| 5 | `set_clip_sync` | `filepath`, `clip` (camera clip id), `audio_clip` (sound file id; default: the current one), `offset` (seconds, recorder = clip + offset), `nudge` (± seconds), `clear` | `{clip, sound: {clip, offset, method: "manual"} \| null}`; layout, render and exports use it at once |
| 5 | `set_line_cut` | `filepath`, `line`, `cut_in`, `cut_out` | `{line, cut}` |
| 6 | `lay_out_editing_script` | `filepath`, `channel`, `frame_start`, `replace`, `set_fps` | `{events, frame_end, fps, placed: [{line, frame_start, frame_end, strips}]}`; the editor's stream now shows the cut |
| 7 | existing tools | `apply_lut`, `set_strip_properties` (crop), game view | |
| 7 | `render_editing_script` | `filepath`, `output` (.mp4) | `{filepath, duration, events}` (ffmpeg, no timeline) |
| 7 | `render_video` | `filepath`, `format` | the fine-tuned timeline, rendered |
| 7 | `export_sequence` | `filepath` (editing script), `output`, `format`: `otio` / `fcpxml` / `xml` (Premiere, FCP7) / `edl` (default: from the extension) | `{filepath, format, events, duration}` |
| 7 | `upload_to_drive` → job | `filepath`, `access_token`, `folder_id` (optional: default the app's own "latest avenue exports" folder, made on first use), `name` | job `result`: `{id, name, bytes, folder_id}` |

### `get_editing_script` formats

- `summary`: `{title, scenes, dialogue_lines, lines_with_footage, lines_without_footage: [ids],
  events, duration, media, unplaced: [clip ids], groups: [{id, clips, scenes}], filepath}`
- `text`: `{filepath, text}`: the screenplay as text, in printed-screenplay columns (character
  names indented 22 spaces, dialogue 10). The user edits this; send it back whole with
  `edit_editing_script`.
- `lines`: `{filepath, lines: [{id, type: dialogue/action/transition, scene, heading, character,
  text, take, cut: {clip, in, out} | null, cover, takes: [{clip, in, out, score, heard,
  supervisor}]}]}`: one row per line with its footage and ranked alternatives (what the
  take-swap UI needs). `heard` is what was actually said in that take.
- `json`: the whole document (format in `scripts/modules/vse_standalone/assembly/editing_script.py`),
  including `media[clip].shots[].thumbnail` (JPEG paths for thumbnails) and word timings.

## Google Drive sign-in: what the site sends

Decided shape (the editor never sees a Google password, refresh token or client secret):

1. **The site gets the token.** At sign-in (or the first time the user opens "From Google
   Drive"), the site asks Google for two extra scopes with the Firebase Google provider:

   ```js
   const provider = new GoogleAuthProvider();
   provider.addScope("https://www.googleapis.com/auth/drive.readonly"); // read the chosen folder
   provider.addScope("https://www.googleapis.com/auth/drive.file");     // write exports it creates
   const result = await signInWithPopup(auth, provider);   // or reauthenticateWithPopup(user, provider)
   const driveToken = GoogleAuthProvider.credentialFromResult(result).accessToken;
   ```

   It is a Google **OAuth access token** (not the Firebase ID token): valid about an hour,
   never refreshed by Firebase. Keep it in memory only (a variable or React state), not in
   localStorage, cookies or the URL.
2. **The site sends it per call**, as the `access_token` parameter of `fetch_drive_folder` and
   `upload_to_drive`, in the JSON body of `POST /api/rpc` (HTTPS). The Firebase ID token stays in
   the `Authorization: Bearer` header as for every call: the gateway checks that one; the Drive
   token goes straight through to the job.
3. **The editor uses it once and forgets it**: it is put in the background job's environment
   (never on a command line, in a file or in a log), used for that one download or upload, and
   gone when the job ends. Nothing in the editor's logs or dataset records call parameters.
4. **When it has expired** (after about an hour, or access was withdrawn) the job fails with
   `error`/log text containing "sign in to Drive again". The page then calls
   `reauthenticateWithPopup` with the same provider for a fresh token and repeats the call.

Scopes: `drive.readonly` reads the folder the user names (any of their folders);
`drive.file` lets the editor create files only in folders it made itself, so exports go to
"latest avenue exports" in My Drive unless a folder the app made is named. Both are Google
"restricted/sensitive" scopes: fine for the owner's account while the Google Cloud project's
OAuth consent screen is in testing mode with Sam's address as a test user; offering it to the
public needs Google's verification.

The folder id: the last part of `https://drive.google.com/drive/folders/<id>`; the whole link is
accepted too.

## Tested end to end

`tests/vse/test_site_flow.py` makes these calls over `/api/rpc` exactly as the page would, in
the order above, against the real gateway and editor (signed in with a test certificate the
gateway is told to trust). It runs as the last step of the container test
(`docker run --rm latest-avenue-edit:test`): upload the script PDF in parts, start the
assembly, poll the job, read the editing script as text and lines, edit the text, swap a take,
take pictures, nudge a sync, lay it out, render, export all four formats, then a music video
from an uploaded song and lyric sheet. Use it as the reference for how the page should call.

## Errors

`error.message` is a sentence for the user ("No editing script at ...", "Line S1-9 has no
takes"). Code `-32000` for expected errors, `-32603` for bugs. HTTP `401` means the sign-in
token was rejected; `503` means the editor isn't up yet (retry in a few seconds).

## State of each tool

All built and tested on Linux. `fetch_drive_folder` and `upload_to_drive` are tested against a
stand-in Drive server only: the first real run is with a token from the site's Google sign-in
(scopes `drive.readonly`, and `drive.file` for uploads). See `doc/vse/assembly_workflow.md` for
what each step does inside.

### Music elements

In a music edit (`get_editing_script` → `summary` has no dialogue; the document has
`"mode": "music"`), every line is an `action` with `music: true`: its `cut` is the stretch of
the song, its `cover` the picture, and `takes` the other pictures that could go there.
`choose_take` swaps the picture and keeps the song.
