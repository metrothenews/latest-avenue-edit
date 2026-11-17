# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Make one "new style" lyric video from the command line, with the editor running without a
window. Picks clips from a clip library (a different set for every video), builds the
timeline with ``vse_standalone.lyric_video`` and exports the MP4.

    <app>/Contents/MacOS/Blender -b --factory-startup --python tools/vse_lyrics/make_lyric_video.py -- \
        --audio song.wav --lyrics song.lyrics.json --library "/path/to/clips" \
        --output out/video1.mp4 --project out/video1.blend --used out/used_clips.json --seed 1

``--used`` names a JSON file listing clips earlier videos used; they are not picked again and
this video's clips are added to it, so every video gets different footage.

See doc/vse/lyric_video_workflow.md.
"""

import argparse
import json
import os
import random
import sys

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}


def find_clips(library, skip_folders=("example videos",)):
    """-> {category: [paths]}; the category is the clip's folder name."""
    by_category = {}
    for root, dirs, files in os.walk(library):
        dirs[:] = sorted(d for d in dirs if d.lower() not in skip_folders and not d.startswith("."))
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS and not name.startswith("."):
                by_category.setdefault(os.path.basename(root), []).append(os.path.join(root, name))
    return by_category


def clip_durations(paths, cache_path=None):
    """-> {path: seconds}. Probed with ffprobe (or by loading the clip in the editor when
    ffprobe is not installed) and remembered in `cache_path`."""
    import shutil
    import subprocess
    cache = {}
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
    ffprobe = shutil.which("ffprobe")
    missing = [p for p in paths if p not in cache]
    for path in missing:
        if ffprobe:
            out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                                  "-of", "csv=p=0", path], stdout=subprocess.PIPE, text=True).stdout.strip()
            cache[path] = float(out) if out else 0.0
        else:
            import bpy
            scene = bpy.context.scene
            if scene.sequence_editor is None:
                scene.sequence_editor_create()
            strip = scene.sequence_editor.strips.new_movie(name="probe", filepath=path, channel=1, frame_start=1)
            cache[path] = strip.frame_duration / (scene.render.fps / scene.render.fps_base)
            scene.sequence_editor.strips.remove(strip)
    if cache_path and missing:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=1)
    return {p: cache[p] for p in paths}


def subject_quotas(subjects, slots, weights, earlier=None):
    """How many of `slots` each subject gets.

    Every video is balanced on its own: shares follow `weights` (largest-remainder rounding),
    and every subject gets at least one slot when there are enough slots. `earlier` holds how
    many clips of each subject earlier videos of the same batch used; it only decides who
    gets the leftover slots, in favour of subjects that are behind their share so far.
    """
    earlier = earlier or {}
    total_weight = sum(weights.get(s, 1.0) for s in subjects)
    share = {s: weights.get(s, 1.0) / total_weight for s in subjects}
    want = {s: slots * share[s] for s in subjects}
    quota = {s: int(want[s]) for s in subjects}
    done = sum(earlier.get(s, 0) for s in subjects)
    behind = {s: max(-1.0, min(1.0, done * share[s] - earlier.get(s, 0))) for s in subjects}
    leftover = sorted(subjects, key=lambda s: (-(want[s] - quota[s] + 0.5 * behind[s]), s))
    for s in leftover[:slots - sum(quota.values())]:
        quota[s] += 1
    if slots >= len(subjects):
        for s in sorted(subjects):
            while quota[s] == 0:
                donor = max(subjects, key=lambda d: (quota[d], d))
                quota[donor] -= 1
                quota[s] += 1
    return quota


def subject_order(quota, first=None):
    """Spread the subjects over the slots: each subject's clips are spaced evenly through the
    video, and the same subject never comes twice in a row when that can be avoided."""
    spots = []
    for subject in sorted(quota):
        for j in range(quota[subject]):
            spots.append(((j + 0.5) / quota[subject], subject))
    order = [subject for _pos, subject in sorted(spots)]
    if first in order:
        k = order.index(first)
        order = order[k:] + order[:k]
    for i in range(1, len(order)):
        if order[i] == order[i - 1]:
            for j in range(i + 1, len(order)):
                if order[j] != order[i - 1] and (j + 1 >= len(order) or order[j + 1] != order[i]):
                    order[i], order[j] = order[j], order[i]
                    break
    return order


def plan_clips(library, slots, seed, used=(), weights=None, first="people", durations_cache=None,
               earlier_subjects=None):
    """Choose one clip for every slot, by subject.

    :arg slots: [(start, end), ...] in seconds (`lyric_video.plan_cuts`).
    :arg used: Clips earlier videos used; never chosen again.
    :return: (clips in slot order, plan rows for the report).

    1. Subjects are the names of the folders the clips sit in (people, cars, urban, ...).
    2. `subject_quotas` decides how many slots each subject gets, `subject_order` where.
    3. Within a subject, the clip must be long enough for its slot; among those, the
       collection (the folder above the subject) used least so far is preferred, then a
       seeded random choice. A subject with no clip long enough hands the slot to the
       subject with the most clips left that fit.
    """
    rng = random.Random(seed)
    used = set(used)
    library_clips = find_clips(library)
    available = {s: [p for p in paths if p not in used] for s, paths in library_clips.items()}
    available = {s: paths for s, paths in available.items() if paths}
    durations = clip_durations(sorted(p for paths in available.values() for p in paths), durations_cache)
    subjects = sorted(available)
    quota = subject_quotas(subjects, len(slots), weights or {}, earlier_subjects)
    order = subject_order(quota, first)

    def collection(path):
        return os.path.basename(os.path.dirname(os.path.dirname(path)))

    collection_use = {}
    for path in used:
        collection_use[collection(path)] = collection_use.get(collection(path), 0) + 1
    clips, rows = [], []
    for (start, end), subject in zip(slots, order):
        needed = end - start + 0.05

        def fitting(name):
            return [p for p in available.get(name, ()) if durations[p] >= needed]
        choice, wanted = subject, subject
        if not fitting(subject):
            others = [s for s in subjects if fitting(s) and (not rows or s != rows[-1]["subject"])] or \
                     [s for s in subjects if fitting(s)]
            if not others:
                clips.append(None)
                rows.append({"start": start, "end": end, "subject": subject, "clip": None,
                             "note": "no clip long enough; the builder will cut this slot in two"})
                continue
            choice = max(others, key=lambda s: (len(fitting(s)), s))
        candidates = fitting(choice)
        least = min(collection_use.get(collection(p), 0) for p in candidates)
        candidates = sorted(p for p in candidates if collection_use.get(collection(p), 0) == least)
        path = rng.choice(candidates)
        available[choice].remove(path)
        collection_use[collection(path)] = collection_use.get(collection(path), 0) + 1
        clips.append(path)
        row = {"start": round(start, 3), "end": round(end, 3), "subject": choice,
               "collection": collection(path), "clip": path, "clip_seconds": round(durations[path], 2)}
        if choice != wanted:
            row["note"] = "wanted {:s}, none long enough".format(wanted)
        rows.append(row)
    # Spare clips for slots the builder has to cut in two.
    spare = [p for s in subjects for p in available[s]]
    rng.shuffle(spare)
    return [c for c in clips if c] + spare[:12], rows, quota


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--audio", required=True)
    parser.add_argument("--lyrics", required=True, help=".lyrics.json from align_lyrics.py")
    parser.add_argument("--library", help="folder of clips to pick from")
    parser.add_argument("--clips", nargs="*", help="use exactly these clips, in order, instead of picking")
    parser.add_argument("--output", help="MP4 to export")
    parser.add_argument("--project", help=".blend project to save")
    parser.add_argument("--used", help="JSON file tracking clips used by earlier videos")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--weights", default="people=2",
                        help="share of the slots per subject folder, e.g. people=2,cars=1.5 "
                             "(subjects not named count 1)")
    parser.add_argument("--first", default="people", help="subject of the opening clip")
    parser.add_argument("--catalog", help="JSON file caching clip lengths (default: next to --used)")
    parser.add_argument("--style", default="{}", help="JSON overrides for lyric_video.STYLE")
    parser.add_argument("--report", help="write what was built to this JSON file")
    parser.add_argument("--sync-check", help="also export a review copy to this MP4, with a tick "
                                             "on every sung word, for checking the timing by ear")
    args = parser.parse_args(argv)

    from vse_standalone import api, lyric_video

    used, history = [], {}
    if args.used and os.path.exists(args.used):
        with open(args.used, encoding="utf-8") as fh:
            saved = json.load(fh)
        # {"videos": {name: [clips]}} ; older files are a plain list of clips.
        history = saved.get("videos", {}) if isinstance(saved, dict) else {"earlier": saved}
    name = os.path.splitext(os.path.basename(args.output or args.project or args.audio))[0]
    history.pop(name, None)  # building this video again: its old clips are free to use
    used = sorted({c for clips in history.values() for c in clips})
    with open(args.lyrics, encoding="utf-8") as fh:
        lyrics = json.load(fh)
    plan = quota = None
    if args.clips:
        clips = args.clips
    else:
        if not args.library:
            parser.error("give --library or --clips")
        weights = {k: float(v) for k, v in (item.split("=") for item in args.weights.split(",") if item)}
        slots = lyric_video.plan_cuts(lyrics, lyrics["duration"])
        earlier = {}
        for path in used:
            subject = os.path.basename(os.path.dirname(path))
            earlier[subject] = earlier.get(subject, 0) + 1
        catalog = args.catalog or (os.path.join(os.path.dirname(os.path.abspath(args.used)), "clip_lengths.json")
                                   if args.used else None)
        clips, plan, quota = plan_clips(args.library, slots, args.seed, used, weights, args.first,
                                        catalog, earlier)

    result = api.call("make_lyric_video", {
        "audio": args.audio, "lyrics": args.lyrics, "clips": clips,
        "output": args.output, "project": args.project, "style": json.loads(args.style)})

    if args.sync_check:
        api.call("add_sync_ticks", {"lyrics": args.lyrics})
        api.call("render_video", {"filepath": args.sync_check, "format": "H264_MP4", "quality": "MEDIUM"})
    if args.used:
        history[name] = [c["filepath"] for c in result["cuts"]]
        with open(args.used, "w", encoding="utf-8") as fh:
            json.dump({"videos": history}, fh, indent=1)
    subjects = {}
    for cut in result["cuts"]:
        cut["subject"] = os.path.basename(os.path.dirname(cut["filepath"]))
        subjects[cut["subject"]] = subjects.get(cut["subject"], 0) + 1
    if args.report:
        report = {k: result[k] for k in ("frames", "duration", "cuts", "skipped", "lyrics",
                                         "project", "render") if k in result}
        report.update(subjects=subjects, subject_quota=quota, clip_plan=plan)
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    print("SUBJECTS", json.dumps(subjects, sort_keys=True))
    for cut in result["cuts"]:
        print("CUT {:6.2f} {:6.2f}  {:12s} {:s}".format(cut["start"], cut["end"], cut["subject"], cut["filepath"]))
    print("DONE", json.dumps(result.get("render", {})))


if __name__ == "__main__":
    main()
