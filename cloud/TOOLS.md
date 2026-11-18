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
| 1 | `fetch_drive_folder` → job | `folder_id`, `access_token` (Google OAuth, `drive.readonly`; used once, not stored), `dest` (optional, default `drive/<folder_id>`) | job `result`: `{folder, files: [{name, path, bytes, skipped}]}`. Google Docs are exported as PDF |
| 1 | `probe_media` | `filepath` (file or folder) | `{folder, files: [{path, name, kind: video/audio/image, duration, fps, width, height, timecode, audio_tracks, error?}]}` |
| 2 | `read_screenplay` | `filepath` (.pdf/.fountain/.txt), `include_text` | `{title, characters: [{name, lines}], scenes: [{id, heading, int_ext, location, time, elements: [{id, type, character?, text, parentheticals?, extensions?}]}], dialogue_lines}` |
| 3-4 | `start_assembly` → job | `media_folder`, `output` (`*.edit.json`), `script` (optional), `model`, `image_backend`, `visual_grouping`, `model_order` | job `result`: summary (below). Includes sound sync, transcription, matching, grouping |
| 5 | `get_editing_script` | `filepath`, `format`: `summary` / `text` / `lines` / `json` | see below |
| 5 | `edit_editing_script` | `filepath`, `text` (whole screenplay text) | `{kept, moved, retrimmed, new, removed, found: [line ids], summary}` |
| 5 | `choose_take` | `filepath`, `line`, `take` (index, 0 = best) | `{line, take, cut}` |
| 5 | `set_line_cut` | `filepath`, `line`, `cut_in`, `cut_out` | `{line, cut}` |
| 6 | `lay_out_editing_script` | `filepath`, `channel`, `frame_start`, `replace`, `set_fps` | `{events, frame_end, fps, placed: [{line, frame_start, frame_end, strips}]}`; the editor's stream now shows the cut |
| 7 | existing tools | `apply_lut`, `set_strip_properties` (crop), game view | |
| 7 | `render_editing_script` | `filepath`, `output` (.mp4) | `{filepath, duration, events}` (ffmpeg, no timeline) |
| 7 | `render_video` | `filepath`, `format` | the fine-tuned timeline, rendered |
| 7 | `export_sequence` | `filepath` (editing script), `output`, `format`: `otio` / `fcpxml` / `xml` (Premiere, FCP7) / `edl` | `{filepath, format, events}` |

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

## Errors

`error.message` is a sentence for the user ("No editing script at ...", "Line S1-9 has no
takes"). Code `-32000` for expected errors, `-32603` for bugs. HTTP `401` means the sign-in
token was rejected; `503` means the editor isn't up yet (retry in a few seconds).

## State of each tool

Built and tested on Linux: everything above except `fetch_drive_folder`, `export_sequence` and
`job_status`, which are being added now (this file is updated as they land). See
`doc/vse/assembly_workflow.md` for what each step does inside.
