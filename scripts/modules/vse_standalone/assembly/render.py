# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Render an editing script straight to a video file with ffmpeg (no editor needed): a quick
look at the assembly, or the export when no fine-tuning was done in the editor.

Every event is cut on input (``-ss``/``-t``, fast), scaled and padded to one frame size and
rate, and joined with the ``concat`` filter. Audio-only lines play over their cover picture,
or black.
"""

import subprocess

from . import editing_script


def command(doc, output, width=None, height=None, fps=None, crf=20, preset="veryfast"):
    events = editing_script.timeline(doc)
    if not events:
        raise ValueError("The editing script has no footage to render")
    videos = [m for m in doc["media"].values() if m["kind"] == "video" and m.get("width")]
    width = width or (videos[0]["width"] if videos else 1920)
    height = height or (videos[0]["height"] if videos else 1080)
    fps = fps or doc.get("fps") or 24
    args = ["ffmpeg", "-v", "error", "-nostdin", "-y"]
    filters, labels, n_in = [], [], 0
    norm = ("scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            "setsar=1,fps={f},format=yuv420p").format(w=width, h=height, f=fps)
    for k, ev in enumerate(events):
        dur = ev["out"] - ev["in"]
        pic = ev["picture"]
        if pic and doc["media"][pic["clip"]]["kind"] == "image":
            args += ["-loop", "1", "-t", "{:.3f}".format(dur), "-i", pic["path"]]
        elif pic:
            args += ["-ss", "{:.3f}".format(pic["in"]), "-t", "{:.3f}".format(dur), "-i", pic["path"]]
        else:
            args += ["-f", "lavfi", "-t", "{:.3f}".format(dur), "-i",
                     "color=black:s={:d}x{:d}:r={}".format(width, height, fps)]
        v_in = n_in
        n_in += 1
        if ev["sound"]:
            if pic and pic["path"] == ev["sound_path"] and abs(pic["in"] - ev["sound_in"]) < 1e-3:
                a_in = v_in
            else:
                late = max(0.0, -ev["sound_in"])     # the recorder started after this point
                args += ["-ss", "{:.4f}".format(max(0.0, ev["sound_in"])), "-t",
                         "{:.3f}".format(dur), "-i", ev["sound_path"]]
                a_in = n_in
                n_in += 1
            delay = ",adelay={:d}:all=1".format(int(late * 1000)) if a_in != v_in and late else ""
            filters.append("[{:d}:a:0]aresample=48000,aformat=channel_layouts=stereo{:s},"
                           "apad,atrim=0:{:.3f}[a{:d}]".format(a_in, delay, dur, k))
        else:
            args += ["-f", "lavfi", "-t", "{:.3f}".format(dur), "-i",
                     "anullsrc=channel_layout=stereo:sample_rate=48000"]
            filters.append("[{:d}:a]atrim=0:{:.3f}[a{:d}]".format(n_in, dur, k))
            n_in += 1
        # A clip shorter than its slot holds its last frame, so picture and sound stay in step.
        filters.append("[{:d}:v:0]{:s},tpad=stop_mode=clone:stop_duration={:.3f},trim=0:{:.3f},"
                       "setpts=PTS-STARTPTS[v{:d}]".format(v_in, norm, dur, dur, k))
        labels.append("[v{0:d}][a{0:d}]".format(k))
    filters.append("{:s}concat=n={:d}:v=1:a=1[v][a]".format("".join(labels), len(events)))
    args += ["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", output]
    return args


def render(doc, output, **kwargs):
    """-> {"filepath", "duration", "events"}. Blocks until ffmpeg finishes."""
    args = command(doc, output, **kwargs)
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + proc.stderr.strip()[-2000:])
    events = editing_script.timeline(doc)
    return {"filepath": output, "events": len(events),
            "duration": round(sum(e["out"] - e["in"] for e in events), 3)}
