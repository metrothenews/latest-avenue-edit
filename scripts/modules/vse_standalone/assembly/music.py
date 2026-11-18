# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Music-driven assembly (music videos, edits cut to a track): the song sets the timeline,
pictures change on the beat.

1. **Beats** (numpy, no model): spectral-flux onset strength, tempo from its autocorrelation
   (weighted toward 120 BPM), beats by dynamic programming (Ellis 2007), bar starts by the
   strongest of the four phases (4/4 assumed).
2. **Lyric lines** (optional): the lyric sheet is read as a script (one line per lyric line)
   and the song is transcribed and matched to it with the same alignment as dialogue takes.
3. **Sections**: one per lyric line, from the bar or beat before its first word to the next
   line; instrumental stretches in sections of ``bars_per_shot`` bars. Every boundary is a beat.
4. **Pictures**: clips grouped by look are dealt out so that neighbouring shots differ, each
   starting at its longest shot, never reusing the same stretch of a clip while others remain.

The result is an ordinary editing script (``"mode": "music"``): sections are action lines
whose cut is the song and whose picture (``cover``) can be swapped with ``choose_take``.
"""

import os

SR = 22050
HOP = 441               # 50 frames a second
FPS = SR / HOP


def _load(path):
    from .sync import _audio
    return _audio(path, SR)


def onset_strength(samples):
    """Onset strength every frame (frame k is centred at k / FPS seconds).

    Spectral flux in 24 log-spaced bands, low bands weighted up: kick and bass carry the beat,
    and a flux summed over plain FFT bins is dominated by broadband hi-hats (often off-beat).
    """
    import numpy
    n_fft = 2048
    pad = numpy.concatenate([numpy.zeros(n_fft // 2, "float32"), samples,
                             numpy.zeros(n_fft // 2, "float32")])
    if len(pad) < n_fft:
        return numpy.zeros(1)
    frames = 1 + (len(pad) - n_fft) // HOP
    window = numpy.hanning(n_fft).astype("float32")
    spec = numpy.empty((frames, n_fft // 2 + 1), dtype="float32")
    for a in range(0, frames, 2048):          # in blocks, to bound memory on long songs
        idx = numpy.arange(n_fft)[None, :] + HOP * numpy.arange(a, min(frames, a + 2048))[:, None]
        spec[a:a + len(idx)] = numpy.abs(numpy.fft.rfft(pad[idx] * window, axis=1))
    freqs = numpy.fft.rfftfreq(n_fft, 1 / SR)
    edges = numpy.geomspace(30, 11000, 25)
    flux = numpy.zeros(frames)
    for lo, hi in zip(edges, edges[1:]):
        band = (freqs >= lo) & (freqs < hi)
        if not band.any():
            continue
        e = numpy.log1p(100 * spec[:, band].mean(axis=1))
        weight = 1.0 / (1.0 + numpy.sqrt(lo * hi) / 400.0)
        flux[1:] += weight * numpy.maximum(0, numpy.diff(e))
    flux -= numpy.convolve(flux, numpy.ones(16) / 16, mode="same")   # local mean removed
    flux = numpy.maximum(flux, 0)
    return flux / (flux.std() + 1e-9)


def tempo(onset, lo=60, hi=200):
    import numpy
    x = onset - onset.mean()
    ac = numpy.correlate(x, x, mode="full")[len(x) - 1:]
    lags = numpy.arange(len(ac))
    valid = (lags >= FPS * 60 / hi) & (lags <= FPS * 60 / lo)
    bpm = numpy.where(lags > 0, 60 * FPS / numpy.maximum(lags, 1), 0)
    weight = numpy.exp(-0.5 * (numpy.log2(numpy.maximum(bpm, 1) / 120.0) / 0.9) ** 2)
    score = numpy.where(valid, ac * weight, -numpy.inf)
    lag = int(numpy.argmax(score))
    # Parabolic refinement of the peak for a fractional period.
    if 1 <= lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = a - 2 * b + c
        frac = 0.5 * (a - c) / denom if denom else 0.0
    else:
        frac = 0.0
    period = lag + max(-0.5, min(0.5, frac))
    return 60 * FPS / period, period


def beat_track(onset, period, tightness=100.0):
    """Dynamic-programming beat tracker. -> beat frame indices."""
    import numpy
    n = len(onset)
    score = onset.astype("float64").copy()
    back = -numpy.ones(n, dtype=int)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    for t in range(lo, n):
        p = numpy.arange(max(0, t - hi), t - lo + 1)
        if len(p) == 0:
            continue
        penalty = -tightness * numpy.log((t - p) / period) ** 2
        cand = score[p] + penalty
        k = int(numpy.argmax(cand))
        if cand[k] > 0:
            score[t] += cand[k]
            back[t] = p[k]
    end_region = numpy.arange(max(0, n - int(period)), n)
    t = int(end_region[numpy.argmax(score[end_region])])
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    return beats[::-1]


def analyse(path):
    """-> {"tempo", "beats": [s], "bars": [s], "duration"}."""
    import numpy
    samples = _load(path)
    onset = onset_strength(samples)
    bpm, period = tempo(onset)
    beats = beat_track(onset, period)
    beat_times = [round(b / FPS, 3) for b in beats]
    # Bar starts: the phase (of 4) whose beats carry the most onset energy.
    phase = max(range(4), key=lambda k: float(numpy.sum(onset[beats[k::4]])) if beats[k::4] else 0)
    return {"tempo": round(float(bpm), 2), "beats": beat_times, "bars": beat_times[phase::4],
            "duration": round(len(samples) / SR, 3)}


def read_lyrics(path):
    """Lyric sheet (plain text, or a PDF exported from a Google Doc) -> list of lines.
    [Section] lines and blank lines are skipped."""
    import re
    if path.lower().endswith(".pdf"):
        from .screenplay import pdf_to_text
        text = pdf_to_text(path)
    else:
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
    lines = []
    for raw in text.splitlines():
        line = raw.strip().replace("\u200b", "")
        if not line or re.fullmatch(r"\\?\[.*\\?\]|\(.*\)", line):
            continue
        lines.append(line)
    return lines


def _snap(t, grid):
    """Last grid time at or before t (a cut lands on the beat before the word)."""
    import bisect
    k = bisect.bisect_right(grid, t + 0.02) - 1
    return grid[max(0, k)] if grid else t


def build(song, clips, groups, visuals, lyrics=None, transcript=None, *, bars_per_shot=2,
          fps=None, title=None):
    """-> editing script dict (mode "music"). song: probe dict of the track. clips: probe
    dicts of the footage. groups: look-alike groups (lists of paths). lyrics: list of lines."""
    from . import editing_script, match
    beat = analyse(song["path"])
    beats, bars, duration = beat["beats"], beat["bars"], beat["duration"]
    grid = sorted(set([0.0] + beats + [duration]))

    # Lyric line start times, by matching the song's words to the sheet.
    starts = []
    if lyrics and transcript and transcript.get("words"):
        lines = [{"id": "L{:d}".format(k + 1), "scene": "S1", "text": t} for k, t in enumerate(lyrics)]
        index = match.ScriptIndex(lines)
        hits = match.match_take(transcript["words"], index, min_coverage=0.3, min_words=2, passes=6)["hits"]
        seen = set()
        for h in sorted(hits, key=lambda h: h["in"]):
            if h["line_id"] in seen and starts and h["in"] - starts[-1][0] < 1.0:
                continue
            seen.add(h["line_id"])
            starts.append((h["in"], lyrics[h["line"]], h["out"]))
    # Section boundaries.
    bounds, labels = [0.0], ["INTRO"]
    for t, text, _end in starts:
        b = _snap(t - 0.05, grid)
        if b - bounds[-1] >= 0.4:
            bounds.append(b)
            labels.append(text)
        else:
            labels[-1] = text if labels[-1] in ("INTRO",) else labels[-1] + " / " + text
    bounds.append(duration)
    # Long stretches (instrumental, or no lyrics at all) cut every few bars.
    bar_len = (60.0 / beat["tempo"]) * 4 if beat["tempo"] else 2.0
    sections = []
    for (a, b), label in zip(zip(bounds, bounds[1:]), labels):
        inner = [x for x in bars if a + bars_per_shot * bar_len * 0.75 < x < b - bar_len * 0.5]
        cuts = [a]
        for x in inner:
            if x - cuts[-1] >= bars_per_shot * bar_len * 0.9:
                cuts.append(x)
        for k, (x, y) in enumerate(zip(cuts, cuts[1:] + [b])):
            sections.append((x, y, label if k == 0 else label + " (cont.)"))

    # Media and pictures.
    pictures = [c for c in clips if c["kind"] in ("video", "image") and not c.get("error")]
    doc = editing_script.build({"title": title, "source": None, "scenes": [], "characters": []},
                               [song] + pictures, {}, {}, groups, visuals, fps=fps)
    ids = {m["path"]: cid for cid, m in doc["media"].items()}
    song_id = ids[song["path"]]
    order = _deal(doc, groups, ids)
    usage = {}
    elements = []
    for k, (a, b, label) in enumerate(sections):
        length = b - a
        options = []
        for n in range(len(order)):
            cid = order[(k + n) % len(order)]
            options.append(_picture(doc, cid, length, usage, reserve=(n == 0)))
        el = {"id": "S1-{:d}".format(k + 1), "type": "action", "music": True,
              "text": "[{:d}:{:05.2f}] {:s}".format(int(a // 60), a % 60, label),
              "cut": {"clip": song_id, "in": round(a, 3), "out": round(b, 3), "from": "song"},
              "cut_locked": True, "take": 0 if options else None, "takes": options,
              "cover": options[0] if options else None}
        elements.append(el)
    doc["scenes"] = [{"id": "S1", "number": None, "heading": "MUSIC: " + song["name"],
                      "int_ext": None, "location": None, "time": None, "group": None,
                      "elements": elements}]
    doc["mode"] = "music"
    doc["music"] = {"song": song_id, "tempo": beat["tempo"], "beats": beats, "bars": bars,
                    "lyrics": bool(lyrics), "lines_timed": len(starts)}
    doc["unplaced"] = [c for c in doc["media"] if c != song_id and not any(
        (el.get("cover") or {}).get("clip") == c for el in elements)]
    return doc


def _deal(doc, groups, ids):
    """Clip order that alternates between look-alike groups (neighbouring shots differ)."""
    pools = [[ids[p] for p in g if p in ids and doc["media"][ids[p]]["kind"] in ("video", "image")]
             for g in groups or []]
    grouped = {c for p in pools for c in p}
    pools += [[c] for c, m in doc["media"].items() if m["kind"] in ("video", "image") and c not in grouped]
    pools = [p for p in pools if p]
    order = []
    while any(pools):
        for p in pools:
            if p:
                order.append(p.pop(0))
    return order


def _picture(doc, cid, length, usage, reserve):
    """A stretch of a clip `length` long, preferring its longest shot and unused time."""
    m = doc["media"][cid]
    if m["kind"] == "image":
        return {"clip": cid, "in": 0.0, "out": round(length, 3), "score": 0.5, "kind": "cover"}
    dur = m.get("duration") or length
    shots = sorted(m.get("shots") or [{"start": 0.0, "end": dur}], key=lambda s: s["start"] - s["end"])
    start = usage.get(cid, None)
    if start is None or start + length > dur:
        start = shots[0]["start"] if shots[0]["end"] - shots[0]["start"] >= length else 0.0
    start = max(0.0, min(start, dur - length)) if dur > length else 0.0
    if reserve:
        usage[cid] = start + length
    return {"clip": cid, "in": round(start, 3), "out": round(start + min(length, dur), 3),
            "score": 0.5, "kind": "cover"}
