# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
"New style" lyric videos: a vertical frame with a 3:4 picture between black bars, a new clip
for every lyric line, and the lyrics building up word by word in a justified block, in time
with the song.

Tools (all available to agents through the control server, and to scripts through
``vse_standalone.api.call``):

``add_picture_clips``  Clips cut one after another, each filling the picture area.
``add_lyric_words``    On-screen lyrics from a ``.lyrics.json`` timing file.
``make_lyric_video``   The whole video in one call: project, audio, clips, lyrics, export.
``add_sync_ticks``     A tick on every sung word, for checking the timing by ear.

The timing file comes from ``tools/vse_lyrics/align_lyrics.py``. The full workflow is in
``doc/vse/lyric_video_workflow.md``.
"""

import json
import os
import re
import typing

import bpy

from . import api
from .api import tool

# Arial Narrow ships with macOS and Windows.
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Narrow.ttf",
    "/Library/Fonts/Arial Narrow.ttf",
    "C:/Windows/Fonts/ARIALN.TTF",
    "/usr/share/fonts/truetype/msttcorefonts/Arial_Narrow.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Regular.ttf",
)

# The house "new style". Values measured from the four reference videos (576 x 1024 TikTok
# examples), converted to a 1080-wide frame; they scale with the frame width.
STYLE = {
    "picture_aspect": 0.75,       # picture width / height (3:4); black bars fill the rest
    "font_size": 96.0,
    "stretch_x": 0.955,           # the reference's tight letter spacing, as a slight squeeze
    "stretch_y": 1.07,            # the "slight vertical stretch"
    "color": [0.98, 0.98, 0.98, 0.9],   # almost white, 90 % opacity
    "max_chars": 14,              # a row holds at most this many characters (spaces included)
    "block_width": 0.48,          # of the frame width (0.40-0.57 in the references)
    "block_top": 0.16,            # top of the text block, from the top of the picture (0..1)
    "block_align_y": "TOP",       # "CENTER": center the block in the picture, as the references do
    "line_pitch": 1.27,           # row distance, in font sizes (122 px)
    "weight": 0.02,               # thickens the strokes (an edge in the text's own colour)
    # A faint soft shadow, as in the references. No outline.
    "shadow_blur": 0.3,
    "shadow_color": [0.0, 0.0, 0.0, 0.6],
    "outline_width": 0.0,
    "outline_color": [0.0, 0.0, 0.0, 0.5],
    "lowercase": True,
    "lead_frames": 1,             # words appear this many frames before they are sung
}


def _font_path(font):
    for path in ((font,) if font else ()) + FONT_CANDIDATES:
        if path and os.path.exists(path):
            return path
    if font:
        raise api.APIError("Font not found: {:s}".format(font))
    return None  # None of the usual files: the editor's built-in font.


def _load_font(path):
    for font in bpy.data.fonts:
        if bpy.path.abspath(font.filepath) == path:
            return font
    return bpy.data.fonts.load(path)


def display_word(text, lowercase=True):
    """A lyric-sheet word as shown on screen: no punctuation except apostrophes."""
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^\w'*]", "", text, flags=re.UNICODE)
    return text.lower() if lowercase else text


def wrap_rows(texts, max_chars):
    """Break words into rows the way the references do: a row holds as many words as fit in
    `max_chars` characters, counting the spaces between them.

    :return: A list of rows, each a list of word indices.
    """
    rows, row, used = [], [], 0
    for index, text in enumerate(texts):
        needed = len(text) if not row else used + 1 + len(text)
        if row and needed > max_chars:
            rows.append(row)
            row, needed = [], len(text)
        row.append(index)
        used = needed
    if row:
        rows.append(row)
    return rows


def layout_block(widths, rows, block_width):
    """Justify every row to the block width.

    :arg widths: Width of every word, in pixels.
    :arg rows: Rows of word indices (see `wrap_rows`).
    :return: ``[(row, x), ...]`` for every word, x from the left edge of the block.

    A row with one word sits on the left; rows with more are spread over the full width,
    first word on the left edge and last word on the right edge, also on the last row (as in
    the reference videos). A row wider than the block keeps its natural size.
    """
    placed = [None] * len(widths)
    for r, row in enumerate(rows):
        total = sum(widths[i] for i in row)
        gap = max(0.0, (block_width - total) / (len(row) - 1)) if len(row) > 1 else 0.0
        x = 0.0
        for i in row:
            placed[i] = (r, x)
            x += widths[i] + gap
    return placed


def _frame_of(seconds, fps):
    return 1 + int(round(seconds * fps))


def _fps(scene):
    return scene.render.fps / scene.render.fps_base


def _picture_rect(scene, aspect):
    """(x, y, width, height) of the picture area in pixels, y from the bottom."""
    width, height = scene.render.resolution_x, scene.render.resolution_y
    pic_h = min(height, width / aspect)
    pic_w = min(width, pic_h * aspect)
    return (width - pic_w) / 2, (height - pic_h) / 2, pic_w, pic_h


@tool()
def add_picture_clips(clips: typing.List[dict], picture_aspect: float = 0.75,
                      channel: int = 1) -> list:
    """Place video clips one after another, each scaled and cropped to fill the picture area:
    a centered rectangle of the given aspect ratio, with black bars filling the rest of the frame.

    :arg clips: List of {"filepath", "start", "end"} in seconds; optional "offset" (seconds into the clip).
    :arg picture_aspect: Picture width / height. 0.75 is the 3:4 "new style" picture; use the frame's own aspect for full screen.
    """
    scene = api._scene()
    fps = _fps(scene)
    _px, _py, pic_w, pic_h = _picture_rect(scene, picture_aspect)
    added = []
    for clip in clips:
        path = api._abspath(clip["filepath"])
        start, end = _frame_of(clip["start"], fps), _frame_of(clip["end"], fps)
        offset = int(round(clip.get("offset", 0.0) * fps))
        strip = scene.sequence_editor.strips.new_movie(
            name=os.path.basename(path), filepath=path, channel=channel,
            frame_start=start - offset, fit_method='ORIGINAL')
        if strip.frame_duration - offset < end - start:
            available = (strip.frame_duration - offset) / fps
            scene.sequence_editor.strips.remove(strip)
            raise api.APIError("{:s} is too short: {:.2f}s needed, {:.2f}s available".format(
                os.path.basename(path), (end - start) / fps, available))
        strip.frame_offset_start = offset
        strip.frame_final_duration = end - start
        src_w, src_h = strip.elements[0].orig_width, strip.elements[0].orig_height
        scale = max(pic_w / src_w, pic_h / src_h)
        strip.transform.scale_x = strip.transform.scale_y = scale
        strip.transform.offset_x = strip.transform.offset_y = 0
        crop_x = max(0.0, (src_w * scale - pic_w) / 2 / scale)
        crop_y = max(0.0, (src_h * scale - pic_h) / 2 / scale)
        strip.crop.min_x = strip.crop.max_x = int(round(crop_x))
        strip.crop.min_y = strip.crop.max_y = int(round(crop_y))
        added.append(api._strip_info(strip))
    return added


def _read_lyrics(lyrics):
    if isinstance(lyrics, dict):
        return lyrics
    with open(api._abspath(lyrics), encoding="utf-8") as fh:
        return json.load(fh)


@tool()
def add_lyric_words(lyrics: str, channel: int = 3, picture_aspect: float = 0.75,
                    font: typing.Optional[str] = None,
                    style: typing.Optional[dict] = None,
                    end: typing.Optional[float] = None) -> dict:
    """Add timed lyrics as on-screen text: each lyric line builds up word by word in a
    justified block over the picture and is replaced when the next line starts.

    :arg lyrics: Path of a .lyrics.json file made by tools/vse_lyrics/align_lyrics.py.
    :arg channel: First channel for the words; a line of N words uses N channels.
    :arg font: Path of a font file. Default: Arial Narrow.
    :arg style: Overrides for lyric_video.STYLE, e.g. {"font_size": 80, "block_top": 0.3}.
    :arg end: Time in seconds when the last line disappears. Default: the audio's duration.
    """
    import blf
    data = _read_lyrics(lyrics)
    st = dict(STYLE, **(style or {}))
    scene = api._scene()
    fps = _fps(scene)
    width, height = scene.render.resolution_x, scene.render.resolution_y
    k = width / 1080.0
    size = st["font_size"] * k
    font_path = _font_path(font)
    vfont = _load_font(font_path) if font_path else None
    font_id = blf.load(font_path) if font_path else 0
    blf.size(font_id, size)

    px, py, pic_w, pic_h = _picture_rect(scene, picture_aspect)
    block_w = st["block_width"] * width
    block_x = (width - block_w) / 2
    pitch = st["line_pitch"] * size
    stretch_x, stretch = st["stretch_x"], st["stretch_y"]
    last_end = end if end is not None else data.get("duration", data["words"][-1]["end"])

    strips = scene.sequence_editor.strips
    lines = data["lines"]
    count, rows_max, channels = 0, 0, 0
    for n, line in enumerate(lines):
        words = [data["words"][i] for i in line["words"]]
        texts = [display_word(w["text"], st["lowercase"]) for w in words]
        keep = [i for i, t in enumerate(texts) if t]
        words, texts = [words[i] for i in keep], [texts[i] for i in keep]
        if not words:
            continue
        widths = [blf.dimensions(font_id, t)[0] * stretch_x for t in texts]
        rows = wrap_rows(texts, st["max_chars"])
        placed = layout_block(widths, rows, block_w)
        if st["block_align_y"] == "CENTER":
            block_top = py + pic_h / 2 + (len(rows) * pitch) / 2
        else:
            block_top = py + pic_h - st["block_top"] * pic_h      # y of the top of the first row
        clear = lines[n + 1]["start"] if n + 1 < len(lines) else last_end
        # The line leaves on the frame the next one's first word arrives (no overlap).
        clear_frame = _frame_of(clear, fps) - (st["lead_frames"] if n + 1 < len(lines) else 0)
        for i, (word, text, (row, x)) in enumerate(zip(words, texts, placed)):
            start = max(1, _frame_of(word["start"], fps) - st["lead_frames"])
            if i == 0 and n > 0:
                start = max(start, _frame_of(line["start"], fps) - st["lead_frames"])
            if start >= clear_frame:
                start = clear_frame - 1
            strip = strips.new_effect(name="lyric {:02d}.{:02d} {:s}".format(n + 1, i + 1, text),
                                      type='TEXT', channel=channel + i,
                                      frame_start=start, length=clear_frame - start)
            strip.text = text
            if vfont is not None:
                strip.font = vfont
            strip.font_size = st["font_size"]
            strip.color = st["color"]
            strip.use_shadow = st["shadow_blur"] > 0
            strip.shadow_color = st["shadow_color"]
            strip.shadow_offset = 0.0
            strip.shadow_blur = st["shadow_blur"]
            if st["outline_width"] > 0:
                strip.use_outline = True
                strip.outline_width = st["outline_width"]
                strip.outline_color = st["outline_color"]
            elif st["weight"] > 0:
                strip.use_outline = True
                strip.outline_width = st["weight"]
                strip.outline_color = st["color"]
            strip.wrap_width = 0.0
            strip.alignment_x = 'LEFT'
            strip.anchor_x = 'LEFT'
            strip.anchor_y = 'TOP'
            y = block_top - row * pitch
            # The stretch scales the strip around the frame center; place it so that the
            # stretched text lands on (x, y).
            strip.location = (
                0.5 + ((block_x + x) / width - 0.5) / stretch_x,
                0.5 + (y / height - 0.5) / stretch,
            )
            strip.transform.scale_x = stretch_x
            strip.transform.scale_y = stretch
            strip.blend_type = 'ALPHA_OVER'
            count += 1
            rows_max = max(rows_max, row + 1)
            channels = max(channels, i + 1)
    return {"words": count, "lines": len(lines), "rows_max": rows_max,
            "channels": [channel, channel + channels - 1], "font": font_path or "built-in"}


@tool()
def add_sync_ticks(lyrics: str, channel: int = 40, volume: float = 0.9) -> dict:
    """Add a short tick sound at the moment every lyric word is sung, for checking the lyric
    timing by ear: export the result as a review copy, play it, and listen for ticks that do
    not land on their word. Delete the tick strips (named "tick ...") before the final export.

    :arg lyrics: Path of the .lyrics.json timing file.
    :arg channel: Channel for the ticks (they never overlap, so one is enough).
    """
    import math
    import struct
    import tempfile
    import wave
    data = _read_lyrics(lyrics)
    scene = api._scene()
    fps = _fps(scene)
    path = os.path.join(tempfile.gettempdir(), "vse_sync_tick.wav")
    rate, length = 48000, 0.03
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        count = int(rate * length)
        wav.writeframes(b"".join(
            struct.pack("<h", int(30000 * math.sin(2 * math.pi * 2000 * n / rate) * (1 - n / count)))
            for n in range(count)))
    strips = scene.sequence_editor.strips
    frames = sorted({_frame_of(w["start"], fps) for w in data["words"]})
    for frame in frames:
        strip = strips.new_sound(name="tick {:04d}".format(frame), filepath=path, channel=channel,
                                 frame_start=frame)
        strip.volume = volume
    return {"ticks": len(frames), "channel": channel}


def plan_cuts(lyrics, duration, max_clip=3.2, min_clip=0.9):
    """Cut times for the picture: a new clip at every lyric line; long lines get an extra cut
    at a word start near their middle.

    :return: ``[(start, end), ...]`` in seconds, covering 0..duration.
    """
    data = _read_lyrics(lyrics)
    points = [0.0]
    lines = data["lines"]
    for n, line in enumerate(lines):
        start = line["start"] if n else 0.0
        stop = lines[n + 1]["start"] if n + 1 < len(lines) else duration
        if n and start - points[-1] >= min_clip:
            points.append(start)
        if stop - points[-1] > max_clip:
            middle = (points[-1] + stop) / 2
            starts = [data["words"][i]["start"] for i in line["words"][1:]]
            starts = [t for t in starts if t - points[-1] >= min_clip and stop - t >= min_clip]
            if starts:
                points.append(min(starts, key=lambda t: abs(t - middle)))
    points = [p for p in points if duration - p >= min_clip or p == 0.0]
    return list(zip(points, points[1:] + [duration]))


@tool()
def make_lyric_video(audio: str, lyrics: str, clips: typing.List[str],
                     output: typing.Optional[str] = None,
                     width: int = 1080, height: int = 1920, fps: float = 30.0,
                     picture_aspect: float = 0.75,
                     font: typing.Optional[str] = None,
                     style: typing.Optional[dict] = None,
                     project: typing.Optional[str] = None) -> dict:
    """Build a whole "new style" lyric video in the current (empty) project: the audio, a new
    clip at every lyric line inside a 3:4 picture with black bars, and the lyrics appearing
    word by word. Optionally save the project and export the MP4.

    :arg audio: Audio file; the video is as long as the audio.
    :arg lyrics: .lyrics.json timing file for this audio (tools/vse_lyrics/align_lyrics.py).
    :arg clips: Video files to use, in order. Clips too short for their slot are skipped; give a few more than there are lyric lines.
    :arg output: MP4 file to export (H.264 + AAC). Leave out to only build the timeline.
    :arg project: .blend file to save the finished timeline to.
    """
    scene = api._scene()
    api.set_project(fps=fps, width=width, height=height)
    sound = api.add_sound(audio, channel=2, frame_start=1)
    data = _read_lyrics(lyrics)
    real_fps = _fps(scene)
    frames = int(round(data.get("duration", (sound["frame_end"] - 1) / real_fps) * real_fps))
    frames = max(frames, sound["frame_end"] - 1)
    duration = frames / real_fps
    api.set_project(frame_start=1, frame_end=frames)

    cuts = plan_cuts(data, duration)
    pool = list(clips)
    used, too_short = [], {}
    word_starts = [w["start"] for w in data["words"]]
    queue = list(cuts)
    while queue:
        start, end = queue.pop(0)
        needed = end - start
        # Not two clips in a row from the same folder (subject) when there is a choice.
        previous = os.path.dirname(used[-1]["filepath"]) if used else None
        ordered = ([p for p in pool if os.path.basename(os.path.dirname(p)) != os.path.basename(previous or "")] +
                   [p for p in pool if os.path.basename(os.path.dirname(p)) == os.path.basename(previous or "")])
        for path in ordered:
            # A clip too short for this slot stays in the pool for a shorter one.
            if too_short.get(path, needed + 1.0) <= needed:
                continue
            try:
                add_picture_clips([{"filepath": path, "start": start, "end": end}],
                                  picture_aspect=picture_aspect, channel=1)
            except api.APIError:
                too_short[path] = needed
                continue
            pool.remove(path)
            used.append({"filepath": path, "start": round(start, 3), "end": round(end, 3)})
            break
        else:
            # No clip is long enough: cut the slot in two, on a word when one is near the middle.
            if needed < 1.0:
                raise api.APIError("No clip left that is long enough for {:.2f}s..{:.2f}s "
                                   "({:d} clips offered)".format(start, end, len(clips)))
            middle = (start + end) / 2
            near = [t for t in word_starts if abs(t - middle) <= needed / 4]
            split = min(near, key=lambda t: abs(t - middle)) if near else middle
            queue[:0] = [(start, split), (split, end)]
    skipped = sorted(too_short)
    words = add_lyric_words(data, channel=3, picture_aspect=picture_aspect, font=font,
                            style=style, end=duration)
    result = {"frames": frames, "duration": duration, "cuts": used, "skipped": skipped,
              "unused": pool, "lyrics": words}
    if project:
        project = os.path.abspath(bpy.path.abspath(project))
        os.makedirs(os.path.dirname(project), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=project)
        result["project"] = project
    if output:
        result["render"] = api.render_video(output, format="H264_MP4", quality="HIGH")
    return result
