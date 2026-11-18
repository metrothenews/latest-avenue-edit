# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Read a screenplay PDF into scenes, action, characters and dialogue. Rules only, no model.

``pdftotext -layout`` keeps each line's indentation, and screenplays are laid out on fixed
columns (Courier 12: action at 1.5", dialogue at 2.5", parentheticals at 3.1", character cues
at 3.7", transitions flush right). The rules below use that indentation *relative to the
action column*, plus the content rules every screenplay follows (scene headings start with
INT./EXT., cues are upper case and are directly followed by speech). When a PDF has lost its
layout (every line flush left) the content rules alone are used, the same way Fountain plain
text is read.

Standard library only; needs the ``pdftotext`` command (poppler) for PDFs. Plain-text and
Fountain files are read directly.
"""

import collections
import os
import re
import subprocess

HEADING = re.compile(
    r"^(?:\d+[A-Z]?\s+)?"                                   # optional scene number
    r"((?:INT\.?\s*/\s*EXT|EXT\.?\s*/\s*INT|INT|EXT|I\s*/\s*E|EST)[.\s].*?)"
    r"(?:\s+\d+[A-Z]?)?$")                                  # number repeated on the right
FORCED_HEADING = re.compile(r"^\.(?=[A-Za-z0-9])(.+)$")     # Fountain ".HEADING"
TRANSITION = re.compile(r"^(?:[A-Z ]+TO:|FADE (?:IN|OUT)[.:]?|FADE TO BLACK\.?|CUT TO BLACK\.?|"
                        r"SMASH CUT\.?|THE END\.?|END\.?)$")
EXTENSION = re.compile(r"\s*\(([^)]*)\)\s*")
PAGE_NUMBER = re.compile(r"^\d{1,3}\.?$")
NOISE = re.compile(r"^\(?(?:MORE|CONTINUED|CONT'D|CONT’D)\)?:?$|^CONTINUED:(?:\s*\(\d+\))?$")


def pdf_to_text(path):
    proc = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", path, "-"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("pdftotext failed on {:s}: {:s}".format(path, proc.stderr.strip()))
    return proc.stdout


def _is_upper(text):
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(not c.islower() for c in letters)


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _clean_lines(text):
    """Page breaks, page numbers and (MORE)/(CONTINUED) markers removed; tabs expanded."""
    out = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.expandtabs(8).rstrip()
        if "\f" in line:
            line = line.replace("\f", "")
            out.append("")              # a page break ends a paragraph
        stripped = line.strip()
        if PAGE_NUMBER.match(stripped) and (_indent(line) > 40 or not out or not out[-1].strip()):
            continue
        if NOISE.match(stripped):
            continue
        out.append(line)
    return out


def _action_column(lines):
    """The most common indentation of long lines: the action column."""
    counts = collections.Counter(_indent(l) for l in lines if len(l.strip()) > 30)
    if not counts:
        counts = collections.Counter(_indent(l) for l in lines if l.strip())
    if not counts:
        return 0
    common = [i for i, n in counts.most_common() if n >= max(2, counts.most_common(1)[0][1] * 0.2)]
    return min(common) if common else counts.most_common(1)[0][0]


def _heading_parts(heading):
    text = re.sub(r"\s+", " ", heading.strip().rstrip("."))
    m = re.match(r"^(INT\.?\s*/\s*EXT\.?|EXT\.?\s*/\s*INT\.?|INT\.?|EXT\.?|I\s*/\s*E\.?|EST\.?)\s*(.*)$",
                 text, re.I)
    int_ext, rest = (m.group(1), m.group(2)) if m else ("", text)
    int_ext = re.sub(r"[.\s]", "", int_ext).upper().replace("I/E", "INT/EXT")
    location, time = rest, None
    parts = re.split(r"\s+[-–—]+\s+", rest)
    if len(parts) > 1:
        location, time = " - ".join(parts[:-1]), parts[-1].strip(". ")
    return int_ext or None, location.strip() or None, time


def _cue_parts(line):
    """"MAYA (V.O.) (CONT'D)" -> ("MAYA", ["V.O."])."""
    extensions = [e.strip() for e in EXTENSION.findall(line)]
    name = EXTENSION.sub(" ", line).strip().rstrip(":^").strip()
    extensions = [e for e in extensions if e.upper() not in ("CONT'D", "CONT’D", "CONTD", "CONT.")]
    return name, extensions


def parse_text(text, source=None):
    """Screenplay text (pdftotext -layout output, Fountain or plain) -> script dict."""
    lines = _clean_lines(text)
    base = _action_column(lines)
    flat = all(_indent(l) - base < 6 for l in lines if l.strip())

    def rel(line):
        return _indent(line) - base

    scenes = []
    preamble = []
    current = None
    counter = {"scene": 0}

    def new_scene(heading, number=None):
        counter["scene"] += 1
        int_ext, location, time = _heading_parts(heading)
        scene = {"id": "S{:d}".format(counter["scene"]), "number": number,
                 "heading": re.sub(r"\s+", " ", heading.strip()), "int_ext": int_ext,
                 "location": location, "time": time, "elements": []}
        scenes.append(scene)
        return scene

    def add(element):
        nonlocal current
        if current is None:
            if element["type"] in ("action", "transition") and not scenes:
                preamble.append(element["text"])
                return
            current = new_scene("", None)
            current["heading"] = None
        element["id"] = "{:s}-{:d}".format(current["id"], len(current["elements"]) + 1)
        current["elements"].append(element)

    def next_nonblank(i):
        return lines[i + 1] if i + 1 < len(lines) and lines[i + 1].strip() else None

    i = 0
    paragraph = []

    def flush_action():
        if paragraph:
            text = " ".join(p.strip() for p in paragraph)
            add({"type": "action", "text": re.sub(r"\s+", " ", text)})
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_action()
            i += 1
            continue

        m = HEADING.match(stripped) if _is_upper(stripped.split(" - ")[0]) else None
        forced = FORCED_HEADING.match(stripped)
        if (m or forced) and rel(line) < 15:
            flush_action()
            heading = (m.group(1) if m else forced.group(1)).strip()
            num = re.match(r"^(\d+[A-Z]?)\s", stripped)
            current = new_scene(heading, num.group(1) if num else None)
            i += 1
            continue

        if _is_upper(stripped) and (TRANSITION.match(stripped) or (not flat and rel(line) >= 35)):
            flush_action()
            add({"type": "transition", "text": stripped})
            i += 1
            continue

        following = next_nonblank(i)
        is_cue = (_is_upper(stripped) and len(stripped) <= 50 and following is not None
                  and not paragraph and not TRANSITION.match(stripped)
                  and not HEADING.match(following.strip())
                  and (flat or (rel(line) >= 15 and rel(following) >= 5)))
        if is_cue:
            flush_action()
            name, extensions = _cue_parts(stripped)
            speech, parentheticals = [], []
            i += 1
            while i < len(lines) and lines[i].strip():
                part = lines[i].strip()
                if not flat and rel(lines[i]) < 5:
                    break               # back at the action column without a blank line
                if part.startswith("(") and (part.endswith(")") or not speech):
                    # Parenthetical, possibly over two lines.
                    text = part
                    while not text.endswith(")") and i + 1 < len(lines) and lines[i + 1].strip():
                        i += 1
                        text += " " + lines[i].strip()
                    parentheticals.append({"before_word": sum(len(s.split()) for s in speech),
                                           "text": text})
                else:
                    speech.append(part)
                i += 1
            add({"type": "dialogue", "character": name, "extensions": extensions,
                 "parentheticals": parentheticals,
                 "text": re.sub(r"\s+", " ", " ".join(speech)).strip()})
            continue

        paragraph.append(stripped)
        i += 1
    flush_action()

    characters = collections.Counter(
        e["character"] for s in scenes for e in s["elements"] if e["type"] == "dialogue")
    title = None
    for text in preamble:
        if text and not re.match(r"(?i)^(written by|by|draft|copyright|©)", text):
            title = text
            break
    return {
        "source": os.path.abspath(source) if source else None,
        "title": title,
        "title_page": preamble,
        "characters": [{"name": n, "lines": c} for n, c in characters.most_common()],
        "scenes": scenes,
    }


def parse(path):
    """Screenplay file (.pdf, .fountain, .txt) -> script dict."""
    if path.lower().endswith(".pdf"):
        text = pdf_to_text(path)
    else:
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
    return parse_text(text, source=path)


def dialogue_lines(script):
    """Every dialogue element in script order, with its scene id."""
    return [dict(e, scene=s["id"]) for s in script["scenes"] for e in s["elements"]
            if e["type"] == "dialogue"]
