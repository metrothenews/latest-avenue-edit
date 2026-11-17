#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Time a song's lyrics against an audio file, word by word.

The audio is transcribed with word timestamps (faster-whisper). What the recognizer heard is
then matched against the lyric sheet: the part of the sheet that was sung is found, and every
word is replaced by the sheet's exact word, keeping the recognizer's timing. Words the
recognizer missed get a time between their neighbours; words it invented are dropped.

The result is a ``.lyrics.json`` file that ``vse_standalone.lyric_video`` (the
``add_lyric_words`` tool of the editor) turns into on-screen text.

Runs outside the editor, in its own Python environment (see doc/vse/lyric_video_workflow.md):

    python3 -m venv lyrics-env && lyrics-env/bin/pip install faster-whisper
    lyrics-env/bin/python tools/vse_lyrics/align_lyrics.py song.wav lyrics.txt -o song.lyrics.json

The lyric sheet is plain text, one line per lyric line. Lines in [brackets] (section names)
and empty lines are ignored.
"""

import argparse
import difflib
import json
import os
import re
import sys
import unicodedata

# Letters from other alphabets that look like Latin ones; lyric sheets copied from the web
# often contain them and fonts without those alphabets show them in a fallback font.
HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j",
    "ѕ": "s", "ԁ": "d", "ɡ": "g", "һ": "h", "ո": "n", "ս": "u", "ӏ": "l",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
    "Т": "T", "Х": "X", "Ү": "Y", "Ѕ": "S", "І": "I", "Ј": "J",
    "\u00a0": " ", "\u200b": "",
})


def clean_display(text):
    """Text as it should appear on screen: look-alike letters replaced, straight apostrophes."""
    return text.translate(HOMOGLYPHS).replace("’", "'").replace("‘", "'")


def norm(word):
    """Form used for matching: lowercase letters and digits only."""
    word = unicodedata.normalize("NFKD", clean_display(word)).lower()
    word = word.replace("in'", "ing")
    return re.sub(r"[^a-z0-9]", "", word)


def read_sheet(path):
    """-> list of {"text", "line"} for every word of the sheet, and the list of lines."""
    words, lines, homoglyphs = [], [], []
    with open(path, encoding="utf-8-sig") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw or re.fullmatch(r"\\?\[.*\\?\]", raw):
                continue
            if raw != raw.translate(HOMOGLYPHS):
                homoglyphs.append(raw)
            line = clean_display(raw)
            lines.append(line)
            for token in line.split():
                if norm(token):
                    words.append({"text": token, "line": len(lines) - 1})
    return words, lines, homoglyphs


def transcribe(audio, model_name, prompt=None, language="en", models_dir=None):
    import subprocess
    import numpy
    from faster_whisper import WhisperModel
    # Decode with ffmpeg (16 kHz mono float), not the recognizer's own loader: that one
    # breaks whenever the PyAV package is newer than the recognizer expects.
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", audio, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        check=True, stdout=subprocess.PIPE).stdout
    samples = numpy.frombuffer(raw, dtype=numpy.float32)
    model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=models_dir)
    segments, info = model.transcribe(
        samples, language=language, word_timestamps=True, beam_size=5,
        vad_filter=False, condition_on_previous_text=False, initial_prompt=prompt)
    heard = []
    for segment in segments:
        for w in segment.words or ():
            if norm(w.word):
                heard.append({"text": w.word.strip(), "start": float(w.start), "end": float(w.end),
                              "probability": float(w.probability)})
    return heard, float(info.duration)


def similarity(a, b):
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def local_align(heard, sheet):
    """Smith-Waterman over words. -> list of (heard index or None, sheet index or None)."""
    n, m = len(heard), len(sheet)
    gap = -0.6
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    best, best_at = 0.0, (0, 0)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sim = similarity(heard[i - 1], sheet[j - 1])
            match = score[i - 1][j - 1] + (2.0 * sim - 1.0 if sim >= 0.5 else -1.0)
            up = score[i - 1][j] + gap      # heard word that is not on the sheet
            left = score[i][j - 1] + gap    # sheet word that was not heard
            value = max(0.0, match, up, left)
            score[i][j] = value
            back[i][j] = 0 if value == 0.0 else (1 if value == match else (2 if value == up else 3))
            if value > best:
                best, best_at = value, (i, j)
    pairs = []
    i, j = best_at
    while i > 0 and j > 0 and back[i][j]:
        if back[i][j] == 1:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif back[i][j] == 2:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    pairs.reverse()
    return pairs, best


def align(heard, sheet_words, lines, duration, *, lead_in=None, lead_out=None):
    # Words of next to no length at the very start or end are the recognizer inventing text
    # in silence ("thanks for watching"); they must not be matched to lyrics.
    heard = list(heard)
    while heard and heard[-1]["end"] - heard[-1]["start"] < 0.08:
        heard.pop()
    while heard and heard[0]["end"] - heard[0]["start"] < 0.08:
        heard.pop(0)
    pairs, score = local_align([norm(w["text"]) for w in heard], [norm(w["text"]) for w in sheet_words])
    sheet_idx = [j for _i, j in pairs if j is not None]
    if not sheet_idx:
        raise SystemExit("No part of the lyric sheet matches what was heard in the audio.")
    first, last = sheet_idx[0], sheet_idx[-1]
    heard_idx = [i for i, _j in pairs if i is not None]

    timed = {}
    corrections = []
    for i, j in pairs:
        if i is not None and j is not None:
            timed[j] = (heard[i]["start"], heard[i]["end"], heard[i]["text"], heard[i]["probability"])
            if norm(heard[i]["text"]) != norm(sheet_words[j]["text"]):
                corrections.append({"heard": heard[i]["text"], "sheet": sheet_words[j]["text"],
                                    "time": round(heard[i]["start"], 2)})
        elif i is not None:
            corrections.append({"heard": heard[i]["text"], "sheet": None, "time": round(heard[i]["start"], 2)})

    # Singing at the edges that the alignment could not place (badly misheard words). Words of
    # next to no length are the recognizer inventing text in silence ("thanks for watching").
    def real(words):
        return [w for w in words if w["end"] - w["start"] >= 0.08]
    before = real(heard[:heard_idx[0]])
    after = real(heard[heard_idx[-1] + 1:])

    def spread(indices, t0, t1, source):
        """Give sheet words `indices` evenly spaced times in [t0, t1]."""
        step = (t1 - t0) / len(indices)
        text = " ".join(w["text"] for w in source)
        for n, j in enumerate(indices):
            timed[j] = (t0 + step * n, t0 + step * (n + 1), None, 0.0)
            corrections.append({"heard": text if n == 0 else "...", "sheet": sheet_words[j]["text"],
                                "time": round(t0 + step * n, 2)})

    # Whole lines: when the match starts or ends inside a lyric line and there is unplaced
    # singing next to it, take the rest of that line and spread it over that singing.
    auto_in, auto_out = lead_in is None, lead_out is None
    if lead_in is None:
        lead_in = 0
        if before:
            while first - lead_in > 0 and sheet_words[first - lead_in - 1]["line"] == sheet_words[first]["line"]:
                lead_in += 1
    if lead_out is None:
        lead_out = 0
        if after:
            while (last + lead_out + 1 < len(sheet_words) and
                   sheet_words[last + lead_out + 1]["line"] == sheet_words[last]["line"]):
                lead_out += 1

    def line_length(line):
        return sum(1 for w in sheet_words if w["line"] == line)

    # The match ends exactly at a line edge but there is more singing: take the neighbouring
    # line when it has about as many words as were heard.
    if lead_in == 0 and len(before) >= 2 and first > 0 and auto_in:
        size = line_length(sheet_words[first - 1]["line"])
        if abs(size - len(before)) <= max(1, size // 2):
            lead_in = size
    if lead_out == 0 and len(after) >= 2 and last + 1 < len(sheet_words) and auto_out:
        size = line_length(sheet_words[last + 1]["line"])
        if abs(size - len(after)) <= max(1, size // 2):
            lead_out = size
    lead_in = min(lead_in, first)
    lead_out = min(lead_out, len(sheet_words) - 1 - last)
    if lead_in:
        t1 = timed[first][0]
        t0 = before[0]["start"] if before else max(0.0, t1 - 0.3 * lead_in)
        spread(list(range(first - lead_in, first)), min(t0, t1), t1, before)
    if lead_out:
        t0 = timed[last][1]
        t1 = after[-1]["end"] if after else min(duration, t0 + 0.3 * lead_out)
        spread(list(range(last + 1, last + lead_out + 1)), t0, max(t0, t1), after)
    first, last = first - lead_in, last + lead_out

    words = []
    for j in range(first, last + 1):
        entry = {"text": sheet_words[j]["text"], "line": sheet_words[j]["line"]}
        if j in timed:
            start, end, heard_text, probability = timed[j]
            entry.update(start=start, end=end, heard=heard_text, probability=round(probability, 3),
                         matched=heard_text is not None)
        else:
            entry.update(start=None, end=None, heard=None, probability=0.0, matched=False)
        words.append(entry)

    # Words the recognizer missed: spread evenly between the timed neighbours.
    k = 0
    while k < len(words):
        if words[k]["start"] is not None:
            k += 1
            continue
        run_end = k
        while run_end < len(words) and words[run_end]["start"] is None:
            run_end += 1
        t0 = words[k - 1]["end"] if k > 0 else 0.0
        t1 = words[run_end]["start"] if run_end < len(words) else duration
        step = (t1 - t0) / (run_end - k + 1) if t1 > t0 else 0.0
        for n, w in enumerate(words[k:run_end]):
            w["start"] = t0 + step * n if k > 0 else max(0.0, t1 - step * (run_end - k - n))
            w["end"] = w["start"] + step
            corrections.append({"heard": None, "sheet": w["text"], "time": round(w["start"], 2)})
        k = run_end

    # Starts never go backwards and every word ends before the next one starts.
    for a, b in zip(words, words[1:]):
        b["start"] = max(b["start"], a["start"])
        a["end"] = min(max(a["end"], a["start"]), b["start"]) if a["end"] > b["start"] else a["end"]
    for w in words:
        w["start"], w["end"] = round(w["start"], 3), round(max(w["end"], w["start"]), 3)

    out_lines = []
    for w_index, w in enumerate(words):
        if not out_lines or out_lines[-1]["line"] != w["line"]:
            out_lines.append({"line": w["line"], "sheet_text": lines[w["line"]], "words": []})
        out_lines[-1]["words"].append(w_index)
    for entry in out_lines:
        entry["text"] = " ".join(words[i]["text"] for i in entry["words"])
        entry["start"] = words[entry["words"][0]]["start"]
        entry["end"] = words[entry["words"][-1]]["end"]
        entry["complete"] = entry["text"] == " ".join(entry["sheet_text"].split())

    matched = sum(1 for w in words if w["matched"])
    exact = sum(1 for w in words if w["matched"] and norm(w["heard"]) == norm(w["text"]))
    return {
        "duration": round(duration, 3),
        "words": words,
        "lines": out_lines,
        "corrections": sorted(corrections, key=lambda c: c["time"]),
        "stats": {"words": len(words), "timed_from_audio": matched, "heard_exactly": exact,
                  "alignment_score": round(score, 2),
                  "confidence": round(exact / len(words), 3) if words else 0.0},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("audio")
    parser.add_argument("lyrics", help="lyric sheet, plain text")
    parser.add_argument("-o", "--output", help="default: <audio>.lyrics.json")
    parser.add_argument("--model", default="medium.en",
                        help="faster-whisper model: small.en, medium.en (default), large-v3, or a "
                             "folder with a converted model (e.g. a fine-tuned or LoRA-merged one)")
    parser.add_argument("--models-dir", default=os.environ.get("VSE_LYRICS_MODELS"),
                        help="folder to download and keep recognizer models in (default: the "
                             "VSE_LYRICS_MODELS environment variable, else ~/.cache/huggingface)")
    parser.add_argument("--no-prompt", action="store_true",
                        help="do not show the recognizer the lyric sheet's vocabulary")
    parser.add_argument("--lead-in", type=int, default=None,
                        help="sheet words to add before the match (default: complete the first line "
                             "when there is unplaced singing before it)")
    parser.add_argument("--lead-out", type=int, default=None,
                        help="sheet words to add after the match (default: complete the last line "
                             "when there is unplaced singing after it)")
    parser.add_argument("--heard", help="reuse the recognizer output saved in this JSON file")
    parser.add_argument("--save-heard", help="save the recognizer output to this JSON file")
    args = parser.parse_args()

    sheet_words, lines, homoglyphs = read_sheet(args.lyrics)
    for raw in homoglyphs:
        print("note: look-alike letters from another alphabet replaced in: {:s}".format(raw), file=sys.stderr)

    if args.heard:
        with open(args.heard, encoding="utf-8") as fh:
            saved = json.load(fh)
        heard, duration = saved["heard"], saved["duration"]
    else:
        prompt = None
        if not args.no_prompt:
            # The sheet's distinct words, as vocabulary only: not in song order, so the
            # recognizer can't simply recite the sheet.
            vocabulary = sorted({clean_display(w["text"]).strip(",.?!—-").lower() for w in sheet_words})
            prompt = "Song lyrics. Words: " + ", ".join(vocabulary[:120])
        heard, duration = transcribe(args.audio, args.model, prompt, models_dir=args.models_dir)
    if args.save_heard:
        with open(args.save_heard, "w", encoding="utf-8") as fh:
            json.dump({"heard": heard, "duration": duration, "model": args.model}, fh, indent=1)

    result = align(heard, sheet_words, lines, duration, lead_in=args.lead_in, lead_out=args.lead_out)
    result.update(audio=args.audio, lyrics=args.lyrics, model=args.model,
                  heard_text=" ".join(w["text"] for w in heard))
    output = args.output or re.sub(r"\.[^.]+$", "", args.audio) + ".lyrics.json"
    with open(output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, ensure_ascii=False)

    stats = result["stats"]
    print("heard:  " + result["heard_text"])
    print("lyrics: " + " / ".join(line["text"] for line in result["lines"]))
    print("{:d} words, {:d} timed from the audio, {:d} heard exactly ({:.0%}); {:d} corrections".format(
        stats["words"], stats["timed_from_audio"], stats["heard_exactly"], stats["confidence"],
        len(result["corrections"])))
    for c in result["corrections"]:
        print("  {:6.2f}s  heard {!s:14}  sheet {!s}".format(c["time"], c["heard"], c["sheet"]))
    print("wrote " + output)


if __name__ == "__main__":
    main()
