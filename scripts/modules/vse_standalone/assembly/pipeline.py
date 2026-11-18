# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
From a folder of footage (and a screenplay, if there is one) to an editing script.

    probe every file (ffprobe)
    -> read the screenplay (pdftotext + rules)                 [skipped without a script]
    -> transcribe each clip's audio (faster-whisper, cached)
    -> match takes to script lines (fuzzy sequence alignment)
    -> shots, thumbnails, embeddings, groups (ffmpeg + MobileCLIP2-S0 or colour signature)
    -> the editing script (JSON)

Without a screenplay a rough one is made from what was said, scene by look-alike group, and
a language model is asked once (if one is configured) to suggest the scene order.
"""

import os
import re
import time

from . import editing_script, match, probe, screenplay, transcribe, visual


def _log(log, *parts):
    if log:
        log(" ".join(str(p) for p in parts))


def assemble(media, script=None, *, model=None, image_backend=None, cache_dir=None,
             visual_grouping=True, ask_model=True, log=print):
    """-> editing script dict. media: a folder or a list of files. script: .pdf/.fountain/.txt."""
    started = time.time()
    if isinstance(media, str):
        folder = os.path.abspath(media)
        clips = probe.scan(folder)
    else:
        folder = os.path.dirname(os.path.abspath(media[0])) if media else None
        clips = [probe.probe(p) for p in media]
    cache_dir = cache_dir or os.path.join(folder or os.getcwd(), ".assembly_cache")
    model = model or transcribe.DEFAULT_MODEL
    bad = [c for c in clips if c.get("error")]
    clips = [c for c in clips if not c.get("error")]
    _log(log, "media: {:d} files ({:d} unreadable)".format(len(clips), len(bad)))

    parsed = screenplay.parse(script) if script else None
    if parsed:
        n_lines = len(screenplay.dialogue_lines(parsed))
        _log(log, "script: {:d} scenes, {:d} dialogue lines, characters {:s}".format(
            len(parsed["scenes"]), n_lines, ", ".join(c["name"] for c in parsed["characters"])))

    # No initial prompt: showing Whisper the character names made it invent words in the
    # silence after a take (seen with base.en). Names are matched fuzzily instead.
    prompt = None
    transcripts = {}
    for clip in clips:
        if clip["kind"] in ("video", "audio") and clip.get("audio_tracks"):
            t0 = time.time()
            transcripts[clip["path"]] = transcribe.transcribe(
                clip["path"], model, prompt=prompt, cache_dir=os.path.join(cache_dir, "words"))
            _log(log, "heard {:s} ({:.1f}s): {:s}".format(
                clip["name"], time.time() - t0, transcripts[clip["path"]]["text"][:90]))

    visuals, groups = {}, []
    if visual_grouping:
        visuals, backend = visual.describe_clips(clips, cache_dir, backend=image_backend)
        emb = {p: v["embedding"] for p, v in visuals.items() if "embedding" in v}
        groups = visual.group(emb, visual.THRESHOLD[backend])
        _log(log, "looks ({:s}): {:d} groups".format(backend, len(groups)))
        for v in visuals.values():
            v.pop("embedding", None)

    if parsed is None:
        parsed = rough_script(clips, transcripts, groups)
        _log(log, "no script: rough script with {:d} scenes from speech and looks".format(
            len(parsed["scenes"])))

    index = match.ScriptIndex(screenplay.dialogue_lines(parsed))
    matches = {p: match.match_take(t["words"], index) for p, t in transcripts.items()}
    doc = editing_script.build(parsed, clips, transcripts, matches, groups, visuals)
    doc["sources"]["media"] = folder
    doc["settings"] = {"whisper_model": str(model), "image_backend": visual_grouping and backend or None}
    if bad:
        doc["unreadable"] = [{"path": c["path"], "error": c["error"]} for c in bad]
    if parsed.get("rough"):
        doc["rough"] = True
        if ask_model:
            from . import llm
            order = llm.suggest_scene_order(doc, log=log)
            if order:
                pos = {sid: k for k, sid in enumerate(order)}
                doc["scenes"].sort(key=lambda s: pos.get(s["id"], len(pos)))
                doc["order_suggested_by"] = "model"
    s = editing_script.summary(doc)
    _log(log, "editing script: {:d}/{:d} lines have footage, {:d} events, {:.1f}s, {:d} unplaced "
              "({:.0f}s in all)".format(s["lines_with_footage"], s["dialogue_lines"], s["events"],
                                       s["duration"], len(s["unplaced"]), time.time() - started))
    return doc


# -----------------------------------------------------------------------------
# No screenplay: a rough one from speech and looks.

def _sentences(words, gap=0.8):
    out, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > gap):
            out.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.?!]$", w["text"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [s for s in out if len(s) >= 2]


def rough_script(clips, transcripts, groups):
    """Scenes from look-alike groups (in timecode order); lines from the most complete take
    of each group. Speakers are unknown ("SPEAKER")."""
    by_path = {c["path"]: c for c in clips}
    grouped = [g for g in groups if any(p in transcripts for p in g)]
    lonely = [p for p in transcripts if not any(p in g for g in grouped)]
    grouped += [[p] for p in lonely]

    def when(path):
        c = by_path[path]
        return (c.get("timecode_seconds") or 0, c.get("creation_time") or "", c["name"])

    grouped.sort(key=lambda g: min(when(p) for p in g))
    scenes = []
    for n, members in enumerate(grouped, 1):
        sid = "S{:d}".format(n)
        speaking = [p for p in members if transcripts.get(p, {}).get("words")]
        elements = []
        if speaking:
            ref = max(speaking, key=lambda p: len(transcripts[p]["words"]))
            for sentence in _sentences(transcripts[ref]["words"]):
                text = " ".join(w["text"] for w in sentence)
                if editing_script.heard_slate(sentence) and len(sentence) <= 8:
                    continue
                elements.append({"id": "{:s}-{:d}".format(sid, len(elements) + 1),
                                 "type": "dialogue", "character": "SPEAKER", "extensions": [],
                                 "parentheticals": [], "text": text})
        else:
            names = ", ".join(by_path[p]["name"] for p in members)
            elements.append({"id": sid + "-1", "type": "action", "text": "Picture: " + names})
        scenes.append({"id": sid, "number": None, "heading": "SCENE {:d}".format(n),
                       "int_ext": None, "location": None, "time": None, "elements": elements})
    return {"source": None, "title": None, "title_page": [], "characters": [], "scenes": scenes,
            "rough": True}
