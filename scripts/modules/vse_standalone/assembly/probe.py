# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
What is in a media file, from ffprobe: kind, length, frame rate, size, start timecode and audio
tracks. Standard library only.
"""

import fractions
import json
import os
import subprocess

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".webm", ".mts", ".m2ts", ".mpg",
             ".mpeg", ".3gp", ".dv", ".r3d", ".braw"}
AUDIO_EXT = {".wav", ".bwf", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus",
             ".wma"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif", ".webp", ".bmp", ".dng",
             ".exr"}


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return None


def _rate(text):
    try:
        value = fractions.Fraction(text)
    except (ValueError, ZeroDivisionError, TypeError):
        return None
    return round(float(value), 3) if value > 0 else None


def timecode_to_seconds(tc, fps):
    """"01:00:10:12" (or ";" drop-frame separator) -> seconds. Drop-frame is approximated."""
    if not tc or not fps:
        return None
    parts = tc.replace(";", ":").split(":")
    if len(parts) != 4:
        return None
    h, m, s, f = (int(p) for p in parts)
    return h * 3600 + m * 60 + s + f / round(fps)


def probe(path):
    """-> dict describing the file. Raises RuntimeError if ffprobe can't read it."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("ffprobe could not read {:s}: {:s}".format(path, proc.stderr.strip()))
    data = json.loads(proc.stdout or "{}")
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    kind = kind_of(path)
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if kind is None:
        kind = "video" if video else "audio" if audio else "other"
    if kind == "video" and video is None:
        kind = "audio" if audio else "other"

    info = {
        "path": os.path.abspath(path),
        "name": os.path.basename(path),
        "kind": kind,
        "duration": float(fmt["duration"]) if fmt.get("duration") not in (None, "N/A") else None,
        "bytes": int(fmt.get("size") or os.path.getsize(path)),
        "format": fmt.get("format_name"),
        "creation_time": (fmt.get("tags") or {}).get("creation_time"),
    }
    if video is not None and kind in ("video", "image"):
        fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
        info.update(width=video.get("width"), height=video.get("height"),
                    codec=video.get("codec_name"))
        rotation = None
        for side in video.get("side_data_list") or ():
            if "rotation" in side:
                rotation = side["rotation"]
        if rotation is not None:
            info["rotation"] = rotation
        if kind == "video":
            info["fps"] = fps
            if info["duration"] is None and video.get("duration"):
                info["duration"] = float(video["duration"])
            if video.get("nb_frames"):
                info["frames"] = int(video["nb_frames"])
            elif info["duration"] and fps:
                info["frames"] = int(round(info["duration"] * fps))
    timecode = None
    for source in [fmt] + streams:
        timecode = (source.get("tags") or {}).get("timecode")
        if timecode:
            break
    info["timecode"] = timecode
    if timecode and info.get("fps"):
        info["timecode_seconds"] = timecode_to_seconds(timecode, info["fps"])
    # Sound recorders (Broadcast WAV) store their start as samples since midnight.
    tags = fmt.get("tags") or {}
    ref = tags.get("time_reference")
    if ref and audio and str(ref).isdigit() and int(ref) > 0 and audio[0].get("sample_rate"):
        info["time_reference_seconds"] = int(ref) / int(audio[0]["sample_rate"])
    if tags.get("encoded_by") or tags.get("originator"):
        info["recorder"] = tags.get("originator") or tags.get("encoded_by")
    info["audio_tracks"] = [{
        "index": s.get("index"),
        "codec": s.get("codec_name"),
        "channels": s.get("channels"),
        "sample_rate": int(s["sample_rate"]) if s.get("sample_rate") else None,
        "language": (s.get("tags") or {}).get("language"),
        "title": (s.get("tags") or {}).get("title"),
    } for s in audio]
    return info


def scan(folder, recursive=True):
    """Probe every media file under a folder. -> list of probe dicts (unreadable files skipped
    with an "error" entry)."""
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.startswith(".") or kind_of(name) is None:
                continue
            path = os.path.join(root, name)
            try:
                found.append(probe(path))
            except RuntimeError as ex:
                found.append({"path": os.path.abspath(path), "name": name, "kind": kind_of(name),
                              "error": str(ex)})
        if not recursive:
            break
    return found
