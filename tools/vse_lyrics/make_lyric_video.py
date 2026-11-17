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


def pick_clips(library, count, seed, used=(), weights=None):
    """An ordered list of `count` clips, drawn category by category so the video mixes
    subjects, never repeating a clip in `used`.

    :arg weights: {category: weight}; categories not named get weight 1.
    """
    rng = random.Random(seed)
    used = set(used)
    pools = {}
    for category, paths in find_clips(library).items():
        paths = [p for p in paths if p not in used]
        rng.shuffle(paths)
        if paths:
            pools[category] = paths
    weights = weights or {}
    picked, last = [], None
    while len(picked) < count and pools:
        names = sorted(pools)
        # Not the same category twice in a row when there is a choice.
        choices = [n for n in names if n != last] or names
        category = rng.choices(choices, [weights.get(n, 1.0) for n in choices])[0]
        picked.append(pools[category].pop())
        if not pools[category]:
            del pools[category]
        last = category
    return picked


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
    parser.add_argument("--weights", default="people=3,cars=1.5,urban=1.5",
                        help="how often each category is picked, e.g. people=3,cars=1")
    parser.add_argument("--style", default="{}", help="JSON overrides for lyric_video.STYLE")
    parser.add_argument("--report", help="write what was built to this JSON file")
    args = parser.parse_args(argv)

    from vse_standalone import api, lyric_video

    used = []
    if args.used and os.path.exists(args.used):
        with open(args.used, encoding="utf-8") as fh:
            used = json.load(fh)
    if args.clips:
        clips = args.clips
    else:
        if not args.library:
            parser.error("give --library or --clips")
        with open(args.lyrics, encoding="utf-8") as fh:
            lines = len(json.load(fh)["lines"])
        weights = {k: float(v) for k, v in (item.split("=") for item in args.weights.split(",") if item)}
        clips = pick_clips(args.library, lines * 4 + 8, args.seed, used, weights)

    result = api.call("make_lyric_video", {
        "audio": args.audio, "lyrics": args.lyrics, "clips": clips,
        "output": args.output, "project": args.project, "style": json.loads(args.style)})

    if args.used:
        used = sorted(set(used) | {c["filepath"] for c in result["cuts"]})
        with open(args.used, "w", encoding="utf-8") as fh:
            json.dump(used, fh, indent=1)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump({k: result[k] for k in ("frames", "duration", "cuts", "skipped", "lyrics",
                                              "project", "render") if k in result}, fh, indent=1)
    for cut in result["cuts"]:
        print("CUT {:6.2f} {:6.2f}  {:s}".format(cut["start"], cut["end"], cut["filepath"]))
    print("DONE", json.dumps(result.get("render", {})))


if __name__ == "__main__":
    main()
