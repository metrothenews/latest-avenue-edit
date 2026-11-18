# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The assembly tools inside the editor: read the screenplay, lay an editing script out on the
timeline, swap a take and lay it out again, render a frame.

Make the editing script first (tests/vse/test_assembly.py --full --keep DIR), then:

    blender -b --factory-startup --python tests/vse/test_assembly_editor.py -- DIR
"""

import os
import sys
import tempfile
import traceback

import bpy

from vse_standalone import api, server  # noqa: F401
from vse_standalone import assembly_tools  # noqa: F401 (registers the tools)

samples = sys.argv[sys.argv.index("--") + 1]
doc_path = os.path.join(samples, "cut.edit.json")
failures = []


def check(name, ok, detail=""):
    print("{:s}  {:s}{:s}".format("ok  " if ok else "FAIL", name,
                                 ("  " + str(detail)) if detail and not ok else ""))
    if not ok:
        failures.append(name)


def test_site_tools(lines):
    import base64
    import json
    import shutil
    import time
    # save_upload: a script PDF in two parts.
    data_dir = tempfile.mkdtemp()
    os.makedirs(os.path.join(data_dir, "media"))
    os.environ["VSE_DATA"] = data_dir
    pdf = open(os.path.join(samples, "script.pdf"), "rb").read()
    half = len(pdf) // 2
    api.call("save_upload", {"name": "../../script.pdf", "data_base64": base64.b64encode(pdf[:half]).decode()})
    r = api.call("save_upload", {"name": "script.pdf", "append": True,
                                 "data_base64": base64.b64encode(pdf[half:]).decode()})
    check("save_upload: two parts make the file, inside the media folder",
          r["filepath"] == os.path.join(data_dir, "media", "uploads", "script.pdf") and
          open(r["filepath"], "rb").read() == pdf, r)
    s = api.call("read_screenplay", {"filepath": "uploads/script.pdf"})
    check("save_upload: the uploaded script reads", s["dialogue_lines"] == 6)
    try:
        api.call("save_upload", {"name": "x.exe", "data_base64": "AAAA"})
        check("save_upload: refuses other kinds of file", False)
    except api.APIError:
        check("save_upload: refuses other kinds of file", True)

    # set_clip_sync: nudge, then clear, and the layout follows.
    work = os.path.join(data_dir, "cut.edit.json")
    shutil.copy(doc_path, work)
    doc = json.load(open(work))
    clip = next(c for c, m in doc["media"].items() if m.get("sound"))
    before = doc["media"][clip]["sound"]["offset"]
    r = api.call("set_clip_sync", {"filepath": work, "clip": clip, "nudge": 0.5})
    check("set_clip_sync: nudge", abs(r["sound"]["offset"] - before - 0.5) < 1e-6 and r["sound"]["method"] == "manual", r)
    api.call("lay_out_editing_script", {"filepath": work})
    sc = api._scene()
    fps = sc.render.fps / sc.render.fps_base
    mine = [st for st in sc.sequence_editor.strips_all if st.get("editing_script") == work]
    snd = next(st for st in mine if st.type == "SOUND" and clip in st.name)
    pic = next(st for st in mine if st.type == "MOVIE" and clip in st.name)
    shift = ((pic.left_handle - pic.content_start) - (snd.left_handle - snd.content_start)) / fps
    check("set_clip_sync: the laid-out sound moves with it", abs(-shift - (before + 0.5)) <= 1.5 / fps,
          (shift, before + 0.5))
    api.call("set_clip_sync", {"filepath": work, "clip": clip, "clear": True})
    check("set_clip_sync: clear", "sound" not in json.load(open(work))["media"][clip])

    # take_thumbnails: one picture per take.
    line = next(l for l in lines if len(l["takes"]) > 1)
    r = api.call("take_thumbnails", {"filepath": doc_path, "line": line["id"], "width": 96})
    check("take_thumbnails: a JPEG per take", len(r["takes"]) == len(line["takes"]) and all(
        t.get("data_url", "").startswith("data:image/jpeg;base64,") and os.path.exists(t["thumbnail"])
        for t in r["takes"]), [t.get("error") for t in r["takes"]])

    # start_assembly with a song and lyrics (music video), through the job.
    music = os.path.join(samples, "music")
    if os.environ.get("VSE_ASSEMBLY_PYTHON") and os.path.exists(os.path.join(music, "song.wav")):
        j = api.call("start_assembly", {"media_folder": music, "output": os.path.join(data_dir, "mv.edit.json"),
                                        "song": os.path.join(music, "song.wav"),
                                        "lyrics": os.path.join(samples, "lyrics.txt")})
        while True:
            r = api.call("job_status", {"job": j["job"]})
            if r["state"] != "running":
                break
            time.sleep(1)
        check("start_assembly with song and lyrics", r["state"] == "done" and r["result"]["events"] >= 4,
              r["log"][-5:])
        if r["state"] == "done":
            lay = api.call("lay_out_editing_script", {"filepath": os.path.join(data_dir, "mv.edit.json")})
            check("music edit lays out", lay["events"] == r["result"]["events"])


def test_import_tools():
    """The Drive import queue through the editor's tools, against a stand-in Drive."""
    import shutil
    import time
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import test_imports as ti
    work = tempfile.mkdtemp()
    data = os.path.join(work, "data")
    os.makedirs(os.path.join(data, "media"))
    os.environ["VSE_DATA"] = data
    files = ti.build_drive(work)
    drive = ti.FakeDrive(files, valid=(ti.GOOD,), throttle=0.004)
    os.environ["VSE_DRIVE_API"] = drive.url
    from vse_standalone.assembly import imports
    imports.CHUNK = 8192
    imports._jobs.clear()

    def poll(jid, cond, limit=60):
        t0 = time.time()
        while time.time() - t0 < limit:
            st = api.call("import_status", {"job_id": jid})
            if cond(st):
                return st
            time.sleep(0.05)
        return api.call("import_status", {"job_id": jid})

    try:
        r = api.call("import_start", {"access_token": ti.GOOD, "items": [
            {"drive_id": "root", "kind": "folder", "name": "Shoot", "recursive": True},
            {"drive_id": "big1", "kind": "file", "name": "big1.mov"}]})
        jid = r["job_id"]
        st = poll(jid, lambda s: any(i["drive_id"] == "big1" and i["bytes_done"] > 50000 for i in s["items"]))
        st = api.call("import_pause", {"job_id": jid})
        st = poll(jid, lambda s: next(i for i in s["items"] if i["drive_id"] == "big1")["state"] == "paused")
        check("import tools: pause mid-file", st["state"] == "paused", st["state"])
        st = api.call("import_edit", {"job_id": jid, "add": [{"drive_id": "small", "kind": "file", "name": "small.mov"}]})
        check("import tools: add while paused", any(i["drive_id"] == "small" and i["state"] == "queued" for i in st["items"]))
        api.call("import_resume", {"job_id": jid, "access_token": ti.GOOD})
        st = poll(jid, lambda s: s["state"] in ("done", "failed"))
        check("import tools: resume to done, totals add up", st["state"] == "done"
              and st["totals"]["done"] == st["totals"]["files"] == 7, (st["state"], st["totals"]))
        lm = api.call("list_media", {})
        ids = {m["drive_id"] for m in lm["imported"]}
        check("list_media: imported files with drive_id, size, duration, kind",
              {"c1", "c2", "b1", "big1", "small", "pdf", "doc"} <= ids and all(
                  {"drive_id", "name", "size", "duration", "kind"} <= set(m) for m in lm["imported"]), ids)
        r = api.call("remove_media", {"drive_ids": ["small"]})
        check("remove_media", r["removed"] == ["small"] and not any(
            m["drive_id"] == "small" for m in api.call("list_media", {})["imported"]), r)
        old = api.call("fetch_drive_folder", {"folder_id": "https://drive.google.com/drive/folders/sub?usp=x",
                                              "access_token": ti.GOOD})
        t0 = time.time()
        while time.time() - t0 < 60:
            js = api.call("job_status", {"job": old["job"]})
            if js["state"] != "running":
                break
            time.sleep(0.1)
        check("fetch_drive_folder (old tool) runs on the queue; job_status reports it",
              js["state"] == "done" and js["result"]["files"] and js["result"]["files"][0]["skipped"], js)
        try:
            api.call("import_status", {"job_id": "nope"})
            check("import_status: unknown job is a readable error", False)
        except api.APIError:
            check("import_status: unknown job is a readable error", True)
    finally:
        drive.close()
        shutil.rmtree(work, ignore_errors=True)


def main():
    names = {t["name"] for t in api.describe()}
    wanted = {"probe_media", "read_screenplay", "assembly_status", "start_assembly", "assembly_job",
              "get_editing_script", "edit_editing_script", "choose_take", "set_line_cut",
              "lay_out_editing_script", "render_editing_script", "job_status", "export_sequence",
              "fetch_drive_folder", "upload_to_drive", "save_upload", "set_clip_sync",
              "take_thumbnails", "import_start", "import_status", "import_pause", "import_resume",
              "import_edit", "import_cancel", "remove_media", "list_media"}
    check("assembly tools are registered", wanted <= names, wanted - names)

    s = api.call("read_screenplay", {"filepath": os.path.join(samples, "script.pdf")})
    check("read_screenplay in the editor's Python", s["dialogue_lines"] == 6, s["dialogue_lines"])
    p = api.call("probe_media", {"filepath": os.path.join(samples, "media")})
    check("probe_media on a folder", len(p["files"]) == len([f for f in os.listdir(os.path.join(samples, "media")) if not f.startswith(".")]), len(p["files"]))

    lines = api.call("get_editing_script", {"filepath": doc_path, "format": "lines"})["lines"]
    with_cut = [l for l in lines if l["cut"]]
    result = api.call("lay_out_editing_script", {"filepath": doc_path})
    scene = api._scene()
    fps = scene.render.fps / scene.render.fps_base
    check("project frame rate from the footage", abs(fps - 24) < 0.01, fps)
    check("one event per line with footage", result["events"] == len(with_cut),
          (result["events"], len(with_cut)))
    strips = [s for s in scene.sequence_editor.strips_all if s.get("editing_script")]
    pics = sorted((s for s in strips if s.type in ("MOVIE", "IMAGE")), key=lambda s: s.left_handle)
    gaps = [b.left_handle - a.right_handle for a, b in zip(pics, pics[1:])]
    check("pictures butt together with no gaps or overlaps", all(g == 0 for g in gaps), gaps)
    for line in with_cut:
        strip = next((s for s in pics if s.get("editing_script_line") == line["id"]), None)
        if strip is None:
            check("line {:s} has a picture strip".format(line["id"]), False)
            continue
        cut = line["cut"]
        want = int(round((cut["out"] - cut["in"]) * fps))
        got = strip.right_handle - strip.left_handle
        offset = (strip.left_handle - strip.content_start) / fps
        src = cut if strip.type != "MOVIE" or not line.get("cover") else line["cover"]
        check("line {:s}: length and source in point".format(line["id"]),
              abs(got - want) <= 1 and (strip.type == "IMAGE" or abs(offset - src["in"]) <= 1 / fps + 1e-6),
              (got, want, offset, src["in"]))
    sounds = [s for s in strips if s.type == "SOUND"]
    check("dialogue has sound strips", len(sounds) >= len([l for l in with_cut if l["type"] == "dialogue"]),
          len(sounds))
    markers = [m.name for m in scene.timeline_markers if m.name.startswith("§ ")]
    check("a marker per scene", len(markers) == 2, markers)

    # Swap a take and lay out again: the old strips are replaced.
    line = next(l for l in lines if len(l["takes"]) > 1)
    api.call("choose_take", {"filepath": doc_path, "line": line["id"], "take": 1})
    api.call("lay_out_editing_script", {"filepath": doc_path})
    strips = [s for s in scene.sequence_editor.strips_all if s.get("editing_script")]
    pics = [s for s in strips if s.type in ("MOVIE", "IMAGE")]
    check("laying out again replaces the old strips", len(pics) == len(with_cut), len(pics))
    swapped = next(s for s in pics if s.get("editing_script_line") == line["id"])
    check("swapped take is on the timeline", line["takes"][1]["clip"] in swapped.name, swapped.name)
    api.call("choose_take", {"filepath": doc_path, "line": line["id"], "take": 0})

    if os.environ.get("VSE_ASSEMBLY_PYTHON"):
        seq = os.path.join(tempfile.mkdtemp(), "cut.fcpxml")
        r = api.call("export_sequence", {"filepath": doc_path, "output": seq})
        check("export_sequence from the editor", r["events"] == len(with_cut) and os.path.exists(seq), r)

    test_site_tools(lines)
    test_import_tools()

    out = os.path.join(tempfile.mkdtemp(), "frame.png")
    r = api.call("render_frame", {"frame": 30, "filepath": out})
    check("a frame of the laid-out cut renders", r["exists"])


try:
    main()
except Exception:
    traceback.print_exc()
    failures.append("exception")
print("{:d} failed".format(len(failures)) if failures else "all passed")
sys.exit(1 if failures else 0)
