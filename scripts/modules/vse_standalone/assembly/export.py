# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Export an editing script as a sequence other editors open, through OpenTimelineIO.

The editing script's cut becomes one OTIO timeline (picture on V1, sound on A1, a marker per
scene, each clip tagged with its script line), written by OTIO's adapters:

==========  ==========================  ========================================
format      file                        opens in
==========  ==========================  ========================================
``otio``    ``.otio``                   DaVinci Resolve 18+, anything with OTIO
``fcpxml``  ``.fcpxml``                 Final Cut Pro, DaVinci Resolve
``xml``     ``.xml`` (FCP 7 XML)        Premiere Pro, DaVinci Resolve
``edl``     ``.edl`` (CMX 3600)         everything (picture only, 8-character reels)
==========  ==========================  ========================================

Source times use each clip's own start timecode, so relinking in the other editor finds the
right frames. Needs ``opentimelineio`` (+ ``otio-fcpx-xml-adapter``, ``otio-fcp-adapter``,
``otio-cmx3600-adapter``) in the assembly's environment.
"""

import os
import re
import urllib.parse
import urllib.request

from . import editing_script

FORMATS = {"otio": ("otio_json", ".otio"), "fcpxml": ("fcpx_xml", ".fcpxml"),
           "xml": ("fcp_xml", ".xml"), "edl": ("cmx_3600", ".edl")}


def _url(path):
    return urllib.parse.urljoin("file:", urllib.request.pathname2url(os.path.abspath(path)))


def _reel(name, used):
    base = re.sub(r"[^A-Z0-9]", "", os.path.splitext(name)[0].upper())[-8:] or "AX"
    reel, n = base, 1
    while used.get(reel, name) != name:
        n += 1
        reel = (base[:8 - len(str(n))] + str(n))
    used[reel] = name
    return reel


START_TIMECODE = 3600.0          # seconds: sequences start at 01:00:00:00


def _fcpxml_start(path, seconds):
    import re as _re
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    start = "{:d}/1s".format(int(round(seconds)))
    text = _re.sub(r"<sequence(?![^>]*tcStart)", '<sequence tcStart="{:s}" tcFormat="NDF"'.format(start), text)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _edl_record_offset(path, frames, rate):
    """Shift every event's record in/out (the last two timecodes) by `frames`."""
    import re as _re
    fps = int(round(rate))

    def tc_to(n):
        f = n % fps
        s = n // fps
        return "{:02d}:{:02d}:{:02d}:{:02d}".format(s // 3600, s // 60 % 60, s % 60, f)

    def tc_from(t):
        h, m, sec, f = (int(x) for x in _re.split(r"[:;]", t))
        return ((h * 60 + m) * 60 + sec) * fps + f

    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = _re.match(r"^(\d{3,4}\s.*?)(\d\d:\d\d:\d\d[:;]\d\d)(\s+)(\d\d:\d\d:\d\d[:;]\d\d)(\s*)$",
                          line.rstrip("\n"))
            if m:
                line = "{:s}{:s}{:s}{:s}{:s}\n".format(m.group(1), tc_to(tc_from(m.group(2)) + frames), m.group(3),
                                                     tc_to(tc_from(m.group(4)) + frames), m.group(5))
            out.append(line)
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(out)


def timeline(doc, start_timecode=START_TIMECODE):
    """Editing script -> opentimelineio.schema.Timeline."""
    import opentimelineio as otio
    rate = float(doc.get("fps") or 24)
    rt = lambda seconds: otio.opentime.RationalTime(round(seconds * rate), rate)  # noqa: E731
    tl = otio.schema.Timeline(name=doc.get("title") or "assembly",
                              global_start_time=rt(start_timecode))
    video = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    audio = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(video)
    tl.tracks.append(audio)
    reels, refs = {}, {}

    def ref(cid):
        if cid not in refs:
            m = doc["media"][cid]
            start = m.get("timecode_seconds") or m.get("time_reference_seconds") or 0.0
            available = None
            if m.get("duration"):
                available = otio.opentime.TimeRange(rt(start), rt(m["duration"]))
            refs[cid] = (otio.schema.ExternalReference(target_url=_url(m["path"]),
                                                       available_range=available), start, m)
        r, start, m = refs[cid]
        return r.clone(), start, m

    def clip(cid, cut_in, length, element, text):
        r, start, m = ref(cid)
        kind = m["kind"]
        c = otio.schema.Clip(
            name=m["name"], media_reference=r,
            source_range=otio.opentime.TimeRange(rt(start + (0 if kind == "image" else cut_in)), length))
        c.metadata["latest_avenue"] = {"line": element, "text": text, "clip": cid}
        c.metadata["cmx_3600"] = {"reel": _reel(m["name"], reels)}
        return c

    texts = {el["id"]: el.get("text", "") for s in doc["scenes"] for el in s["elements"]}
    headings = {s["id"]: s.get("heading") or s["id"] for s in doc["scenes"]}
    last_scene = None
    for ev in editing_script.timeline(doc):
        length = otio.opentime.RationalTime(max(1, round((ev["out"] - ev["in"]) * rate)), rate)
        pic = ev["picture"]
        v = clip(pic["clip"], pic["in"], length, ev["element"], texts.get(ev["element"], "")) if pic \
            else otio.schema.Gap(duration=length)
        if ev["scene"] != last_scene and pic:
            v.markers.append(otio.schema.Marker(
                name=headings.get(ev["scene"], ev["scene"]),
                marked_range=otio.opentime.TimeRange(v.source_range.start_time,
                                                     otio.opentime.RationalTime(1, rate)),
                color=otio.schema.MarkerColor.RED))
        last_scene = ev["scene"]
        video.append(v)
        if ev["sound"]:
            sound_clip = next((c for c, m in doc["media"].items() if m["path"] == ev["sound_path"]), ev["clip"])
            a = clip(sound_clip, ev["sound_in"], length, ev["element"], texts.get(ev["element"], ""))
        else:
            a = otio.schema.Gap(duration=length)
        audio.append(a)
    return tl


def export(doc, output, fmt=None):
    """Write the sequence. fmt: otio / fcpxml / xml / edl (default from the file extension)."""
    import opentimelineio as otio
    if fmt is None:
        ext = os.path.splitext(output)[1].lower()
        fmt = next((k for k, (_a, e) in FORMATS.items() if e == ext), None)
        if fmt is None:
            raise ValueError("Unknown sequence format for {:s}: use .otio, .fcpxml, .xml or .edl".format(output))
    if fmt not in FORMATS:
        raise ValueError("format must be one of " + ", ".join(FORMATS))
    adapter, _ext = FORMATS[fmt]
    tl = timeline(doc)
    if fmt == "edl":
        # CMX 3600 holds one picture track; sound stays in the other formats.
        tl.tracks[:] = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video]
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    otio.adapters.write_to_file(tl, output, adapter_name=adapter)
    # Sequences start at 01:00:00:00, the usual convention (OTIO and FCP 7 XML carry the
    # timeline's start already; the FCPXML and CMX 3600 adapters don't).
    rate = float(doc.get("fps") or 24)
    if fmt == "fcpxml":
        _fcpxml_start(output, START_TIMECODE)
    elif fmt == "edl":
        _edl_record_offset(output, int(round(START_TIMECODE * rate)), rate)
    return {"filepath": os.path.abspath(output), "format": fmt,
            "events": sum(1 for c in tl.tracks[0] if isinstance(c, otio.schema.Clip)),
            "duration": round(tl.duration().to_seconds(), 3)}
