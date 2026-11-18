# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Editor tools for the assembly cut (``vse_standalone.assembly``): read a screenplay, build an
editing script from footage, edit it as text, swap takes, lay it out on the timeline, and
render it with ffmpeg. Every tool is also reachable through the control server, the MCP
bridge and the cloud gateway's ``/api/rpc``.

Reading the screenplay, editing the editing script and laying it out run in the editor's own
Python. Transcription and picture grouping need their own environment
(``tools/vse_assembly/requirements.txt``); :func:`start_assembly` runs that in the background
with the interpreter named by ``VSE_ASSEMBLY_PYTHON`` and returns a job id to poll with
:func:`assembly_job`, so the editor never freezes.
"""

import json
import os
import subprocess
import sys
import time
import typing
import uuid

import bpy

from . import api
from .api import tool
from .assembly import editing_script, probe, render, screenplay

_jobs = {}
MODULES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _path(filepath):
    """An existing file or folder: absolute, relative to the project, or in the media folder."""
    path = bpy.path.abspath(filepath)
    if not os.path.isabs(filepath) and not os.path.exists(path):
        candidate = os.path.join(api.media_root(), filepath)
        if os.path.exists(candidate):
            return candidate
    if not os.path.exists(path):
        raise api.APIError("File not found: {:s}".format(filepath))
    return path


def _out_path(filepath):
    """A file to write: absolute, or relative to the media folder."""
    path = bpy.path.abspath(filepath)
    if not os.path.isabs(path):
        path = os.path.join(api.media_root(), filepath)
    return path


def _load(filepath):
    path = _path(filepath)
    try:
        return path, editing_script.load(path)
    except ValueError as ex:
        raise api.APIError(str(ex)) from None


def _python():
    return os.environ.get("VSE_ASSEMBLY_PYTHON") or sys.executable


# -----------------------------------------------------------------------------
# Inputs.

@tool(mutates=False)
def probe_media(filepath: str) -> dict:
    """Length, frame rate, size, timecode and audio tracks of a media file, or of every media
    file in a folder (ffprobe).

    :arg filepath: A file or folder; relative paths are looked up in the media folder.
    """
    path = _path(filepath)
    if os.path.isdir(path):
        return {"folder": path, "files": probe.scan(path)}
    try:
        return probe.probe(path)
    except RuntimeError as ex:
        raise api.APIError(str(ex)) from None


@tool(mutates=False)
def read_screenplay(filepath: str, include_text: bool = False) -> dict:
    """Read a screenplay (.pdf, .fountain, .txt) into scenes, action, characters and dialogue.

    Rules only (pdftotext -layout + screenplay conventions), no model.
    :arg include_text: Also return the pdftotext layout text, to check the reading.
    """
    path = _path(filepath)
    try:
        result = screenplay.parse(path)
        if include_text and path.lower().endswith(".pdf"):
            result["layout_text"] = screenplay.pdf_to_text(path)
    except (RuntimeError, OSError) as ex:
        raise api.APIError(str(ex)) from None
    result["dialogue_lines"] = len(screenplay.dialogue_lines(result))
    return result


# -----------------------------------------------------------------------------
# Building (background job in the assembly's own Python).

@tool(mutates=False)
def assembly_status() -> dict:
    """Whether the assembly environment is set up: its Python, and which models it can use."""
    code = ("import importlib.util as u, json; print(json.dumps({m: bool(u.find_spec(m)) for m in "
            "('faster_whisper', 'rapidfuzz', 'numpy', 'open_clip', 'torch')}))")
    try:
        out = subprocess.run([_python(), "-c", code], capture_output=True, text=True, timeout=60)
        found = json.loads(out.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired) as ex:
        found = {"error": str(ex)}
    import shutil
    return {"python": _python(), "packages": found,
            "transcription": bool(found.get("faster_whisper")),
            "image_model": "MobileCLIP2-S0" if found.get("open_clip") and found.get("torch") else "signature",
            "tools": {t: bool(shutil.which(t)) for t in ("ffmpeg", "ffprobe", "pdftotext")},
            "setup": "VSE_ASSEMBLY_PYTHON=<venv>/bin/python with tools/vse_assembly/requirements.txt"}


@tool(mutates=False)
def start_assembly(media_folder: str, output: str, script: typing.Optional[str] = None,
                   model: typing.Optional[str] = None,
                   image_backend: typing.Optional[typing.Literal["mobileclip", "signature"]] = None,
                   visual_grouping: bool = True, model_order: bool = True,
                   music: typing.Optional[str] = None, lyrics: typing.Optional[str] = None,
                   song: typing.Optional[str] = None) -> dict:
    """Start building an editing script (assembly cut) from footage and an optional screenplay.

    Runs in the background: transcribes every take, matches takes to script lines, groups
    clips by look. Poll with assembly_job. Without a script a rough one is made from speech.
    :arg media_folder: Folder of footage, photos and audio.
    :arg output: Where to write the editing script (.edit.json).
    :arg script: Screenplay .pdf/.fountain/.txt.
    :arg model: faster-whisper model (default base.en).
    :arg model_order: Without a script, ask the language model once for the scene order.
    :arg music: Music-driven edit (music video): the song to cut to, or "" for the longest
      sound file in the folder. Pictures change on the beat.
    :arg lyrics: With music: the lyric sheet (.txt or .pdf); each line starts a new shot.
    :arg song: Same as music (the song file); either turns on the music-driven edit.
    """
    if song is not None and music is None:
        music = song
    media = _path(media_folder)
    if not os.path.isdir(media):
        raise api.APIError("Not a folder: {:s}".format(media))
    out = _out_path(output)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    args = [_python(), "-m", "vse_standalone.assembly", "assemble", media, "-o", out]
    if script:
        args += ["--script", _path(script)]
    if model:
        args += ["--model", model]
    if image_backend:
        args += ["--image-backend", image_backend]
    if not visual_grouping:
        args.append("--no-visual")
    if not model_order:
        args.append("--no-model-order")
    if music is not None:
        args += ["--music"] + ([_path(music)] if music else [])
        if lyrics:
            args += ["--lyrics", _path(lyrics)]
    return _start_job("assembly", args, out + ".log", out, _assembly_result)


def _assembly_result(entry):
    return editing_script.summary(editing_script.load(entry["output"]))


def _start_job(kind, args, log, output, on_done=None, env_extra=None, stdout=None):
    """Run a command of the assembly's own Python in the background. -> job descriptor.

    env_extra: secrets (an access token) go to the child's environment, never its arguments.
    stdout: file for the command's JSON result.
    """
    job = uuid.uuid4().hex[:8]
    env = dict(os.environ, PYTHONPATH=MODULES_DIR + os.pathsep + os.environ.get("PYTHONPATH", ""),
               PYTHONUNBUFFERED="1", **(env_extra or {}))
    fh = open(log, "w", encoding="utf-8")
    out = open(stdout, "w", encoding="utf-8") if stdout else subprocess.DEVNULL
    proc = subprocess.Popen(args, stdout=out, stderr=fh, env=env)
    if stdout:
        out.close()
    _jobs[job] = {"kind": kind, "proc": proc, "log": log, "output": output,
                  "started": time.time(), "fh": fh, "on_done": on_done, "result": None}
    return {"job": job, "kind": kind, "output": output, "log": log}


@tool(mutates=False)
def job_status(job: str) -> dict:
    """Progress of a background job (start_assembly, fetch_drive_folder): running/done/failed,
    the last log lines, and its result when done."""
    entry = _jobs.get(job)
    if entry is None:
        from .assembly import imports
        try:
            return _import_job_status(imports.get(job))
        except KeyError:
            pass
        raise api.APIError("No job {!r} (jobs are forgotten when the editor restarts)".format(job))
    code = entry["proc"].poll()
    try:
        with open(entry["log"], encoding="utf-8") as fh:
            log = [l.rstrip() for l in fh if l.strip() and "HF_TOKEN" not in l
                   and "Warning:" not in l][-40:]
    except OSError:
        log = []
    state = "running" if code is None else "done" if code == 0 else "failed"
    if code is not None and not entry["fh"].closed:
        entry["fh"].close()
    if state == "done" and entry["result"] is None and entry["on_done"]:
        try:
            entry["result"] = entry["on_done"](entry)
        except Exception as ex:  # a result that can't be read is a failure
            state, log = "failed", log + ["could not read the result: {:s}".format(str(ex))]
    return {"job": job, "kind": entry["kind"], "state": state,
            "seconds": round(time.time() - entry["started"], 1), "output": entry["output"],
            "log": log, "result": entry["result"] if state == "done" else None}


@tool(mutates=False)
def assembly_job(job: str) -> dict:
    """Older name of job_status."""
    result = job_status(job)
    if result["state"] == "done":
        result["summary"] = result["result"]
    return result


# -----------------------------------------------------------------------------
# The editing script.

@tool(mutates=False)
def get_editing_script(filepath: str, format: typing.Literal["summary", "text", "json", "lines"] = "summary") -> dict:
    """Read an editing script.

    :arg format: "summary"; "text" (screenplay text to edit); "lines" (each line with its
      footage and ranked takes, without word timings); "json" (everything).
    """
    path, doc = _load(filepath)
    if format == "text":
        return {"filepath": path, "text": editing_script.to_text(doc)}
    if format == "json":
        return doc
    if format == "lines":
        out = []
        for scene in doc["scenes"]:
            for el in scene["elements"]:
                item = {k: el.get(k) for k in ("id", "type", "character", "text", "cut", "cover", "take")}
                item["scene"], item["heading"] = scene["id"], scene.get("heading")
                item["takes"] = [{k: t.get(k) for k in ("clip", "in", "out", "score", "heard", "supervisor")}
                                 for t in el.get("takes") or []]
                out.append(item)
        return {"filepath": path, "lines": out}
    return dict(editing_script.summary(doc), filepath=path)


@tool(mutates=False)
def edit_editing_script(filepath: str, text: str) -> dict:
    """Replace the editing script's text (screenplay layout, as from get_editing_script with
    format "text"). Lines keep their footage wherever they move; shortened lines are
    re-trimmed; new lines are looked up in what was said in the takes."""
    path, doc = _load(filepath)
    new, report = editing_script.apply_text(doc, text)
    editing_script.save(new, path)
    return dict(report, summary=editing_script.summary(new))


@tool(mutates=False)
def choose_take(filepath: str, line: str, take: int) -> dict:
    """Play a line of the editing script from another take.

    :arg line: Line id, e.g. "S1-3".
    :arg take: Index into the line's ranked takes (0 = best).
    """
    path, doc = _load(filepath)
    try:
        el = editing_script.choose_take(doc, line, take)
    except (KeyError, ValueError) as ex:
        raise api.APIError(str(ex).strip("'\"")) from None
    editing_script.save(doc, path)
    return {"line": el["id"], "take": el["take"], "cut": el["cut"]}


@tool(mutates=False)
def set_line_cut(filepath: str, line: str, cut_in: typing.Optional[float] = None,
                 cut_out: typing.Optional[float] = None) -> dict:
    """Fine-tune where a line starts and ends in its clip (seconds in the clip)."""
    path, doc = _load(filepath)
    try:
        el = editing_script.set_cut(doc, line, cut_in, cut_out)
    except (KeyError, ValueError) as ex:
        raise api.APIError(str(ex).strip("'\"")) from None
    editing_script.save(doc, path)
    return {"line": el["id"], "cut": el["cut"]}


# -----------------------------------------------------------------------------
# On the timeline.

@tool()
def lay_out_editing_script(filepath: str, channel: int = 1, frame_start: int = 1,
                           replace: bool = True, set_fps: bool = True) -> dict:
    """Lay the editing script out on the timeline: one strip per line, cut to its in/out
    points, picture on `channel`, sound on the channel above, a marker at each scene.

    Strips are named after their line (e.g. "S1-3 A001_C002"). One undo step.
    :arg replace: Remove the strips this editing script laid out before.
    :arg set_fps: Set the project frame rate to the footage's.
    """
    path, doc = _load(filepath)
    scene = api._scene()
    fps = float(doc.get("fps") or 24)
    if set_fps:
        rate = round(fps)
        base = rate / fps
        scene.render.fps, scene.render.fps_base = rate, round(base, 6)
    fps = scene.render.fps / scene.render.fps_base
    if scene.sequence_editor is None:
        scene.sequence_editor_create()
    strips = scene.sequence_editor.strips
    events = editing_script.timeline(doc)
    if replace:
        for strip in list(scene.sequence_editor.strips_all):
            if strip.get("editing_script") == path:
                strips.remove(strip)
        for marker in list(scene.timeline_markers):
            if marker.name.startswith("§ "):
                scene.timeline_markers.remove(marker)

    frame = frame_start
    placed, last_scene = [], None
    for ev in events:
        length = max(1, int(round((ev["out"] - ev["in"]) * fps)))
        label = "{:s} {:s}".format(ev["element"], (ev["picture"] or {}).get("clip") or ev["clip"])[:60]
        made = []
        pic = ev["picture"]
        if pic:
            kind = doc["media"][pic["clip"]]["kind"]
            if kind == "image":
                s = strips.new_image(name=label, filepath=pic["path"], channel=channel, frame_start=frame)
                s.frame_final_duration = length
            else:
                start = frame - int(round(pic["in"] * fps))
                s = strips.new_movie(name=label, filepath=pic["path"], channel=channel, frame_start=start)
                s.left_handle, s.right_handle = frame, frame + length
            made.append(s)
        if ev["sound"]:
            start = frame - int(round(ev["sound_in"] * fps))
            try:
                a = strips.new_sound(name=label + " (audio)", filepath=ev["sound_path"],
                                     channel=channel + 1, frame_start=start)
                a.left_handle, a.right_handle = frame, frame + length
                made.append(a)
            except (RuntimeError, TypeError):
                pass
        for s in made:
            s["editing_script"] = path
            s["editing_script_line"] = ev["element"]
        if ev["scene"] != last_scene:
            heading = next((s.get("heading") for s in doc["scenes"] if s["id"] == ev["scene"]), None)
            scene.timeline_markers.new(name="§ {:s} {:s}".format(ev["scene"], heading or "")[:60], frame=frame)
            last_scene = ev["scene"]
        placed.append({"line": ev["element"], "frame_start": frame, "frame_end": frame + length,
                       "strips": [s.name for s in made]})
        frame += length
    scene.frame_start, scene.frame_end = frame_start, max(frame_start, frame - 1)
    scene["editing_script"] = path
    return {"filepath": path, "events": len(placed), "frame_end": frame - 1, "fps": round(fps, 3),
            "placed": placed}


@tool(mutates=False)
def render_editing_script(filepath: str, output: str, width: typing.Optional[int] = None,
                          height: typing.Optional[int] = None) -> dict:
    """Render the editing script straight to an MP4 with ffmpeg (no timeline needed): a quick
    look at the assembly. For the fine-tuned edit use render_video."""
    path, doc = _load(filepath)
    out = _out_path(output)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    try:
        return render.render(doc, out, width=width, height=height)
    except (RuntimeError, ValueError) as ex:
        raise api.APIError(str(ex)) from None


@tool(mutates=False)
def export_sequence(filepath: str, output: str,
                    format: typing.Optional[typing.Literal["otio", "fcpxml", "xml", "edl"]] = None) -> dict:
    """Export the editing script as a sequence for another editor (OpenTimelineIO).

    :arg output: .otio (Resolve, OTIO), .fcpxml (Final Cut Pro, Resolve), .xml (FCP 7 XML:
      Premiere Pro, Resolve) or .edl (CMX 3600, picture only).
    :arg format: Overrides the format chosen from the file extension.
    """
    path, _doc = _load(filepath)
    out = _out_path(output)
    args = [_python(), "-m", "vse_standalone.assembly", "export", path, "-o", out]
    if format:
        args += ["--format", format]
    env = dict(os.environ, PYTHONPATH=MODULES_DIR + os.pathsep + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=300)
    if proc.returncode != 0:
        last = (proc.stderr.strip().splitlines() or ["export failed"])[-1]
        raise api.APIError("Sequence export failed: " + last)
    return json.loads(proc.stdout)


# -----------------------------------------------------------------------------
# Google Drive.

def _json_result(entry):
    with open(entry["output"], encoding="utf-8") as fh:
        return json.load(fh)


@tool(mutates=False)
def fetch_drive_folder(folder_id: str, access_token: str, dest: typing.Optional[str] = None) -> dict:
    """Download a whole Google Drive folder into the media folder (older tool: the same import
    queue as import_start, with the folder's files placed directly in dest).

    Poll with job_status (or import_status). Files already imported are skipped.
    :arg folder_id: The folder's id (or its drive.google.com link).
    :arg access_token: A Google OAuth access token with drive.readonly. Kept in memory for this import only.
    :arg dest: Local folder (default: drive/<folder_id> in the media folder).
    """
    from .assembly import imports
    folder_id = folder_id.strip().rstrip("/").rsplit("/", 1)[-1].split("?")[0]
    out = _out_path(dest or os.path.join("drive", folder_id))
    try:
        job = imports.start(access_token, [{"drive_id": folder_id, "kind": "folder", "name": folder_id,
                                            "recursive": True, "flat": True}], dest=out)
    except ValueError as ex:
        raise api.APIError(str(ex)) from None
    return {"job": job.id, "job_id": job.id, "kind": "fetch_drive_folder", "output": out}


def _import_job_status(job):
    """An import job seen through job_status (for fetch_drive_folder callers)."""
    st = job.status()
    state = {"done": "done", "failed": "failed", "cancelled": "failed"}.get(st["state"], "running")
    files = [{"name": os.path.basename(it.get("path") or "") or it["name"], "path": it.get("path"),
              "bytes": it.get("size"), "skipped": it["state"] == "skipped", "drive_id": it["drive_id"]}
             for it in job.items if it["kind"] == "file" and it["state"] in ("done", "skipped")]
    log = ["{:s}: {:s}".format(it["name"], it["state"] + (" ({:s})".format(it["error"]) if it.get("error") else ""))
           for it in job.items if it["kind"] == "file"][-40:]
    if st["state"] == "needs_token":
        log.append("sign in to Drive again")
    return {"job": job.id, "kind": "import", "state": state, "seconds": round(time.time() - job.created, 1),
            "output": job.dest, "log": log,
            "result": {"folder": job.dest, "files": files,
                       "fetched": sum(1 for f in files if not f["skipped"])} if state == "done" else None,
            "import": st}


# -----------------------------------------------------------------------------
# The import queue (cloud/TOOLS.md, "Import queue").

def _import(job_id):
    from .assembly import imports
    try:
        return imports.get(job_id)
    except KeyError as ex:
        raise api.APIError(str(ex).strip("'\"")) from None


def _items_arg(items):
    out = []
    for it in items or []:
        if not isinstance(it, dict) or not it.get("drive_id"):
            raise api.APIError("Each item needs a drive_id (and kind: file or folder)")
        kind = it.get("kind", "file")
        if kind not in ("file", "folder"):
            raise api.APIError("kind must be 'file' or 'folder'")
        out.append({"drive_id": str(it["drive_id"]).strip().rstrip("/").rsplit("/", 1)[-1].split("?")[0],
                    "kind": kind, "name": it.get("name"), "recursive": it.get("recursive", True)})
    return out


@tool(mutates=False)
def import_start(access_token: str, items: typing.List[dict], proxies: bool = True) -> dict:
    """Start importing files and folders from Google Drive into the media folder (drive/).
    One file at a time, in order; folders are expanded (media and PDFs only, recursively).

    :arg access_token: Google OAuth access token (drive.readonly). Kept in memory only.
    :arg items: [{drive_id, kind: "file"|"folder", name, recursive}].
    :arg proxies: Also make a 140p copy of each video for light thumbnails.
    """
    from .assembly import imports
    try:
        job = imports.start(access_token, _items_arg(items), proxies=proxies)
    except ValueError as ex:
        raise api.APIError(str(ex)) from None
    return {"job_id": job.id}


@tool(mutates=False)
def import_status(job_id: str) -> dict:
    """State of an import: running / paused / needs_token / cancelled / done / failed, totals,
    and every item with its progress."""
    return _import(job_id).status()


@tool(mutates=False)
def import_pause(job_id: str) -> dict:
    """Pause an import. The file in flight stops; its partial download is kept."""
    return _import(job_id).pause()


@tool(mutates=False)
def import_resume(job_id: str, access_token: str) -> dict:
    """Resume a paused import (or one waiting for a fresh sign-in); the partial file continues
    where it stopped."""
    return _import(job_id).start(access_token)


@tool(mutates=False)
def import_edit(job_id: str, add: typing.Optional[typing.List[dict]] = None,
                remove: typing.Optional[typing.List[str]] = None,
                access_token: typing.Optional[str] = None) -> dict:
    """Add items to an import or remove them, while it runs or is paused. Removing the file in
    flight aborts it; removing a folder removes its unfinished children; finished files stay
    (remove_media deletes them)."""
    return _import(job_id).edit(_items_arg(add), remove or [], access_token)


@tool(mutates=False)
def import_cancel(job_id: str) -> dict:
    """Stop an import: unfinished items are dropped and their partial downloads deleted;
    finished files stay in the project."""
    return _import(job_id).cancel()


@tool(mutates=False)
def remove_media(drive_ids: typing.List[str]) -> dict:
    """Remove imported files (by Drive id) from the project and delete them from the disk,
    with their proxies."""
    from .assembly import imports
    return imports.remove_media(drive_ids)


@tool(mutates=False)
def upload_to_drive(filepath: str, access_token: str, folder_id: typing.Optional[str] = None,
                    name: typing.Optional[str] = None) -> dict:
    """Upload a file (a render, an exported sequence, an editing script) to Google Drive, in
    the background. Poll with job_status; the result has the new file's id and folder.

    :arg access_token: A Google OAuth access token with drive.file. Used for this job only, not stored.
    :arg folder_id: Default: the app's own "latest avenue exports" folder in My Drive (made on
      first use). With drive.file the app can only write into folders it made.
    """
    path = _path(filepath)
    meta = os.path.join(os.path.dirname(path), ".drive")
    os.makedirs(meta, exist_ok=True)
    stem = os.path.basename(path)
    args = [_python(), "-m", "vse_standalone.assembly.drive", "upload", path] + ([folder_id] if folder_id else [])
    if name:
        args += ["--name", name]
    return _start_job("upload_to_drive", args, os.path.join(meta, stem + ".upload.log"),
                      os.path.join(meta, stem + ".upload.json"), _json_result,
                      env_extra={"GOOGLE_ACCESS_TOKEN": access_token},
                      stdout=os.path.join(meta, stem + ".upload.json"))


# -----------------------------------------------------------------------------
# Uploads, sync fixes, thumbnails (for the website).

UPLOAD_EXT = {".pdf", ".txt", ".fountain", ".fdx", ".lrc", ".srt", ".cube"} | probe.VIDEO_EXT | \
    probe.AUDIO_EXT | probe.IMAGE_EXT
UPLOAD_CHUNK = 700 * 1024           # decoded bytes per call (the gateway takes 1 MB requests)


@tool(mutates=False)
def save_upload(name: str, data_base64: str, folder: str = "uploads", append: bool = False) -> dict:
    """Save a file sent from the browser (a script PDF, a lyric sheet, a song) into the media
    folder. Large files go in several calls: the first without append, the rest with append.

    :arg name: File name, e.g. "script.pdf". Only the name is used, never a path.
    :arg data_base64: The bytes, base64-encoded (up to about 700 KB decoded per call).
    :arg folder: Folder inside the media folder (default "uploads").
    :arg append: Add to the file instead of starting it.
    """
    import base64
    import binascii
    safe = os.path.basename(name.replace("\\", "/")).strip()
    if not safe or safe.startswith("."):
        raise api.APIError("Give the file a name, e.g. script.pdf")
    if os.path.splitext(safe)[1].lower() not in UPLOAD_EXT:
        raise api.APIError("This kind of file can't be uploaded here: {:s}".format(safe))
    sub = os.path.normpath(folder or "uploads")
    if sub.startswith("..") or os.path.isabs(sub):
        raise api.APIError("The folder must be inside the media folder")
    try:
        data = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise api.APIError("The file data is not valid base64") from None
    if len(data) > UPLOAD_CHUNK:
        raise api.APIError("Send at most {:d} KB per call (use append for the rest)".format(UPLOAD_CHUNK // 1024))
    dest = os.path.join(api.media_root(), sub)
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, safe)
    if append and not os.path.exists(path):
        raise api.APIError("Nothing to append to: send the first part without append")
    with open(path, "ab" if append else "wb") as fh:
        fh.write(data)
    return {"filepath": path, "relative": os.path.join(sub, safe), "bytes": os.path.getsize(path)}


@tool(mutates=False)
def set_clip_sync(filepath: str, clip: str, audio_clip: typing.Optional[str] = None,
                  offset: typing.Optional[float] = None, nudge: typing.Optional[float] = None,
                  clear: bool = False) -> dict:
    """Fix one clip's sound sync in the editing script: pair it with a sound file, set or nudge
    the offset, or remove the pairing (the clip's own sound plays again).

    Offset: recorder time = clip time + offset (seconds). Playback, layout, render and exports
    use it at once; transcription and take matching use it on the next start_assembly.
    :arg clip: The camera clip's id (as in the editing script's media).
    :arg audio_clip: The sound file's id. Default: the one it is synced to now.
    :arg offset: Set the offset to this.
    :arg nudge: Move the current offset by this many seconds (e.g. 0.04 is one frame at 25 fps).
    :arg clear: Unsync the clip.
    """
    path, doc = _load(filepath)
    media = doc["media"]
    if clip not in media:
        raise api.APIError("No clip {!r} in the editing script".format(clip))
    m = media[clip]
    old = m.get("sound")
    if old and old["clip"] in media:
        media[old["clip"]]["synced_to"] = [c for c in media[old["clip"]].get("synced_to", []) if c != clip]
    if clear:
        m.pop("sound", None)
        editing_script.save(doc, path)
        return {"clip": clip, "sound": None}
    audio = audio_clip or (old or {}).get("clip")
    if not audio:
        raise api.APIError("Name the sound file to sync {:s} with (audio_clip)".format(clip))
    if audio not in media or media[audio]["kind"] != "audio":
        raise api.APIError("{!r} is not a sound file in the editing script".format(audio))
    value = offset if offset is not None else (old or {}).get("offset", 0.0)
    if nudge:
        value += nudge
    m["sound"] = {"clip": audio, "offset": round(float(value), 4), "method": "manual", "score": 1.0}
    media[audio].setdefault("synced_to", [])
    if clip not in media[audio]["synced_to"]:
        media[audio]["synced_to"].append(clip)
    if audio in doc.get("unplaced", []):
        doc["unplaced"].remove(audio)
    editing_script.save(doc, path)
    return {"clip": clip, "sound": m["sound"]}


@tool(mutates=False)
def take_thumbnails(filepath: str, line: str, width: int = 160, inline: bool = True) -> dict:
    """A thumbnail for each take of a line (the frame in the middle of the take), so the page
    can show the alternatives side by side.

    :arg line: Line id, e.g. "S1-3".
    :arg width: Thumbnail width in pixels (16-640).
    :arg inline: Also return each JPEG as a data: URL (the page can't read the editor's files).
    """
    import base64
    from .assembly import visual
    path, doc = _load(filepath)
    try:
        _scene, el = editing_script.find(doc, line)
    except KeyError as ex:
        raise api.APIError(str(ex).strip("'\"")) from None
    width = max(16, min(640, int(width)))
    folder = os.path.join(os.path.dirname(path), ".thumbs", os.path.basename(path))
    out = []
    for k, take in enumerate(el.get("takes") or []):
        pic = take
        if doc["media"][take["clip"]]["kind"] == "audio":
            pic = el.get("cover") or None
        item = {"take": k, "clip": take["clip"], "chosen": k == el.get("take"),
                "score": take.get("score"), "heard": take.get("heard")}
        if pic:
            m = doc["media"][pic["clip"]]
            t = None if m["kind"] == "image" else round((pic["in"] + pic["out"]) / 2, 3)
            thumb = os.path.join(folder, "{:s}_{:d}_{:d}.jpg".format(line, k, width))
            try:
                visual.thumbnail(m["path"], t, thumb, width=width)
                item.update(thumbnail=thumb, time=t, picture_clip=pic["clip"])
                if inline:
                    with open(thumb, "rb") as fh:
                        item["data_url"] = "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()
            except Exception as ex:  # a broken clip shouldn't hide the other takes
                item["error"] = str(ex)[:200]
        out.append(item)
    return {"line": line, "takes": out}
