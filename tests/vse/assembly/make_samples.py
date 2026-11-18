#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Make a small test shoot: a two-scene screenplay PDF, takes with spoken dialogue (espeak-ng),
a voice-over recorded on its own, B-roll with no speech, and a still photo.

    python3 tests/vse/assembly/make_samples.py OUTPUT_FOLDER [--tiny] [--no-music]

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
    # No slate in the name, and the camera's own sound is useless (wind): only the clap
    # at the head ties it to its recorder file.
    ("IMG_0042.MOV", "wide2", [
        (DANIEL, LINES["L5"], "L5")]),
]
VOICE_OVER = ("VO_MAYA_01.wav", [(MAYA, LINES["L6"], "L6")])
BROLL = ("A003_C003_BROLL.mov", "broll2", 4.0)

# Frame size, rate, sound format, and the length of clips without speech (None: as before);
# --tiny makes 124x70 pixel, 1-second, 16 kHz mono files.
FORMAT = {"w": 640, "h": 360, "rate": 24, "ar": 48000, "ac": 2, "abr": "128k", "clip": None}


def pictures():
    w, h, r = FORMAT["w"], FORMAT["h"], FORMAT["rate"]
    return {
        # Scene 1: a slow zoom into the same fractal (the close-up is a tighter crop of it).
        "wide1": "mandelbrot=size={w}x{h}:rate={r}:start_scale=3".format(w=w, h=h, r=r),
        "close1": "mandelbrot=size={w}x{h}:rate={r}:start_scale=3,crop={cw}:{ch}:{x}:{y},scale={w}:{h}".format(
            w=w, h=h, r=r, cw=w // 2, ch=h // 2, x=w * 5 // 16, y=h // 4),
        # Scene 2: a cellular automaton in green; the B-roll is the same look.
        "wide2": "life=size={w}x{h}:rate={r}:seed=7:mold=10:life_color=#40ff80:death_color=#002010".format(w=w, h=h, r=r),
        "broll2": "life=size={w}x{h}:rate={r}:seed=9:mold=10:life_color=#40ff80:death_color=#002010".format(w=w, h=h, r=r),
        # Music video pictures: three more looks.
        "red": "testsrc2=size={w}x{h}:rate={r},hue=h=0:s=2,colorchannelmixer=rr=1:gg=0.2:bb=0.2".format(w=w, h=h, r=r),
        "blue": "smptebars=size={w}x{h}:rate={r},colorchannelmixer=rr=0.1:gg=0.3:bb=1".format(w=w, h=h, r=r),
        "grey": "cellauto=size={w}x{h}:rate={r}:rule=110,format=gray".format(w=w, h=h, r=r),
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
            # Where the voice really starts and stops (espeak pads with silence).
            import array
            pcm = array.array("h", frames)
            loud = [k for k in range(0, len(pcm), rate // 100) if abs(pcm[k]) > 500
                    or max(abs(v) for v in pcm[k:k + rate // 100]) > 500]
            a = loud[0] / rate if loud else 0.0
            b = (loud[-1] + rate // 100) / rate if loud else dur
            timeline.append({"line": line_id, "text": text, "start": round(t + a, 2),
                             "end": round(t + b, 2)})
        t += dur
        os.remove(tmp)
    chunks.append(b"\0\0" * int(rate * lead))
    with wave.open(path, "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(chunks))
    return timeline, t + lead


# Separately recorded sound for two takes: (take, recorder file, seconds the recorder started
# before the camera, write a BWF timecode?). The camera keeps a worse "scratch" copy.
RECORDERS = [
    ("A001_C001_S1_T1.mov", "ZOOM0001.WAV", 2.5, True),
    ("A003_C001_S2_T1.mov", "ZOOM0002.WAV", 1.7, False),
    ("IMG_0042.MOV", "ZOOM0003.WAV", 0.9, False),        # synced by the clap
]
CLAP_TAKES = {"IMG_0042.MOV"}
WIND = "anoisesrc=color=brown:amplitude=0.3:sample_rate=16000:seed=42"
SCRATCH = "highpass=f=300,lowpass=f=3000,volume=0.4"


def add_clap(wav, at=0.25):
    """A clap (a short, very loud noise burst) into a 16-bit mono WAV, `at` seconds in."""
    import random
    with wave.open(wav) as w:
        params, frames = w.getparams(), bytearray(w.readframes(w.getnframes()))
    rate, rng = params.framerate, random.Random(5)
    start, n = int(at * rate), int(0.03 * rate)
    for k in range(n):
        v = int(30000 * (rng.random() * 2 - 1) * (1 - k / n))
        frames[2 * (start + k):2 * (start + k) + 2] = max(-32767, min(32767, v)).to_bytes(2, "little", signed=True)
    with wave.open(wav, "wb") as w:
        w.setparams(params)
        w.writeframes(bytes(frames))


def _scaled_copy(src, dst, gain):
    import array
    with wave.open(src) as w:
        params, data = w.getparams(), array.array("h", w.readframes(w.getnframes()))
    data = array.array("h", (int(v * gain) for v in data))
    with wave.open(dst, "wb") as w:
        w.setparams(params)
        w.writeframes(data.tobytes())


def make_recorder(speech, path, lead, timecode_seconds=None):
    """Clean sound starting `lead` seconds before the camera, with a little room tone."""
    ar = FORMAT["ar"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", speech, "-f", "lavfi", "-i",
           "anoisesrc=color=pink:amplitude=0.004:sample_rate={:d}:seed=7".format(ar),
           "-filter_complex", "[0:a]aresample={:d},adelay={:d}:all=1,apad=pad_dur=1[s];"
           "[s][1:a]amix=inputs=2:duration=first:normalize=0".format(ar, int(lead * 1000)),
           "-ar", str(ar), "-ac", "1", "-c:a", "pcm_s16le"]
    if timecode_seconds is not None:
        cmd += ["-write_bext", "1", "-metadata",
                "time_reference={:d}".format(int(round((timecode_seconds - lead) * ar)))]
    subprocess.run(cmd + [path], check=True)


def make_video(path, picture, duration, audio=None, timecode="01:00:00:00", scratch=False,
               wind=False):
    ar = FORMAT["ar"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", "{:.3f}".format(duration),
           "-i", pictures()[picture]]
    if audio:
        cmd += ["-i", audio]
    else:
        cmd += ["-f", "lavfi", "-t", "{:.3f}".format(duration),
                "-i", "anullsrc=channel_layout=mono:sample_rate={:d}".format(ar)]
    if wind:
        # Wind over the camera's microphone: the speech is buried, the clap still stands out.
        cmd += ["-f", "lavfi", "-t", "{:.3f}".format(duration), "-i", WIND, "-filter_complex",
                "[1:a][2:a]amix=inputs=2:duration=first:normalize=0[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        if scratch:
            cmd += ["-af", SCRATCH]
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", FORMAT["abr"], "-ar", str(ar), "-ac", str(FORMAT["ac"]),
            "-shortest", "-timecode", timecode, path]
    subprocess.run(cmd, check=True)


# -----------------------------------------------------------------------------
# Music: a drum loop with sung (spoken) lyric lines on it.

LYRICS = ["city lights are calling me", "I keep driving through the night",
          "never looking back again", "turn it up and feel alive"]


def make_song(path, lyrics_path, work, bpm=120.0, seconds=16.0, start=0.25):
    """Kick on every beat (accented downbeat), off-beat hi-hats, a lyric line every two bars
    from bar 2. -> {"tempo", "beats", "lines": [{"text", "start"}]}."""
    import math
    import random
    rate, rng = FORMAT["ar"], random.Random(1)
    n = int(seconds * rate)
    x = [0.03 * math.sin(2 * math.pi * 220 * k / rate) for k in range(n)]
    beat, beats = 60.0 / bpm, []
    k = 0
    while start + k * beat < seconds - 0.3:
        b = start + k * beat
        beats.append(round(b, 3))
        i, m = int(b * rate), int(0.12 * rate)
        amp = 0.9 if k % 4 == 0 else 0.55
        for j in range(min(m, n - i)):
            x[i + j] += amp * math.exp(-j / rate * 30) * math.sin(2 * math.pi * 60 * j / rate)
        h = int((b + beat / 2) * rate)
        for j in range(min(int(0.02 * rate), max(0, n - h))):
            x[h + j] += 0.15 * (rng.random() * 2 - 1) * math.exp(-j / rate * 150)
        k += 1
    lines = []
    for n_line, text in enumerate(LYRICS):
        at = start + (4 + 8 * n_line) * beat + 0.05          # just after a bar start
        tmp = os.path.join(work, "lyric{:d}.wav".format(n_line))
        _speak(MAYA, text, tmp)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-ar", str(rate), "-ac", "1",
                        "-c:a", "pcm_s16le", tmp + ".r.wav"], check=True)
        with wave.open(tmp + ".r.wav") as w:
            raw = w.readframes(w.getnframes())
        i0 = int(at * rate)
        for j in range(0, len(raw) // 2):
            if i0 + j < n:
                x[i0 + j] += 0.8 * int.from_bytes(raw[2 * j:2 * j + 2], "little", signed=True) / 32768
        lines.append({"text": text, "start": round(at, 3)})
        os.remove(tmp)
        os.remove(tmp + ".r.wav")
    peak = max(abs(v) for v in x) or 1
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(int(v / peak * 0.9 * 32767).to_bytes(2, "little", signed=True) for v in x))
    with open(lyrics_path, "w") as fh:
        fh.write("[Verse]\n" + "\n".join(LYRICS) + "\n")
    return {"tempo": bpm, "beats": beats, "lines": lines}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("output", nargs="?", default="assembly_samples")
    parser.add_argument("--tiny", action="store_true",
                        help="small and short: 124x70 (16:9) video, 1 s clips (dialogue takes as long "
                             "as their speech), 16 kHz mono sound")
    parser.add_argument("--no-music", action="store_true", help="skip the music video shoot")
    args = parser.parse_args()
    if args.tiny:
        FORMAT.update(w=124, h=70, ar=16000, ac=1, abr="32k", clip=1.0)
    out = os.path.abspath(args.output)
    media = os.path.join(out, "media")
    work = os.path.join(out, ".work")
    for d in (media, work):
        os.makedirs(d, exist_ok=True)
    write_pdf(os.path.join(out, "script.pdf"), script_pages())

    truth = {"lines": LINES, "takes": {}, "sync": {}, "format": dict(FORMAT)}
    recorders = {r[0]: r for r in RECORDERS}
    for n, (name, picture, parts) in enumerate(TAKES):
        wav = os.path.join(work, name + ".wav")
        timeline, duration = make_speech(parts, wav, work)
        camera = wav
        if name in CLAP_TAKES:
            # The camera hears the speech faintly under wind, the clap loud; the recorder
            # hears both clearly.
            camera = os.path.join(work, name + ".camera.wav")
            _scaled_copy(wav, camera, 0.15)
            add_clap(camera)
            add_clap(wav)
        make_video(os.path.join(media, name), picture, duration, camera,
                   timecode="01:{:02d}:00:00".format(n + 1), scratch=name in recorders,
                   wind=name in CLAP_TAKES)
        truth["takes"][name] = timeline
        if name in recorders:
            _take, rec, lead, with_tc = recorders[name]
            make_recorder(wav, os.path.join(media, rec), lead,
                          3600 + 60 * (n + 1) if with_tc else None)
            method = "timecode" if with_tc else "clap" if name in CLAP_TAKES else "waveform"
            truth["sync"][name] = {"audio": rec, "offset": lead, "method": method}
    name, parts = VOICE_OVER
    vo = os.path.join(work, name)
    timeline, _ = make_speech(parts, vo, work)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", vo, "-ar", str(FORMAT["ar"]), "-ac", "1",
                    "-c:a", "pcm_s16le", os.path.join(media, name)], check=True)
    truth["takes"][name] = timeline
    name, picture, duration = BROLL
    make_video(os.path.join(media, name), picture, FORMAT["clip"] or duration, timecode="01:10:00:00")
    truth["takes"][name] = []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", pictures()["wide2"],
                    "-frames:v", "1", os.path.join(media, "STILL_diner_sign.jpg")], check=True)

    if not args.no_music:
        music = os.path.join(out, "music")
        os.makedirs(music, exist_ok=True)
        truth["music"] = make_song(os.path.join(music, "song.wav"), os.path.join(out, "lyrics.txt"), work)
        for k, look in enumerate(("red", "blue", "grey", "wide1", "wide2")):
            make_video(os.path.join(music, "mv_{:02d}_{:s}.mp4".format(k + 1, look)), look, FORMAT["clip"] or 6.0,
                       timecode="02:{:02d}:00:00".format(k))
    for f in os.listdir(work):
        os.remove(os.path.join(work, f))
    os.rmdir(work)
    with open(os.path.join(out, "truth.json"), "w") as fh:
        json.dump(truth, fh, indent=1)
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(out) for f in fs)
    print("wrote {:s} ({:.1f} MB)".format(out, size / 1e6))

if __name__ == "__main__":
    main()
