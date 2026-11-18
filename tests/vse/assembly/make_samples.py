#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Make a small test shoot: a two-scene screenplay PDF, takes with spoken dialogue (espeak-ng),
a voice-over recorded on its own, B-roll with no speech, and a still photo.

    python3 tests/vse/assembly/make_samples.py OUTPUT_FOLDER

Writes ``OUTPUT_FOLDER/script.pdf``, ``OUTPUT_FOLDER/media/...`` and ``OUTPUT_FOLDER/truth.json``
(which take says which script line), so tests can check the assembly. Needs ffmpeg and
espeak-ng. The PDF is written directly (Courier 12 on the standard screenplay columns), so
no word processor is needed.
"""

import json
import os
import subprocess
import sys
import wave

# Screenplay columns in inches from the left edge of the page.
COLUMN = {"action": 1.5, "dialogue": 2.5, "paren": 3.1, "cue": 3.7, "transition": 6.0}

SCRIPT = [
    ("title", "THE LAST BUS"),
    ("title", "Written by Test Writer"),
    ("page", None),
    ("transition", "FADE IN:"),
    ("heading", "INT. BUS STATION - NIGHT"),
    ("action", "Rain against the windows. MAYA (30s) waits alone on a bench, a ticket in her "
               "hand. The departures board is blank."),
    ("cue", "DANIEL"), ("paren", "(out of breath)"),
    ("dialogue", "Is this the last bus to the coast?"),
    ("cue", "MAYA"),
    ("dialogue", "It left ten minutes ago. You missed it."),
    ("cue", "DANIEL"),
    ("dialogue", "Then I guess we are both stuck here tonight."),
    ("action", "Maya looks up at the clock above the door."),
    ("cue", "MAYA"),
    ("dialogue", "There is a diner across the street. They make terrible coffee, but the pie "
                 "is good."),
    ("transition", "CUT TO:"),
    ("heading", "EXT. DINER - NIGHT"),
    ("action", "A neon sign flickers. Daniel holds the door open for her."),
    ("cue", "DANIEL"),
    ("dialogue", "Terrible coffee sounds perfect right now."),
    ("cue", "MAYA (V.O.)"),
    ("dialogue", "That was the night everything changed."),
    ("transition", "FADE OUT."),
]

LINES = {  # dialogue in script order: id used by truth.json
    "L1": "Is this the last bus to the coast?",
    "L2": "It left ten minutes ago. You missed it.",
    "L3": "Then I guess we are both stuck here tonight.",
    "L4": "There is a diner across the street. They make terrible coffee, but the pie is good.",
    "L5": "Terrible coffee sounds perfect right now.",
    "L6": "That was the night everything changed.",
}

DANIEL, MAYA, CREW = "en-us+m3", "en-us+f3", "en-us+m7"

# Each take: file, picture source, list of (voice, text, line id or None).
TAKES = [
    ("A001_C001_S1_T1.mov", "wide1", [
        (CREW, "Scene one, take one. Action.", None),
        (DANIEL, LINES["L1"], "L1"), (MAYA, LINES["L2"], "L2"), (DANIEL, LINES["L3"], "L3"),
        (MAYA, LINES["L4"], "L4"), (CREW, "Cut.", None)]),
    ("A001_C002_S1_T2.mov", "wide1", [
        (CREW, "Scene one, take two. Action.", None),
        (DANIEL, LINES["L1"], "L1"),
        (MAYA, "It left twenty minutes ago. You missed it.", "L2"),
        (DANIEL, LINES["L3"], "L3"),
        (MAYA, "There's a diner across the street. The coffee is terrible.", "L4"),
        (CREW, "Cut.", None)]),
    ("A002_C001_S1_T3_CU.mov", "close1", [
        (MAYA, LINES["L2"], "L2"), (MAYA, LINES["L4"], "L4")]),
    ("A003_C001_S2_T1.mov", "wide2", [
        (CREW, "Scene two, take one.", None), (DANIEL, LINES["L5"], "L5")]),
    ("A003_C002_S2_T2.mov", "wide2", [
        (DANIEL, "Terrible coffee sounds perfect.", "L5")]),
]
VOICE_OVER = ("VO_MAYA_01.wav", [(MAYA, LINES["L6"], "L6")])
BROLL = ("A003_C003_BROLL.mov", "broll2", 4.0)

PICTURES = {
    # Scene 1: a slow zoom into the same fractal (the close-up is a tighter crop of it).
    "wide1": "mandelbrot=size=640x360:rate=24:start_scale=3",
    "close1": "mandelbrot=size=640x360:rate=24:start_scale=3,crop=320:180:200:90,scale=640:360",
    # Scene 2: a cellular automaton in green; the B-roll is the same look.
    "wide2": "life=size=640x360:rate=24:seed=7:mold=10:life_color=#40ff80:death_color=#002010",
    "broll2": "life=size=640x360:rate=24:seed=9:mold=10:life_color=#40ff80:death_color=#002010",
}


# -----------------------------------------------------------------------------
# PDF

def _pdf_escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path, pages):
    """pages: list of lists of (x_inches, y_inches_from_top, text). Courier 12."""
    objects = []

    def obj(body):
        objects.append(body)
        return len(objects)

    font = obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    pages_id = len(objects) + 1 + 2 * len(pages)  # reserved below
    page_ids = []
    for page in pages:
        ops = ["BT /F1 12 Tf"]
        for x, y, text in page:
            ops.append("1 0 0 1 {:.2f} {:.2f} Tm ({:s}) Tj".format(
                x * 72, 792 - y * 72, _pdf_escape(text)))
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content = obj(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(obj(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (pages_id, content, font)))
    assert obj(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % p for p in page_ids), len(page_ids))) == pages_id
    catalog = obj(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, catalog, xref)
    with open(path, "wb") as fh:
        fh.write(out)


def _wrap(text, width):
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    return lines + ([line] if line else [])


def script_pages():
    pages, page, y = [], [], 1.0
    widths = {"action": 60, "dialogue": 35, "paren": 25}

    def put(x, text):
        nonlocal y, page
        if y > 10.0:
            pages.append(page)
            page, y = [], 1.0
            page.append((7.0, 0.5, "{:d}.".format(len(pages) + 1)))
        page.append((x, y, text))
        y += 1 / 6

    for kind, text in SCRIPT:
        if kind == "page":
            pages.append(page)
            page, y = [], 1.0
            continue
        if kind == "title":
            page.append((4.25 - len(text) * 0.05, 4.0 + 0.5 * len(page), text))
            continue
        if kind in ("heading", "action", "transition", "cue") and page:
            y += 1 / 6
        if kind == "transition":
            put(7.5 - len(text) * 0.1, text)
        elif kind == "heading":
            put(COLUMN["action"], text)
        elif kind == "cue":
            put(COLUMN["cue"], text)
        else:
            for part in _wrap(text, widths[kind]):
                put(COLUMN[kind], part)
    pages.append(page)
    return pages


# -----------------------------------------------------------------------------
# Audio and video

def _speak(voice, text, path):
    subprocess.run(["espeak-ng", "-v", voice, "-s", "150", "-w", path, text], check=True)


def make_speech(parts, path, work, gap=0.8, lead=0.5):
    """Concatenate spoken parts with silence between. -> list of (line id, start, end)."""
    chunks, timeline = [], []
    rate = None
    t = 0.0
    for n, (voice, text, line_id) in enumerate(parts):
        tmp = os.path.join(work, "part{:d}.wav".format(n))
        _speak(voice, text, tmp)
        with wave.open(tmp) as w:
            rate = rate or w.getframerate()
            frames = w.readframes(w.getnframes())
            params = w.getparams()
        silence = b"\0\0" * int(rate * (lead if n == 0 else gap))
        chunks += [silence, frames]
        t += len(silence) / 2 / rate
        dur = len(frames) / 2 / rate
        if line_id:
            timeline.append({"line": line_id, "text": text, "start": round(t, 2),
                             "end": round(t + dur, 2)})
        t += dur
        os.remove(tmp)
    chunks.append(b"\0\0" * int(rate * lead))
    with wave.open(path, "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(chunks))
    return timeline, t + lead


def make_video(path, picture, duration, audio=None, timecode="01:00:00:00"):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", "{:.3f}".format(duration),
           "-i", PICTURES[picture]]
    if audio:
        cmd += ["-i", audio]
    else:
        cmd += ["-f", "lavfi", "-t", "{:.3f}".format(duration),
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    cmd += ["-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest",
            "-timecode", timecode, path]
    subprocess.run(cmd, check=True)


def main():
    out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "assembly_samples")
    media = os.path.join(out, "media")
    work = os.path.join(out, ".work")
    for d in (media, work):
        os.makedirs(d, exist_ok=True)
    write_pdf(os.path.join(out, "script.pdf"), script_pages())

    truth = {"lines": LINES, "takes": {}}
    for n, (name, picture, parts) in enumerate(TAKES):
        wav = os.path.join(work, name + ".wav")
        timeline, duration = make_speech(parts, wav, work)
        make_video(os.path.join(media, name), picture, duration, wav,
                   timecode="01:{:02d}:00:00".format(n + 1))
        truth["takes"][name] = timeline
    name, parts = VOICE_OVER
    timeline, _ = make_speech(parts, os.path.join(media, name), work)
    truth["takes"][name] = timeline
    name, picture, duration = BROLL
    make_video(os.path.join(media, name), picture, duration, timecode="01:10:00:00")
    truth["takes"][name] = []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", PICTURES["wide2"],
                    "-frames:v", "1", os.path.join(media, "STILL_diner_sign.jpg")], check=True)
    for f in os.listdir(work):
        os.remove(os.path.join(work, f))
    os.rmdir(work)
    with open(os.path.join(out, "truth.json"), "w") as fh:
        json.dump(truth, fh, indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main()
