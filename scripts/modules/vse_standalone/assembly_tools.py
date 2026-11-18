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
                   visual_grouping: bool = True, model_order: bool = True) -> dict:
    """Start building an editing script (assembly cut) from footage and an optional screenplay.

    Runs in the background: transcribes every take, matches takes to script lines, groups
    clips by look. Poll with assembly_job. Without a script a rough one is made from speech.
    :arg media_folder: Folder of footage, photos and audio.
    :arg output: Where to write the editing script (.edit.json).
    :arg script: Screenplay .pdf/.fountain/.txt.
    :arg model: faster-whisper model (default base.en).
    :arg model_order: Without a script, ask the language model once for the scene order.
    """
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
    return _start_job("assembly", args, out + ".log", out, _assembly_result)


def _assembly_result(entry):
    return editing_script.summary(editing_script.load(entry["output"]))


def _start_job(kind, args, log, output, on_done=None):
    """Run a command of the assembly's own Python in the background. -> job descriptor."""
    job = uuid.uuid4().hex[:8]
    env = dict(os.environ, PYTHONPATH=MODULES_DIR + os.pathsep + os.environ.get("PYTHONPATH", ""),
               PYTHONUNBUFFERED="1")
    fh = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=fh, env=env)
    _jobs[job] = {"kind": kind, "proc": proc, "log": log, "output": output,
                  "started": time.time(), "fh": fh, "on_done": on_done, "result": None}
    return {"job": job, "kind": kind, "output": output, "log": log}


@tool(mutates=False)
def job_status(job: str) -> dict:
    """Progress of a background job (start_assembly, fetch_drive_folder): running/done/failed,
    the last log lines, and its result when done."""
    entry = _jobs.get(job)
    if entry is None:
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
        label = "{:s} {:s}".format(ev["element"], ev["clip"])[:60]
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
