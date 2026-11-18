# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Command line for the assembly (also what the editor's tools run in the assembly's own Python):

    python -m vse_standalone.assembly probe FILE_OR_FOLDER
    python -m vse_standalone.assembly script SCRIPT.pdf [-o script.json] [--text]
    python -m vse_standalone.assembly assemble MEDIA_FOLDER [--script SCRIPT.pdf] -o cut.edit.json
    python -m vse_standalone.assembly text cut.edit.json             (screenplay text out)
    python -m vse_standalone.assembly apply cut.edit.json EDITED.txt  (edited text back in)
    python -m vse_standalone.assembly take cut.edit.json LINE_ID INDEX
    python -m vse_standalone.assembly summary cut.edit.json
    python -m vse_standalone.assembly render cut.edit.json -o cut.mp4

``tools/vse_assembly/assemble.py`` is the same with the path set up.
"""

import argparse
import json
import os
import sys

from . import editing_script, probe, screenplay


def _print(data):
    json.dump(data, sys.stdout, indent=1, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="assembly", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="media info from ffprobe")
    p.add_argument("path")

    p = sub.add_parser("script", help="read a screenplay")
    p.add_argument("script")
    p.add_argument("-o", "--output")
    p.add_argument("--text", action="store_true", help="print the pdftotext layout text only")

    p = sub.add_parser("assemble", help="footage (+ script) -> editing script")
    p.add_argument("media", help="folder of footage, photos and audio")
    p.add_argument("--script", help="screenplay .pdf/.fountain/.txt (optional)")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--model", help="faster-whisper model (default base.en, or VSE_ASSEMBLY_WHISPER)")
    p.add_argument("--image-backend", choices=("mobileclip", "signature"))
    p.add_argument("--no-visual", action="store_true", help="skip shots, thumbnails and grouping")
    p.add_argument("--no-model-order", action="store_true",
                   help="without a script: keep timecode order, don't ask the language model")
    p.add_argument("--cache", help="cache folder (default MEDIA/.assembly_cache)")
    p.add_argument("--render", help="also render the assembly with ffmpeg to this file")

    p = sub.add_parser("text", help="editing script -> screenplay text")
    p.add_argument("doc")

    p = sub.add_parser("apply", help="edited screenplay text -> editing script (footage follows)")
    p.add_argument("doc")
    p.add_argument("text", help="text file, or - for standard input")
    p.add_argument("-o", "--output", help="default: overwrite DOC")

    p = sub.add_parser("take", help="play a line from another take")
    p.add_argument("doc")
    p.add_argument("line")
    p.add_argument("index", type=int)

    p = sub.add_parser("summary")
    p.add_argument("doc")

    p = sub.add_parser("timeline", help="the cut as a flat list of events")
    p.add_argument("doc")

    p = sub.add_parser("render", help="render the editing script with ffmpeg")
    p.add_argument("doc")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)

    args = parser.parse_args(argv)
    log = lambda text: print(text, file=sys.stderr)  # noqa: E731

    if args.cmd == "probe":
        _print(probe.scan(args.path) if os.path.isdir(args.path) else probe.probe(args.path))
    elif args.cmd == "script":
        if args.text:
            sys.stdout.write(screenplay.pdf_to_text(args.script))
            return
        result = screenplay.parse(args.script)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=1, ensure_ascii=False)
            log("wrote " + args.output)
        else:
            _print(result)
    elif args.cmd == "assemble":
        from . import pipeline
        doc = pipeline.assemble(args.media, args.script, model=args.model,
                                image_backend=args.image_backend, cache_dir=args.cache,
                                visual_grouping=not args.no_visual,
                                ask_model=not args.no_model_order, log=log)
        editing_script.save(doc, args.output)
        log("wrote " + args.output)
        if args.render:
            from . import render
            log("rendered " + json.dumps(render.render(doc, args.render)))
        _print(editing_script.summary(doc))
    elif args.cmd == "text":
        sys.stdout.write(editing_script.to_text(editing_script.load(args.doc)))
    elif args.cmd == "apply":
        doc = editing_script.load(args.doc)
        text = sys.stdin.read() if args.text == "-" else open(args.text, encoding="utf-8").read()
        new, report = editing_script.apply_text(doc, text)
        editing_script.save(new, args.output or args.doc)
        _print(report)
    elif args.cmd == "take":
        doc = editing_script.load(args.doc)
        el = editing_script.choose_take(doc, args.line, args.index)
        editing_script.save(doc, args.doc)
        _print({"line": el["id"], "cut": el["cut"]})
    elif args.cmd == "summary":
        _print(editing_script.summary(editing_script.load(args.doc)))
    elif args.cmd == "timeline":
        _print(editing_script.timeline(editing_script.load(args.doc)))
    elif args.cmd == "render":
        from . import render
        _print(render.render(editing_script.load(args.doc), args.output,
                             width=args.width, height=args.height))


if __name__ == "__main__":
    main()
