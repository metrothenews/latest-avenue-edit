#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Assembly cut, end to end, against a generated test shoot (tests/vse/assembly/make_samples.py).

    python3 tests/vse/test_assembly.py                 # screenplay rules only (no extra packages)
    assembly-env/bin/python tests/vse/test_assembly.py --full [--model tiny.en] [--keep DIR]

``--full`` needs the assembly environment (tools/vse_assembly/requirements.txt), ffmpeg,
espeak-ng and poppler: it transcribes the takes, matches them to the script, groups them by
look, edits the editing script as text, and renders it with ffmpeg.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts", "modules"))
sys.path.insert(0, os.path.join(HERE, "assembly"))

from vse_standalone.assembly import editing_script, screenplay  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print("{:s}  {:s}{:s}".format("ok  " if ok else "FAIL", name, ("  " + str(detail)) if detail and not ok else ""))
    if not ok:
        failures.append(name)


FOUNTAIN = """\
Title: Flat Test

INT. KITCHEN - DAY

Steam rises from a pot.

ANA
(whispering)
Did you hear that?

BEN (O.S.)
It's just the wind.

CUT TO:

EXT. GARDEN - NIGHT 12

Nothing moves.
"""


def test_rules(samples):
    s = screenplay.parse_text(FOUNTAIN)
    els = [e for sc in s["scenes"] for e in sc["elements"]]
    check("flat text: two scenes", [sc["heading"] for sc in s["scenes"]] ==
          ["INT. KITCHEN - DAY", "EXT. GARDEN - NIGHT"], [sc["heading"] for sc in s["scenes"]])
    check("flat text: element types", [e["type"] for e in els] ==
          ["action", "dialogue", "dialogue", "transition", "action"], [e["type"] for e in els])
    check("flat text: parenthetical", els[1]["parentheticals"][0]["text"] == "(whispering)")
    check("flat text: extension", els[2]["extensions"] == ["O.S."] and els[2]["character"] == "BEN")
    check("flat text: scene heading parts", (s["scenes"][0]["int_ext"], s["scenes"][0]["location"],
                                             s["scenes"][0]["time"]) == ("INT", "KITCHEN", "DAY"))

    numbered = ("Ad Script 1:\n\n1. if you like late night drives\n\n2. on [Radio Logo]\n\n"
                "3. this one is for you\n4. I make music in my bedroom\n\n5. press play\n\n"
                "6. [End Card]\nout now\non [Radio Logo]\n\nAd Script 2:\n\n1. you drive at night\n\n"
                "2. on [Radio Logo]\n\n3. this is for you\n\n4. press play\n")
    s = screenplay.parse_text(numbered)
    els = s["scenes"][0]["elements"]
    check("numbered script: recognised, one scene per script", s["format"] == "numbered"
          and [sc["heading"] for sc in s["scenes"]] == ["Ad Script 1", "Ad Script 2"], s["format"])
    check("numbered script: spoken lines and the end card", [e["type"] for e in els] ==
          ["dialogue"] * 5 + ["action"] and els[5]["text"].endswith("on [Radio Logo]"), [e["type"] for e in els])
    check("numbered script: brackets reduced to what is said", els[1]["spoken"] == "on Radio", els[1])

    names = {"A001_C001_S1_T1.mov": (1, 1), "SC12A_TK3.mp4": ("12A", 3), "Scene 4 Take 2.mov": (4, 2),
             "12A-3.mov": ("12A", 3), "sc04-tk02.mxf": (4, 2), "IMG_0101.MOV": None, "c001.mp4": None}
    got = {n: (lambda r: r and (r["scene"], r["take"]))(editing_script.name_slate(n)) for n in names}
    check("scene and take from file names", got == names, got)

    pdf = os.path.join(samples, "script.pdf")
    s = screenplay.parse(pdf)
    lines = screenplay.dialogue_lines(s)
    import make_samples
    want = list(make_samples.LINES.values())
    check("pdf: title", s["title"] == "THE LAST BUS", s["title"])
    check("pdf: scenes", [sc["heading"] for sc in s["scenes"]] ==
          ["INT. BUS STATION - NIGHT", "EXT. DINER - NIGHT"], [sc["heading"] for sc in s["scenes"]])
    check("pdf: dialogue text", [l["text"] for l in lines] == want, [l["text"] for l in lines])
    check("pdf: characters", [l["character"] for l in lines] ==
          ["DANIEL", "MAYA", "DANIEL", "MAYA", "DANIEL", "MAYA"])
    check("pdf: V.O. extension", lines[-1]["extensions"] == ["V.O."])
    check("pdf: parenthetical", lines[0]["parentheticals"][0]["text"] == "(out of breath)")
    types = [e["type"] for e in s["scenes"][0]["elements"]]
    check("pdf: scene 1 layout", types == ["action", "dialogue", "dialogue", "dialogue", "action",
                                           "dialogue", "transition"], types)
    return lines


def test_full(samples, model, lines):
    from vse_standalone.assembly import pipeline, render
    with open(os.path.join(samples, "truth.json")) as fh:
        truth = json.load(fh)
    line_ids = {l["text"]: l["id"] for l in lines}
    truth_line = {k: line_ids[v] for k, v in truth["lines"].items()}   # L1 -> S1-2

    doc = pipeline.assemble(os.path.join(samples, "media"), os.path.join(samples, "script.pdf"),
                            model=model, ask_model=False, log=lambda t: print("   ", t))
    path = os.path.join(samples, "cut.edit.json")
    editing_script.save(doc, path)
    summary = editing_script.summary(doc)
    check("every line has footage", not summary["lines_without_footage"], summary)

    # Every candidate take really says that line, at about the right time.
    wrong = []
    for scene in doc["scenes"]:
        for el in scene["elements"]:
            for t in el.get("takes", []) if el["type"] == "dialogue" else []:
                name = doc["media"][t["clip"]]["name"]
                spans = [x for x in truth["takes"][name] if truth_line[x["line"]] == el["id"]]
                if not spans or not any(t["in"] <= x["start"] + 0.6 and t["out"] >= x["end"] - 0.6
                                        for x in spans):
                    wrong.append((el["id"], name, t["in"], t["out"], spans))
    check("every candidate take is the right line at the right time", not wrong, wrong)
    expected = {truth_line[x["line"]] for name, spans in truth["takes"].items() for x in spans}
    for el_id in sorted(expected):
        _s, el = editing_script.find(doc, el_id)
        n_truth = sum(1 for spans in truth["takes"].values() for x in spans if truth_line[x["line"]] == el_id)
        check("line {:s}: all {:d} takes found".format(el_id, n_truth), len(el["takes"]) == n_truth,
              [doc["media"][t["clip"]]["name"] for t in el["takes"]])

    _s, flub = editing_script.find(doc, truth_line["L2"])
    flubbed = [t for t in flub["takes"] if "T2" in t["clip"]]
    check("the flubbed take of L2 ranks last", flubbed and flub["takes"][-1] is flubbed[0])
    _s, l5 = editing_script.find(doc, truth_line["L5"])
    check("the cut-short take of L5 ranks second", "S2_T2" in l5["takes"][-1]["clip"])

    groups = [sorted(doc["media"][c]["name"] for c in g["clips"]) for g in doc["groups"]]
    scene1 = sorted(n for n in truth["takes"] if "_S1_" in n)
    check("scene 1 takes look alike", any(set(scene1) <= set(g) for g in groups), groups)
    check("the B-roll goes with scene 2", any("A003_C003_BROLL.mov" in g and "A003_C001_S2_T1.mov" in g
                                              for g in groups), groups)
    check("scenes are told apart", not any("A001_C001_S1_T1.mov" in g and "A003_C001_S2_T1.mov" in g
                                           for g in groups), groups)
    slates = {m["name"]: m["slate"] for m in doc["media"].values() if m["slate"]}
    check("slates heard", slates.get("A001_C002_S1_T2.mov", {}).get("take") == 2, slates)
    _s, vo = editing_script.find(doc, truth_line["L6"])
    check("voice-over plays over cover picture", vo["cut"] and vo["cover"] and
          doc["media"][vo["cover"]["clip"]]["kind"] == "video", vo.get("cover"))
    check("supervisor notes slot reserved", doc["supervisor_notes"]["status"] == "reserved" and
          all("supervisor" in m for m in doc["media"].values()))

    # Separate sound.
    for name, want in truth.get("sync", {}).items():
        cid = next(c for c, m in doc["media"].items() if m["name"] == name)
        got = doc["media"][cid].get("sound")
        rec = got and doc["media"][got["clip"]]["name"]
        check("{:s} synced to {:s} by {:s}".format(name, want["audio"], want["method"]),
              got and rec == want["audio"] and abs(got["offset"] - want["offset"]) < 0.01
              and got["method"] == want["method"], got)
    recorders = {s["audio"] for s in truth.get("sync", {}).values()}
    check("recorder files are not takes of their own", not any(
        doc["media"][t["clip"]]["name"] in recorders for sc in doc["scenes"] for el in sc["elements"]
        for t in el.get("takes", []) if el["type"] == "dialogue"))
    check("the voice-over is not synced to a camera clip", not any(
        m.get("sound") and doc["media"][m["sound"]["clip"]]["name"].startswith("VO_")
        for m in doc["media"].values()))
    tl = editing_script.timeline(doc)
    synced_events = [e for e in tl if doc["media"][e["clip"]].get("sound")]
    check("synced lines play the recorder's sound", synced_events and all(
        os.path.basename(e["sound_path"]) in recorders and e["sound_in"] > e["in"] for e in synced_events),
        [(e["element"], e["sound_path"]) for e in synced_events])

    # Swap a take.
    other = 1
    editing_script.choose_take(doc, flub["id"], other)
    check("choose_take moves the cut", flub["cut"]["clip"] == flub["takes"][other]["clip"])
    editing_script.choose_take(doc, flub["id"], 0)

    # Edit as text: move L3 before L2, shorten L4, delete L5, add a line said only in a take.
    text = editing_script.to_text(doc)
    l2 = "                      MAYA\n          It left ten minutes ago. You missed\n          it.\n\n"
    l3 = "                      DANIEL\n          Then I guess we are both stuck here\n          tonight.\n\n"
    check("text has the lines in screenplay layout", l2 in text and l3 in text, text)
    edited = text.replace(l2 + l3, l3 + l2)
    edited = edited.replace("          There is a diner across the street.\n          They make terrible coffee, but the\n          pie is good.",
                            "          There is a diner across the street.")
    edited = edited.replace("                      DANIEL\n          Terrible coffee sounds perfect\n          right now.\n\n", "")
    edited = edited.replace("FADE OUT.", "FADE OUT.\n\n                      MAYA\n          It left twenty minutes ago.")
    _s, l4_before = editing_script.find(doc, truth_line["L4"])
    clip_before = l4_before["cut"]["clip"]
    new, report = editing_script.apply_text(doc, edited)
    order = [el["id"] for el in new["scenes"][0]["elements"] if el["type"] == "dialogue"]
    check("moved line keeps its footage", order[1] == truth_line["L3"] and order[2] == truth_line["L2"]
          and editing_script.find(new, truth_line["L3"])[1]["cut"] == editing_script.find(doc, truth_line["L3"])[1]["cut"],
          (order, report))
    _s, l4 = editing_script.find(new, truth_line["L4"])
    check("shortened line is re-trimmed", l4["cut"]["clip"] == clip_before and
          l4["cut"]["out"] - l4["cut"]["in"] < l4_before["cut"]["out"] - l4_before["cut"]["in"] - 1.5
          and "pie" not in l4["takes"][0]["heard"], (l4["cut"], l4["takes"][0]["heard"]))
    check("deleted line is gone", truth_line["L5"] in report["removed"], report)
    found = [editing_script.find(new, i)[1] for i in report["found"]]
    check("a new line is found in the takes", found and "S1_T2" in found[0]["cut"]["clip"],
          [(f["text"], f.get("cut")) for f in found])
    round_trip, rep2 = editing_script.apply_text(new, editing_script.to_text(new))
    check("text round trip changes nothing", editing_script.timeline(round_trip) == editing_script.timeline(new)
          and not rep2["new"] and not rep2["removed"], rep2)

    test_no_script(samples, model)
    test_music(samples)

    try:
        import opentimelineio as otio
        from vse_standalone.assembly import export
    except ImportError:
        otio = None
        check("opentimelineio installed (sequence export)", False)
    if otio:
        n_events = len(editing_script.timeline(doc))
        for fmt, ext in (("otio", ".otio"), ("fcpxml", ".fcpxml"), ("xml", ".xml"), ("edl", ".edl")):
            path = os.path.join(samples, "export", "cut" + ext)
            r = export.export(doc, path)
            back = otio.adapters.read_from_file(path)
            vtrack = next(t for t in back.tracks if t.kind == otio.schema.TrackKind.Video)
            clips = [c for c in vtrack if isinstance(c, otio.schema.Clip)]
            check("export {:s}: reads back with every event at the right length".format(fmt),
                  len(clips) == n_events and abs(back.duration().to_seconds() - r["duration"]) < 0.1,
                  (len(clips), n_events, back.duration().to_seconds(), r["duration"]))
        tl = export.timeline(doc)
        events = editing_script.timeline(doc)
        vclips = [c for c in tl.tracks[0] if isinstance(c, otio.schema.Clip)]
        k = next(i for i, ev in enumerate(events)          # the first event from a timecoded clip
                 if ev["picture"] and doc["media"][ev["picture"]["clip"]].get("timecode_seconds") is not None)
        first, ev = vclips[k], events[k]
        m = doc["media"][ev["picture"]["clip"]]
        check("export: source times are in the clip's own timecode",
              abs(first.source_range.start_time.to_seconds() - (m["timecode_seconds"] + ev["picture"]["in"])) < 0.05,
              (first.name, first.source_range.start_time.to_seconds(), m["timecode_seconds"], ev["picture"]["in"]))
        check_sequence_files(os.path.join(samples, "export"), doc.get("fps") or 24, "dialogue")
        exp = os.path.join(samples, "export")
        edl_first = next(l for l in open(os.path.join(exp, "cut.edl")) if l[:3].isdigit()).split()
        import re as _re
        seq = _re.search(r"<sequence[^>]*>", open(os.path.join(exp, "cut.fcpxml")).read()).group(0)
        import xml.etree.ElementTree as _ET
        _seq = _ET.parse(os.path.join(exp, "cut.xml")).getroot().find(".//sequence")
        xml_tc = _seq.findtext("timecode/string")          # the sequence's own, not a clip's
        otio_start = otio.adapters.read_from_file(os.path.join(exp, "cut.otio")).global_start_time
        check("exports start at 01:00:00:00 (EDL, FCPXML, FCP 7 XML, OTIO)",
              edl_first[-2] == "01:00:00:00" and 'tcStart="3600/1s"' in seq
              and xml_tc == "01:00:00:00" and abs(otio_start.to_seconds() - 3600) < 1e-6,
              (edl_first[-2:], seq, xml_tc, otio_start))

    out = os.path.join(samples, "cut.mp4")
    result = render.render(doc, out)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                            "csv=p=0", out], capture_output=True, text=True).stdout.strip()
    check("ffmpeg render has the cut's length", probe and abs(float(probe) - result["duration"]) < 0.5,
          (probe, result))


def _drums(path, bpm, seconds=20.0, start=0.37, sr=22050):
    """Kick on every beat (accented downbeat), off-beat hi-hats, a quiet pad. -> beat times."""
    import numpy
    import wave
    t = numpy.arange(int(seconds * sr)) / sr
    x = 0.05 * numpy.sin(2 * numpy.pi * 220 * t)
    rng = numpy.random.default_rng(1)
    beat, times, k = 60.0 / bpm, [], 0
    while start + k * beat < seconds - 0.5:
        b = start + k * beat
        times.append(b)
        i, n = int(b * sr), int(0.15 * sr)
        x[i:i + n] += (1.0 if k % 4 == 0 else 0.6) * numpy.exp(-numpy.arange(n) / sr * 30) * \
            numpy.sin(2 * numpy.pi * 60 * numpy.arange(n) / sr)
        j, m = int((b + beat / 2) * sr), int(0.03 * sr)
        if j + m < len(x):
            x[j:j + m] += 0.2 * rng.standard_normal(m) * numpy.exp(-numpy.arange(m) / sr * 150)
        k += 1
    x = x / numpy.abs(x).max() * 0.8
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return times


def check_sequence_files(folder, fps, what, stem="cut"):
    """The exported sequences follow their formats' rules (and Apple's DTD, when it can be
    fetched), and the checkers do catch broken files."""
    import re as _re
    from vse_standalone.assembly import check_exports as ce
    dtd = ce.fetch_fcpxml_dtd("1.8", os.path.join(os.path.expanduser("~"), ".cache", "fcpxml-dtd"))
    if not dtd:
        print("note: Apple's FCPXML DTD could not be fetched (offline?); checking the rules only")
    fcpxml, xml, edl = (os.path.join(folder, stem + e) for e in (".fcpxml", ".xml", ".edl"))
    p = ce.check_fcpxml(fcpxml, dtd)
    check("{:s} FCPXML is valid{:s}".format(what, " against Apple's DTD" if dtd else ""), not p, p[:5])
    p = ce.check_fcp7_xml(xml)
    check("{:s} FCP 7 XML (Premiere) has a valid structure and timing".format(what), not p, p[:5])
    p = ce.check_edl(edl, fps)
    check("{:s} EDL follows CMX 3600".format(what), not p, p[:5])
    if what != "dialogue":
        return
    # The checkers must fail on broken files.
    broken = os.path.join(folder, "broken")
    os.makedirs(broken, exist_ok=True)
    text = open(edl).read()
    lines = text.splitlines()
    k = next(i for i, l in enumerate(lines) if _re.match(r"^\d{3}\s", l))
    parts = lines[k].split()
    parts[-3] = "00:00:00:30"                     # bad timecode
    lines[k] = "  ".join(parts)
    open(os.path.join(broken, "b.edl"), "w").write("\n".join(lines) + "\n")
    x = open(xml).read()
    x = _re.sub(r"<end>(\d+)</end>", lambda m: "<end>{:d}</end>".format(int(m.group(1)) + 5), x, count=1)
    open(os.path.join(broken, "b.xml"), "w").write(x)
    f = open(fcpxml).read().replace('ref="r2"', 'ref="r999"', 1)
    open(os.path.join(broken, "b.fcpxml"), "w").write(f)
    check("checkers catch a broken EDL, FCP 7 XML and FCPXML",
          ce.check_edl(os.path.join(broken, "b.edl"), fps) and ce.check_fcp7_xml(os.path.join(broken, "b.xml"))
          and ce.check_fcpxml(os.path.join(broken, "b.fcpxml"), dtd))


def test_music(samples):
    from vse_standalone.assembly import music, pipeline, render
    folder = os.path.join(samples, "music_beats")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    for bpm in (92.0, 123.0, 140.0):
        truth = _drums(os.path.join(folder, "song.wav"), bpm)
        r = music.analyse(os.path.join(folder, "song.wav"))
        errors = [min(abs(b - t) for b in r["beats"]) for t in truth[2:-2]]
        check("music: {:.0f} BPM found, beats within 50 ms, bars on the downbeat".format(bpm),
              abs(r["tempo"] - bpm) < 1.5 and max(errors) < 0.05
              and min(abs(r["bars"][0] - t) for t in truth[::4]) < 0.05,
              (r["tempo"], max(errors)))
    with open(os.path.join(samples, "truth.json")) as fh:
        song_truth = json.load(fh).get("music")
    if song_truth:      # the generated music shoot: drums, sung lyric lines, five looks
        folder = os.path.join(samples, "music")
        song = os.path.join(folder, "song.wav")
        doc = pipeline.assemble_music(folder, song, os.path.join(samples, "lyrics.txt"),
                                      log=lambda t: None)
        starts = [el["cut"]["in"] for el in doc["scenes"][0]["elements"]]
        hit = [any(0 <= line["start"] - st <= 0.6 for st in starts) for line in song_truth["lines"]]
        check("music: tempo of the generated song", abs(doc["music"]["tempo"] - song_truth["tempo"]) < 1.5,
              doc["music"]["tempo"])
        check("music: each sung line starts a shot on the beat before it", all(hit),
              (hit, starts, [l["start"] for l in song_truth["lines"]]))
    else:
        for name in ("A001_C001_S1_T1.mov", "A003_C003_BROLL.mov", "STILL_diner_sign.jpg"):
            shutil.copy(os.path.join(samples, "media", name), folder)
        doc = pipeline.assemble_music(folder, os.path.join(folder, "song.wav"), log=lambda t: None)
    els = doc["scenes"][0]["elements"]
    grid = set(round(b, 3) for b in doc["music"]["beats"]) | {0.0}
    check("music: every cut on a beat", all(round(el["cut"]["in"], 3) in grid for el in els),
          [el["cut"]["in"] for el in els])
    check("music: the song plays through without a gap", all(
        abs(a["cut"]["out"] - b["cut"]["in"]) < 1e-6 for a, b in zip(els, els[1:])))
    check("music: neighbouring shots differ", all(
        a["cover"]["clip"] != b["cover"]["clip"] for a, b in zip(els, els[1:])))
    editing_script.choose_take(doc, els[1]["id"], 1)
    check("music: swapping a shot keeps the song", els[1]["cut"]["clip"] == doc["music"]["song"]
          and els[1]["cover"]["clip"] == els[1]["takes"][1]["clip"])
    out = os.path.join(samples, "mv.mp4")      # outside the footage folder
    r = render.render(doc, out)
    got = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          out], capture_output=True, text=True).stdout.strip()
    want = 16.0 if song_truth else 20.0
    check("music: render is the song's length", got and abs(float(got) - want) < 0.3, got)
    try:
        from vse_standalone.assembly import export
        for ext in (".fcpxml", ".xml", ".edl"):
            export.export(doc, os.path.join(samples, "export_music", "mv" + ext))
        check_sequence_files(os.path.join(samples, "export_music"), doc.get("fps") or 24, "music", "mv")
    except ImportError:
        pass


def test_no_script(samples, model):
    """No screenplay: a rough one from speech and looks; the model is asked once for the order."""
    import http.server
    import threading
    from vse_standalone.assembly import pipeline
    media = os.path.join(samples, "media")
    quiet = lambda t: None  # noqa: E731
    os.environ.pop("VSE_ASSEMBLY_LLM_URL", None)
    os.environ.pop("LLM_BASE_URL", None)
    doc = pipeline.assemble(media, None, model=model, log=quiet)
    scenes = [[el["text"] for el in s["elements"] if el["type"] == "dialogue"] for s in doc["scenes"]]
    check("no script: a scene per look, in timecode order", len(scenes) == 3 and len(scenes[0]) == 4
          and "last bus" in scenes[0][0] and "coffee" in scenes[1][0], scenes)
    check("no script: the slate is not a line", not any("take" in t.lower() for sc in scenes for t in sc), scenes)
    check("no script: every line has its takes", all(
        el.get("cut") for s in doc["scenes"] for el in s["elements"] if el["type"] == "dialogue"))
    _s, second = editing_script.find(doc, doc["scenes"][0]["elements"][1]["id"])
    check("no script: alternates from the other takes", len(second["takes"]) == 3,
          [t["clip"] for t in second["takes"]])

    requests = []

    class Model(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            reply = {"choices": [{"message": {"content": 'Order: ["S3", "S1", "S2"]'}}]}
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    os.environ["VSE_ASSEMBLY_LLM_URL"] = "http://127.0.0.1:{:d}".format(httpd.server_port)
    try:
        doc = pipeline.assemble(media, None, model=model, log=quiet)
    finally:
        httpd.shutdown()
        del os.environ["VSE_ASSEMBLY_LLM_URL"]
    check("no script: the model is asked exactly once", len(requests) == 1, len(requests))
    check("no script: the model's order is used", [s["id"] for s in doc["scenes"]] == ["S3", "S1", "S2"]
          and doc.get("order_suggested_by") == "model", [s["id"] for s in doc["scenes"]])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--model", default="base.en")
    parser.add_argument("--samples", help="reuse a sample folder made by make_samples.py")
    parser.add_argument("--keep", help="make the samples here and keep them")
    args = parser.parse_args()
    samples = args.samples or args.keep or tempfile.mkdtemp(prefix="assembly_test_")
    try:
        if not args.samples:
            if shutil.which("espeak-ng") and shutil.which("ffmpeg"):
                subprocess.run([sys.executable, os.path.join(HERE, "assembly", "make_samples.py"),
                                samples], check=True)
            elif args.full:
                sys.exit("--full needs espeak-ng and ffmpeg to make the test shoot")
            else:
                import make_samples
                os.makedirs(samples, exist_ok=True)
                make_samples.write_pdf(os.path.join(samples, "script.pdf"), make_samples.script_pages())
        lines = test_rules(samples)
        if args.full:
            test_full(samples, args.model, lines)
    finally:
        if not (args.samples or args.keep):
            shutil.rmtree(samples, ignore_errors=True)
    print("{:d} failed".format(len(failures)) if failures else "all passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
