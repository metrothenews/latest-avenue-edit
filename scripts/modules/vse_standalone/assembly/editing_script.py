# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The editing script: a screenplay whose lines carry their footage.

It is one JSON document (``*.edit.json``). Every dialogue line holds the take it plays from,
in/out points, and the ranked alternatives, so a person can swap takes line by line. The
document can be shown as screenplay text (:func:`to_text`), edited as text, and read back
(:func:`apply_text`): lines that survive keep their footage wherever they move, shortened
lines are re-trimmed to the words still in them, and new lines are looked up in what was said
on set. The editor lays the result out on the timeline (``assembly_tools.lay_out``) and
ffmpeg can render it directly (:mod:`.render`).

Format (``version`` 1)::

    {
      "format": "latest-avenue-editing-script", "version": 1,
      "title", "fps", "sources": {"script", "media"},
      "media":  {clip id: {path, name, kind, duration, fps, timecode, audio_tracks, group,
                           shots, slate, words, supervisor}},
      "groups": [{"id", "clips", "scenes"}],
      "scenes": [{"id", "heading", "int_ext", "location", "time", "group", "elements": [
          {"id", "type": "dialogue", "character", "extensions", "parentheticals", "text",
           "script_text",                      # the line as written (text is as edited)
           "take": 0,                           # index into takes, or null (no footage)
           "cut":  {"clip", "in", "out"},       # what plays: seconds in the clip
           "cover": {"clip", "in", "out"} | null,   # picture over audio-only takes (V.O.)
           "takes": [{"clip", "in", "out", "speech_in", "speech_out", "words",
                      "heard", "score", "coverage", "accuracy", "confidence",
                      "supervisor"}]},     # best first
          {"id", "type": "action", "text", "cut" | null, "takes": [...cover options]},
          {"id", "type": "transition", "text"}]}],
      "unplaced": [clip ids not used anywhere],
      "supervisor_notes": {"status": "reserved", ...}   # see doc/vse/script_supervisor_notes.md
    }

Standard library only (rapidfuzz used when present), so the editor's own Python can read and
change it.
"""

import copy
import difflib
import json
import os
import re

from . import match as _match
from . import screenplay

FORMAT = "latest-avenue-editing-script"
VERSION = 1
PRE_ROLL = 0.20         # seconds of picture before the first word of a line
POST_ROLL = 0.30        # and after the last
ACTION_SECONDS = (2.0, 6.0)   # length of an action beat cut from cover footage (min, max)
STICKY = 0.02           # score difference within which a line stays on the previous line's take


def _supervisor_placeholder():
    """Per-take slot for script supervisor notes (not read yet; see the doc)."""
    return {"circled": None, "slate": None, "take_number": None, "camera": None,
            "rating": None, "notes": [], "continuity": []}


def supervisor_reserved():
    return {"status": "reserved", "source": None, "takes": {},
            "doc": "doc/vse/script_supervisor_notes.md"}


# -----------------------------------------------------------------------------
# Building.

SLATE = re.compile(r"\b(?:scene|seen|seeing|see|sin)\s+(\w+)[\s,.-]+(?:shot\s+\w+[\s,.-]+)?take\s+(\w+)", re.I)
_WORD_NUM = {w: i for i, w in enumerate(_match._ONES)}
_WORD_NUM.update({"won": 1, "to": 2, "too": 2, "for": 4, "ate": 8})   # how numbers get misheard


def _num(word):
    word = word.lower().strip(".,")
    if word.isdigit():
        return int(word)
    return _WORD_NUM.get(word, word)


def heard_slate(words):
    """"Scene one, take two" in the first seconds of a take -> {"scene", "take", "time"}."""
    head = " ".join(w["text"] for w in words[:10])
    m = SLATE.search(head)
    if not m:
        return None
    return {"scene": _num(m.group(1)), "take": _num(m.group(2)),
            "time": words[0]["start"], "heard": m.group(0)}


NAME_SLATE = [
    # "S1_T2", "SC12A_TK3", "Scene 4 Take 2", "sc04-tk02"
    re.compile(r"(?:^|[^A-Za-z])(?:S|SC|SCN|SCENE)[ _.-]*(\d{1,3}[A-Z]?)(?:[ _.-]*[A-Z])?[ _.-]+(?:T|TK|TAKE)[ _.-]*(\d{1,3})(?![0-9])", re.I),
    # "12A-3", "12A_T3" (scene + set-up letter, take)
    re.compile(r"^(\d{1,3}[A-Z])[ _.-]+T?(\d{1,3})(?![0-9])", re.I),
]


def name_slate(name):
    """Scene and take from a file name, when it carries them. -> {"scene", "take"} or None."""
    stem = os.path.splitext(os.path.basename(name))[0]
    for pattern in NAME_SLATE:
        m = pattern.search(stem)
        if m:
            scene = m.group(1).upper()
            return {"scene": int(scene) if scene.isdigit() else scene, "take": int(m.group(2))}
    return None


def _clip_id(path, used):
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.splitext(os.path.basename(path))[0])
    cid, n = base, 1
    while cid in used:
        n += 1
        cid = "{:s}_{:d}".format(base, n)
    return cid


def _padded(take, words, media_duration):
    """Cut in/out: the speech plus pre/post roll, kept clear of neighbouring speech."""
    first, last = take["first_word"], take["last_word"]
    lo = 0.0 if first == 0 else (words[first - 1]["end"] + words[first]["start"]) / 2
    hi = media_duration if last + 1 >= len(words) else (words[last]["end"] + words[last + 1]["start"]) / 2
    cut_in = max(lo, take["in"] - PRE_ROLL, 0.0)
    cut_out = min(hi, take["out"] + POST_ROLL, media_duration or take["out"] + POST_ROLL)
    return round(cut_in, 3), round(cut_out, 3)


def build(script, clips, transcripts, matches, groups=None, visuals=None, fps=None, sound=None):
    """Put it all together.

    script: ``screenplay.parse`` result. clips: probe dicts. transcripts: {path: transcribe
    result}. matches: {path: ``match.match_take`` result}. groups: lists of clip paths that
    look alike (``visual.group``). visuals: ``visual.describe_clips`` result.
    """
    media, ids = {}, {}
    for clip in clips:
        if clip.get("error"):
            continue
        cid = _clip_id(clip["path"], media)
        ids[clip["path"]] = cid
        words = (transcripts.get(clip["path"]) or {}).get("words", [])
        entry = {k: clip.get(k) for k in ("path", "name", "kind", "duration", "fps", "frames",
                                          "width", "height", "timecode", "timecode_seconds",
                                          "audio_tracks", "creation_time")}
        entry.update(group=None, shots=(visuals or {}).get(clip["path"], {}).get("shots", []),
                     slate=heard_slate(words), name_slate=name_slate(clip["path"]), words=[
                         {"text": w["text"], "start": w["start"], "end": w["end"], "p": w.get("p")}
                         for w in words],
                     supervisor=_supervisor_placeholder())
        media[cid] = entry
    # Synced sound: {"clip": recorder clip id, "offset"}: recorder time = clip time + offset.
    for path, r in (sound or {}).items():
        if path in ids and r["audio"] in ids:
            media[ids[path]]["sound"] = {"clip": ids[r["audio"]], "offset": r["offset"],
                                         "method": r["method"], "score": r["score"]}
            media[ids[r["audio"]]].setdefault("synced_to", []).append(ids[path])

    doc = {
        "format": FORMAT, "version": VERSION, "title": script.get("title"),
        "fps": fps or next((m["fps"] for m in media.values() if m.get("fps")), 24.0),
        "sources": {"script": script.get("source"), "media": None},
        "media": media, "groups": [], "scenes": copy.deepcopy(script["scenes"]),
        "unplaced": [], "supervisor_notes": supervisor_reserved(), "next_id": 1,
    }

    # Every dialogue line's candidate takes.
    lines = screenplay.dialogue_lines(script)
    by_line = {line["id"]: [] for line in lines}
    for path, result in matches.items():
        cid = ids.get(path)
        if cid is None:
            continue
        words = media[cid]["words"]
        for hit in result.get("hits", []):
            cut_in, cut_out = _padded(hit, words, media[cid].get("duration") or 0.0)
            by_line[hit["line_id"]].append({
                "clip": cid, "in": cut_in, "out": cut_out,
                "speech_in": hit["in"], "speech_out": hit["out"],
                "words": [hit["first_word"], hit["last_word"]],
                "heard": hit["heard"], "score": hit["score"], "coverage": hit["coverage"],
                "accuracy": hit["accuracy"], "confidence": hit["confidence"],
                "supervisor": None})
    for takes in by_line.values():
        takes.sort(key=lambda t: (-t["score"], t["clip"]))
    # Near ties go to the take the previous line plays from: fewer cuts for the same quality.
    for scene in script["scenes"]:
        recent = []
        for el in scene["elements"]:
            takes = by_line.get(el["id"]) if el["type"] == "dialogue" else None
            if not takes:
                continue
            for clip in recent[-2:][::-1]:    # the previous line's take, then the one before
                same = next((k for k, t in enumerate(takes) if t["clip"] == clip), None)
                if same is not None:
                    if same and takes[0]["score"] - takes[same]["score"] <= STICKY:
                        takes.insert(0, takes.pop(same))
                    break
            recent.append(takes[0]["clip"])

    # Groups: which scenes each look belongs to.
    used = set()
    scene_clips = {}
    for line_id, takes in by_line.items():
        scene = line_id.split("-")[0]
        for t in takes:
            scene_clips.setdefault(scene, set()).add(t["clip"])
            used.add(t["clip"])
    for n, members in enumerate(groups or [], 1):
        gid = "G{:d}".format(n)
        cids = [ids[p] for p in members if p in ids]
        for c in cids:
            media[c]["group"] = gid
        scenes = sorted({s for s, cs in scene_clips.items() if cs & set(cids)},
                        key=lambda s: int(s[1:]) if s[1:].isdigit() else 0)
        doc["groups"].append({"id": gid, "clips": cids, "scenes": scenes})

    for scene in doc["scenes"]:
        scene_groups = [g for g in doc["groups"] if scene["id"] in g["scenes"]]
        scene["group"] = scene_groups[0]["id"] if scene_groups else None
        cover = _cover_options(doc, scene_groups, used)
        for el in scene["elements"]:
            if el["type"] == "dialogue":
                el["script_text"] = el["text"]
                el["takes"] = by_line.get(el["id"], [])
                el["take"] = 0 if el["takes"] else None
                el["cover"] = None
            elif el["type"] == "action":
                el["takes"] = [dict(c) for c in cover]
                el["take"] = None
            el["cut"] = None
        _fill_cuts(doc, scene)
    doc["unplaced"] = [c for c in media if c not in used and not media[c].get("synced_to") and not any(
        el.get("cut") and el["cut"].get("clip") == c or (el.get("cover") or {}).get("clip") == c
        for s in doc["scenes"] for el in s["elements"])]
    return doc


def _cover_options(doc, scene_groups, used):
    """Picture-only options for a scene: clips in its look-alike groups with no dialogue."""
    out = []
    for g in scene_groups:
        for cid in g["clips"]:
            m = doc["media"][cid]
            if cid in used or m["kind"] not in ("video", "image"):
                continue
            spans = m.get("shots") or [{"start": 0.0, "end": m.get("duration") or 4.0}]
            for shot in sorted(spans, key=lambda sh: sh["start"] - sh["end"]):   # longest first
                end = shot["end"] if m["kind"] == "video" else ACTION_SECONDS[1]
                out.append({"clip": cid, "in": shot["start"], "out": end, "score": 0.5,
                            "kind": "cover"})
    return out


def _has_picture(doc, cid):
    return doc["media"][cid]["kind"] in ("video", "image")


def _fill_cuts(doc, scene):
    """Derive each element's cut from its chosen take (and action beats from context)."""
    els = scene["elements"]
    for el in els:
        if el["type"] == "dialogue":
            el["cut"] = None
            if el.get("take") is not None and el["takes"]:
                t = el["takes"][el["take"]]
                el["cut"] = {"clip": t["clip"], "in": t["in"], "out": t["out"]}
    for k, el in enumerate(els):
        if el["type"] == "dialogue" and el["cut"] and not _has_picture(doc, el["cut"]["clip"]):
            if not el.get("cover"):
                el["cover"] = _cover_for(doc, scene, el["cut"]["out"] - el["cut"]["in"])
        if el["type"] != "action" or el.get("cut_locked"):
            continue
        prev = next((e for e in reversed(els[:k]) if e["type"] == "dialogue" and e["cut"]), None)
        nxt = next((e for e in els[k + 1:] if e["type"] == "dialogue" and e["cut"]), None)
        el["cut"] = None
        if prev and nxt and prev["cut"]["clip"] == nxt["cut"]["clip"] and \
                _has_picture(doc, prev["cut"]["clip"]) and nxt["cut"]["in"] - prev["cut"]["out"] >= 0.3:
            # The beat is already in the take between the two lines: play through it.
            el["cut"] = {"clip": prev["cut"]["clip"], "in": prev["cut"]["out"],
                         "out": nxt["cut"]["in"], "from": "between lines"}
        elif el.get("takes"):
            choice = el["takes"][el["take"] or 0]
            words = len(el["text"].split())
            length = min(ACTION_SECONDS[1], max(ACTION_SECONDS[0], words / 4.0))
            length = min(length, max(0.5, choice["out"] - choice["in"]))
            el["cut"] = {"clip": choice["clip"], "in": choice["in"],
                         "out": round(choice["in"] + length, 3), "from": "cover"}


def _cover_for(doc, scene, length):
    for g in doc["groups"]:
        if g["id"] != scene.get("group"):
            continue
        for cid in g["clips"]:
            m = doc["media"][cid]
            if m["kind"] in ("video", "image") and not any(
                    t["clip"] == cid for e in scene["elements"] for t in e.get("takes", [])
                    if e["type"] == "dialogue"):
                return {"clip": cid, "in": 0.0, "out": round(min(length, m.get("duration") or length), 3)}
    return None


# -----------------------------------------------------------------------------
# Changing it.

def find(doc, element_id):
    for scene in doc["scenes"]:
        for el in scene["elements"]:
            if el["id"] == element_id:
                return scene, el
    raise KeyError("No line {!r} in the editing script".format(element_id))


def choose_take(doc, element_id, take):
    """Play a line from another of its takes (index into its ranked list)."""
    scene, el = find(doc, element_id)
    if not el.get("takes"):
        raise ValueError("Line {:s} has no takes".format(element_id))
    if not 0 <= take < len(el["takes"]):
        raise ValueError("Line {:s} has takes 0..{:d}".format(element_id, len(el["takes"]) - 1))
    el["take"] = take
    if el.get("music"):
        # Music sections keep the song; only the picture changes.
        length = el["cut"]["out"] - el["cut"]["in"]
        c = dict(el["takes"][take])
        c["out"] = round(c["in"] + length, 3)
        el["cover"] = c
        return el
    if el["type"] == "action":
        el.pop("cut_locked", None)
    _fill_cuts(doc, scene)
    return el


def set_cut(doc, element_id, cut_in=None, cut_out=None):
    """Fine-tune a line's in/out points (seconds in its clip). They belong to the chosen take:
    choosing another take plays that take's own points."""
    scene, el = find(doc, element_id)
    if not el.get("cut"):
        raise ValueError("Line {:s} has no footage".format(element_id))
    if el["type"] == "dialogue":
        t = el["takes"][el["take"]]
        if cut_in is not None:
            t["in"] = round(float(cut_in), 3)
        if cut_out is not None:
            t["out"] = round(float(cut_out), 3)
    else:
        if cut_in is not None:
            el["cut"]["in"] = round(float(cut_in), 3)
        if cut_out is not None:
            el["cut"]["out"] = round(float(cut_out), 3)
        el["cut_locked"] = True
    _fill_cuts(doc, scene)
    return el


# -----------------------------------------------------------------------------
# Text round trip.

ACTION_INDENT, DIALOGUE_INDENT, PAREN_INDENT, CUE_INDENT, TRANSITION_INDENT = 0, 10, 16, 22, 50


def _wrap(text, width, indent):
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(" " * indent + line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(" " * indent + line)
    return out


def to_text(doc):
    """The editing script as screenplay text (the same columns as a printed screenplay)."""
    out = []
    if doc.get("title"):
        out += [doc["title"], ""]
    for scene in doc["scenes"]:
        if scene.get("heading"):
            heading = scene["heading"]
            if not screenplay.HEADING.match(heading):
                heading = "." + heading      # Fountain's forced scene heading
            out += [heading, ""]
        for el in scene["elements"]:
            if el["type"] == "action":
                out += _wrap(el["text"], 60, ACTION_INDENT)
            elif el["type"] == "transition":
                out.append(" " * TRANSITION_INDENT + el["text"])
            elif el["type"] == "dialogue":
                ext = "".join(" ({:s})".format(e) for e in el.get("extensions", []))
                out.append(" " * CUE_INDENT + el["character"] + ext)
                words = el["text"].split()
                parens = sorted(el.get("parentheticals", []), key=lambda p: p["before_word"])
                at = 0
                for p in parens:
                    if p["before_word"] > at:
                        out += _wrap(" ".join(words[at:p["before_word"]]), 35, DIALOGUE_INDENT)
                        at = p["before_word"]
                    out.append(" " * PAREN_INDENT + p["text"])
                out += _wrap(" ".join(words[at:]), 35, DIALOGUE_INDENT)
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def _similar(a, b):
    try:
        from rapidfuzz import fuzz
        return fuzz.ratio(a, b) / 100.0
    except ImportError:  # pragma: no cover
        return difflib.SequenceMatcher(None, a, b).ratio()


def _retrim(doc, take, text):
    """Narrow a take to the words of (shortened) text. -> updated copy, or None if the words
    aren't in it."""
    words = doc["media"][take["clip"]]["words"]
    first, last = take["words"]
    span = words[first:last + 1]
    heard = [(t, first + i) for t, i in _match.heard_tokens(span)]
    wanted = _match.tokens(text)
    line_tokens = [(t, 0, p) for p, t in enumerate(wanted)]
    pairs = _match.align(heard, line_tokens)
    if not pairs:
        return None
    coverage = len({sj for _h, sj, _s in pairs}) / max(1, len(wanted))
    if coverage < 0.5:
        return None
    a, b = heard[pairs[0][0]][1], heard[pairs[-1][0]][1]
    hit = {"first_word": a, "last_word": b, "in": words[a]["start"], "out": words[b]["end"]}
    new = dict(take)
    new["in"], new["out"] = _padded(hit, words, doc["media"][take["clip"]].get("duration") or 0.0)
    new.update(speech_in=hit["in"], speech_out=hit["out"], words=[a, b],
               heard=" ".join(w["text"] for w in words[a:b + 1]),
               coverage=round(coverage, 3))
    return new


def _search_all(doc, text, scene_id=None):
    """Takes anywhere in the footage that say this text (for a line typed in by hand)."""
    lines = [{"id": "new", "scene": scene_id or "S", "text": text}]
    index = _match.ScriptIndex(lines)
    found = []
    for cid, m in doc["media"].items():
        if not m.get("words"):
            continue
        result = _match.match_take(m["words"], index, min_coverage=0.6, passes=2)
        for hit in result["hits"]:
            cut_in, cut_out = _padded(hit, m["words"], m.get("duration") or 0.0)
            found.append({"clip": cid, "in": cut_in, "out": cut_out, "speech_in": hit["in"],
                          "speech_out": hit["out"], "words": [hit["first_word"], hit["last_word"]],
                          "heard": hit["heard"], "score": hit["score"], "coverage": hit["coverage"],
                          "accuracy": hit["accuracy"], "confidence": hit["confidence"],
                          "supervisor": None})
    found.sort(key=lambda t: -t["score"])
    return found


def _new_id(doc, prefix):
    n = doc.get("next_id", 1)
    doc["next_id"] = n + 1
    return "{:s}N{:d}".format(prefix, n)


def apply_text(doc, text):
    """Read edited screenplay text back into the document; footage follows the lines.

    Lines are matched to the old ones by type, character and wording (best match first, so
    moved lines are found wherever they went). A matched line keeps its takes and choice; if
    its words changed, every take is re-trimmed to the words still said. Unmatched new
    dialogue is searched for in all transcripts. -> (new doc, report).
    """
    parsed = screenplay.parse_text(text)
    old_scenes = {s["id"]: s for s in doc["scenes"]}
    old_elements = [(s["id"], el) for s in doc["scenes"] for el in s["elements"]]
    new_doc = copy.deepcopy(doc)
    report = {"kept": [], "moved": [], "retrimmed": [], "new": [], "removed": [], "found": []}

    # Scenes by heading.
    unused_scenes = list(doc["scenes"])
    scenes = []
    for s in parsed["scenes"]:
        match = None
        if s.get("heading"):
            best = max(unused_scenes, key=lambda o: _similar((o.get("heading") or ""), s["heading"]),
                       default=None)
            if best and _similar(best.get("heading") or "", s["heading"]) >= 0.8:
                match = best
        elif unused_scenes and not unused_scenes[0].get("heading"):
            match = unused_scenes[0]
        if match:
            unused_scenes.remove(match)
            scene = {k: copy.deepcopy(v) for k, v in match.items() if k != "elements"}
            scene["heading"] = s.get("heading")
        else:
            scene = {"id": _new_id(new_doc, "S"), "heading": s.get("heading"),
                     "int_ext": s.get("int_ext"), "location": s.get("location"),
                     "time": s.get("time"), "group": None}
        scene["elements"] = s["elements"]
        scenes.append(scene)

    # Elements: candidates scored on wording, matched greedily best first.
    flat = [(si, ei, el) for si, s in enumerate(scenes) for ei, el in enumerate(s["elements"])]
    pairs = []
    for fi, (_si, _ei, el) in enumerate(flat):
        for oi, (_sid, old) in enumerate(old_elements):
            if old["type"] != el["type"]:
                continue
            if el["type"] == "dialogue" and old["character"] != el["character"]:
                continue
            sim = _similar(old["text"].lower(), el["text"].lower())
            if el["type"] == "dialogue":
                # Shortened lines: the new words are mostly inside the old ones.
                new_t, old_t = set(_match.tokens(el["text"])), set(_match.tokens(old["text"]))
                if new_t:
                    sim = max(sim, 0.9 * len(new_t & old_t) / len(new_t) * min(1.0, 2 * len(new_t) / max(1, len(old_t))))
            if sim >= 0.5:
                pairs.append((sim, fi, oi))
    pairs.sort(key=lambda p: -p[0])
    taken_new, taken_old, assigned = set(), set(), {}
    for sim, fi, oi in pairs:
        if fi in taken_new or oi in taken_old:
            continue
        taken_new.add(fi)
        taken_old.add(oi)
        assigned[fi] = oi

    order_old = {old["id"]: n for n, (_s, old) in enumerate(old_elements)}
    last_old_pos = -1
    for fi, (si, ei, el) in enumerate(flat):
        scene = scenes[si]
        if fi in assigned:
            old_sid, old = old_elements[assigned[fi]]
            carried = copy.deepcopy(old)
            carried["text"] = el["text"]
            if "spoken" in carried:
                carried["spoken"] = screenplay.spoken(el["text"])
            for key in ("parentheticals", "extensions"):
                if key in el:
                    carried[key] = el[key]
            if el["type"] == "dialogue" and _match.tokens(screenplay.spoken(el["text"])) != \
                    _match.tokens(old.get("spoken") or old["text"]):
                takes = []
                for t in old.get("takes", []):
                    nt = _retrim(new_doc, t, carried.get("spoken") or el["text"])
                    if nt is not None:
                        takes.append(nt)
                chosen = old["takes"][old["take"]]["clip"] if old.get("take") is not None else None
                carried["takes"] = takes
                carried["take"] = next((k for k, t in enumerate(takes) if t["clip"] == chosen),
                                       0 if takes else None)
                report["retrimmed"].append(carried["id"])
            pos = order_old[old["id"]]
            (report["moved"] if pos < last_old_pos or old_sid != scene["id"] else report["kept"]).append(carried["id"])
            last_old_pos = max(last_old_pos, pos)
            scene["elements"][ei] = carried
        else:
            el = dict(el)
            el["id"] = _new_id(new_doc, scene["id"] + "-")
            el["cut"] = None
            if el["type"] == "dialogue":
                el["script_text"] = None
                el["cover"] = None
                el["takes"] = _search_all(new_doc, el["text"], scene["id"])
                el["take"] = 0 if el["takes"] else None
                if el["takes"]:
                    report["found"].append(el["id"])
            elif el["type"] == "action":
                el["takes"], el["take"] = [], None
            report["new"].append(el["id"])
            scene["elements"][ei] = el
    report["removed"] = [old["id"] for oi, (_s, old) in enumerate(old_elements) if oi not in taken_old]

    new_doc["scenes"] = scenes
    new_doc["title"] = parsed.get("title") or doc.get("title")
    for scene in scenes:
        _fill_cuts(new_doc, scene)
    return new_doc, report


# -----------------------------------------------------------------------------
# Reading the cut.

def timeline(doc):
    """The cut as a flat list, in order: what plays, from where, for how long.

    -> [{"element", "scene", "type", "clip", "path", "in", "out", "picture": {clip, path,
    in, out} | None, "record_start"}] with record_start in seconds from the top.
    """
    out, t = [], 0.0
    for scene in doc["scenes"]:
        for el in scene["elements"]:
            cut = el.get("cut")
            if not cut or cut["out"] <= cut["in"]:
                continue
            m = doc["media"][cut["clip"]]
            picture = None
            if m["kind"] in ("video", "image"):
                picture = {"clip": cut["clip"], "path": m["path"], "in": cut["in"], "out": cut["out"]}
            elif el.get("cover"):
                c = el["cover"]
                picture = {"clip": c["clip"], "path": doc["media"][c["clip"]]["path"],
                           "in": c["in"], "out": c["in"] + (cut["out"] - cut["in"])}
            sound = m["kind"] in ("video", "audio") and bool(m.get("audio_tracks"))
            sound_path, sound_in = m["path"], cut["in"]
            if m.get("sound"):      # separately recorded sound, synced
                sound = True
                sound_path = doc["media"][m["sound"]["clip"]]["path"]
                sound_in = round(cut["in"] + m["sound"]["offset"], 4)
            if el["type"] == "action" and cut.get("from") == "cover":
                sound = False   # cover footage plays mute under an action beat
            out.append({"element": el["id"], "scene": scene["id"], "type": el["type"],
                        "clip": cut["clip"], "path": m["path"], "in": cut["in"], "out": cut["out"],
                        "sound": sound, "sound_path": sound_path, "sound_in": sound_in,
                        "picture": picture, "record_start": round(t, 3)})
            t += cut["out"] - cut["in"]
    return out


def summary(doc):
    tl = timeline(doc)
    lines = [el for s in doc["scenes"] for el in s["elements"] if el["type"] == "dialogue"]
    return {
        "title": doc.get("title"),
        "scenes": len(doc["scenes"]),
        "dialogue_lines": len(lines),
        "lines_with_footage": sum(1 for el in lines if el.get("cut")),
        "lines_without_footage": [el["id"] for el in lines if not el.get("cut")],
        "events": len(tl),
        "duration": round(sum(e["out"] - e["in"] for e in tl), 3),
        "media": len(doc["media"]),
        "unplaced": doc.get("unplaced", []),
        "groups": [{"id": g["id"], "clips": len(g["clips"]), "scenes": g["scenes"]}
                   for g in doc.get("groups", [])],
    }


def load(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("format") != FORMAT:
        raise ValueError("{:s} is not an editing script".format(path))
    return doc


def save(doc, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return path
