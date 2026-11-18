# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Check exported sequences without the target editors.

* **FCPXML** (Final Cut Pro, Resolve): validated against Apple's DTD with ``xmllint`` when the
  DTD file is given (Apple's, not shipped here: ``fetch_fcpxml_dtd`` downloads it), plus the
  timing rules a DTD can't express: rational times, spine clips back to back, every
  ``ref`` resolving to an asset or format.
* **FCP 7 XML** (``xmeml``, Premiere Pro, Resolve): the structure Premiere's importer needs, and
  per clip item: ``end - start == out - in``, clips in a track in order without overlaps, every
  ``file`` defined once with a ``pathurl`` and referenced by id after that.
* **CMX 3600 EDL**: event numbers 001-999 in order, reel names of at most 8 characters, track
  and transition fields, valid timecodes (frames below the rate), source and record durations
  equal, record times contiguous and increasing, comment lines starting with ``*``.

Each checker returns a list of problems (empty when the file is fine). Standard library only.
"""

import os
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from fractions import Fraction

FCPXML_DTD_URL = ("https://raw.githubusercontent.com/CommandPost/CommandPost/develop/src/extensions/"
                  "cp/apple/fcpxml/dtd/FCPXMLv{:s}.dtd")


def fetch_fcpxml_dtd(version, folder):
    """Download Apple's FCPXML DTD for a version ("1.8") into folder. -> path or None."""
    path = os.path.join(folder, "FCPXMLv{:s}.dtd".format(version.replace(".", "_")))
    if os.path.exists(path):
        return path
    try:
        os.makedirs(folder, exist_ok=True)
        with urllib.request.urlopen(FCPXML_DTD_URL.format(version.replace(".", "_")), timeout=30) as r:
            data = r.read()
        if b"<!ELEMENT fcpxml" not in data:
            return None
        with open(path, "wb") as fh:
            fh.write(data)
        return path
    except Exception:
        return None


def _rational(text):
    """"1001/24000s" or "5s" -> Fraction, else raise ValueError."""
    m = re.fullmatch(r"(-?\d+)(?:/(\d+))?s", text or "")
    if not m:
        raise ValueError("not a rational time: {!r}".format(text))
    return Fraction(int(m.group(1)), int(m.group(2) or 1))


def check_fcpxml(path, dtd=None):
    problems = []
    if dtd:
        proc = subprocess.run(["xmllint", "--noout", "--dtdvalid", dtd, path],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            problems += ["DTD: " + l for l in proc.stderr.strip().splitlines()[:20]]
    root = ET.parse(path).getroot()
    if root.tag != "fcpxml" or not root.get("version"):
        return problems + ["root is not <fcpxml version=...>"]
    ids = {el.get("id") for el in root.iter() if el.get("id")}
    for el in root.iter():
        for attr in ("ref", "format"):
            if el.get(attr) and el.get(attr) not in ids:
                problems.append("{:s} {:s}={!r} points at nothing".format(el.tag, attr, el.get(attr)))
        for attr in ("offset", "start", "duration", "frameDuration", "tcStart"):
            if el.get(attr) is not None:
                try:
                    _rational(el.get(attr))
                except ValueError as ex:
                    problems.append("{:s} {:s}: {:s}".format(el.tag, attr, str(ex)))
    for spine in root.iter("spine"):
        t = None
        for clip in spine:
            if clip.get("offset") is None or clip.get("duration") is None:
                continue
            off, dur = _rational(clip.get("offset")), _rational(clip.get("duration"))
            if dur <= 0:
                problems.append("spine item {!r} has no length".format(clip.get("name")))
            if t is not None and off != t:
                problems.append("spine item {!r} starts at {} but the previous ends at {}".format(
                    clip.get("name"), off, t))
            t = off + dur
    if not list(root.iter("spine")):
        problems.append("no <spine>: no sequence in the file")
    return problems


def _int(el, tag):
    child = el.find(tag)
    if child is None or child.text is None or not re.fullmatch(r"-?\d+", child.text.strip()):
        raise ValueError("<{:s}> missing or not a whole number".format(tag))
    return int(child.text)


def check_fcp7_xml(path):
    problems = []
    root = ET.parse(path).getroot()
    if root.tag != "xmeml" or not root.get("version"):
        return ["root is not <xmeml version=...>"]
    seqs = list(root.iter("sequence"))
    if not seqs:
        return ["no <sequence>"]
    defined = {}
    for seq in seqs:
        rate = seq.find("rate")
        if rate is None or rate.find("timebase") is None:
            problems.append("sequence {!r} has no <rate><timebase>".format(seq.findtext("name")))
        media = seq.find("media")
        if media is None or media.find("video") is None:
            problems.append("sequence has no <media><video>")
            continue
        for kind in ("video", "audio"):
            section = media.find(kind)
            if section is None:
                continue
            for n, track in enumerate(section.findall("track"), 1):
                last_end = None
                for item in track.findall("clipitem"):
                    name = item.findtext("name") or item.get("id")
                    try:
                        start, end = _int(item, "start"), _int(item, "end")
                        cin, cout = _int(item, "in"), _int(item, "out")
                    except ValueError as ex:
                        problems.append("{:s} track {:d} clip {!r}: {:s}".format(kind, n, name, str(ex)))
                        continue
                    if end - start != cout - cin:
                        problems.append("{:s} track {:d} clip {!r}: length on the timeline {:d} != in the source {:d}".format(
                            kind, n, name, end - start, cout - cin))
                    if end <= start or cin < 0:
                        problems.append("{:s} track {:d} clip {!r}: empty or negative".format(kind, n, name))
                    if last_end is not None and start < last_end:
                        problems.append("{:s} track {:d} clip {!r} overlaps the previous one".format(kind, n, name))
                    last_end = end
                    f = item.find("file")
                    if f is None or not f.get("id"):
                        problems.append("clip {!r} has no <file id=...>".format(name))
                        continue
                    if len(f):          # a definition (has children): needs a file:// pathurl
                        url = f.findtext("pathurl")
                        if not url:
                            problems.append("file {!r} defined without <pathurl>".format(f.get("id")))
                        elif not url.startswith("file://"):
                            problems.append("file {!r}: pathurl is not a file:// URL".format(f.get("id")))
                        defined[f.get("id")] = url
                    elif f.get("id") not in defined:   # a reference: must come after its definition
                        problems.append("file {!r} used before it is defined".format(f.get("id")))
    return problems


TC = re.compile(r"^(\d\d):(\d\d):(\d\d)[:;](\d\d)$")
EVENT = re.compile(r"^(\d{3,4})\s+(\S{1,8})\s+(V|A|A2|B|AA|AA/V|A/V|NONE)\s+(C|D|W\d{3}|K\s*[BO]?)\s+"
                   r"(?:(\d{3})\s+)?(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")


def _frames(tc, fps):
    m = TC.match(tc)
    if not m:
        raise ValueError("bad timecode {!r}".format(tc))
    h, mi, s, f = (int(x) for x in m.groups())
    if mi > 59 or s > 59 or f >= round(fps):
        raise ValueError("timecode out of range {!r}".format(tc))
    return ((h * 60 + mi) * 60 + s) * round(fps) + f


def check_edl(path, fps):
    problems = []
    with open(path, encoding="ascii", errors="replace") as fh:
        lines = [l.rstrip("\r\n") for l in fh]
    if not lines or not lines[0].startswith("TITLE:"):
        problems.append("first line is not TITLE:")
    elif len(lines[0]) > 76:
        problems.append("TITLE line longer than 70 characters")
    last_num, last_rec_out, events = 0, None, 0
    for n, line in enumerate(lines[1:], 2):
        if not line.strip() or line.startswith("*") or line.startswith("FCM:") or line.startswith(">>>"):
            continue
        if any(ord(c) > 126 for c in line):
            problems.append("line {:d}: non-ASCII characters".format(n))
        m = EVENT.match(line)
        if not m:
            problems.append("line {:d}: not an event, comment or FCM line: {!r}".format(n, line[:60]))
            continue
        events += 1
        num = int(m.group(1))
        if num <= last_num or num > 999:
            problems.append("line {:d}: event number {:d} out of order".format(n, num))
        last_num = num
        try:
            sin, sout, rin, rout = (_frames(m.group(k), fps) for k in (6, 7, 8, 9))
        except ValueError as ex:
            problems.append("line {:d}: {:s}".format(n, str(ex)))
            continue
        if sout - sin != rout - rin:
            problems.append("line {:d}: source length {:d} != record length {:d} frames".format(n, sout - sin, rout - rin))
        if rout <= rin:
            problems.append("line {:d}: empty event".format(n))
        if last_rec_out is not None and rin < last_rec_out:
            problems.append("line {:d}: record in goes back in time".format(n))
        last_rec_out = rout
    if events == 0:
        problems.append("no events")
    return problems
