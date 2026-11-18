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


def main():
    names = {t["name"] for t in api.describe()}
    wanted = {"probe_media", "read_screenplay", "assembly_status", "start_assembly", "assembly_job",
              "get_editing_script", "edit_editing_script", "choose_take", "set_line_cut",
              "lay_out_editing_script", "render_editing_script"}
    check("assembly tools are registered", wanted <= names, wanted - names)

    s = api.call("read_screenplay", {"filepath": os.path.join(samples, "script.pdf")})
    check("read_screenplay in the editor's Python", s["dialogue_lines"] == 6, s["dialogue_lines"])
    p = api.call("probe_media", {"filepath": os.path.join(samples, "media")})
    check("probe_media on a folder", len(p["files"]) == 8, len(p["files"]))

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
