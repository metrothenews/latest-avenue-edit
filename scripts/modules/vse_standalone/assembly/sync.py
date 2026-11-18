# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Sync separately recorded sound to the camera clips, in order of trust:

1. **Timecode**: the recorder's start (BWF ``time_reference``, or a ``timecode`` tag) against
   the camera clip's start timecode. Exact when both were jammed; checked by waveform when
   the camera has sound too.
2. **Waveform**: the camera's own (scratch) sound cross-correlated with the recorder's, on
   10 ms loudness envelopes (FFT), then refined to the sample on the raw sound around the
   peak. The peak must stand clearly above the rest.
3. **Clap / slate**: the sharpest loud transient near the start of both.

Skipped entirely when there are no separate sound files. Needs ffmpeg and numpy.

``offset`` everywhere means: recorder time = camera time + offset (seconds).
"""

import os

ENV_RATE = 100          # loudness envelope frames per second
RAW_RATE = 8000         # sample rate for refinement and clap search
MIN_PEAK_RATIO = 1.25   # best correlation peak / best peak elsewhere
MIN_CORR = 0.25         # normalised correlation of the envelopes at the peak
MIN_RAW = 0.3           # raw-sound correlation that proves it's the same recording
MIN_COVER = 0.6         # share of the camera clip the recorder must overlap


def _audio(path, rate, seconds=None):
    import numpy
    import subprocess
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", path]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, check=True, stdout=subprocess.PIPE).stdout
    return numpy.frombuffer(raw, dtype=numpy.float32)


def envelope(samples, rate):
    """Log loudness every 10 ms, mean removed: robust to the two microphones' levels."""
    import numpy
    hop = rate // ENV_RATE
    n = len(samples) // hop
    if n == 0:
        return numpy.zeros(0, dtype="float32")
    frames = samples[:n * hop].reshape(n, hop)
    env = numpy.log(numpy.sqrt((frames ** 2).mean(axis=1)) + 1e-4)
    # Onsets matter more than steady levels: high-pass the envelope a little.
    env = env - numpy.convolve(env, numpy.ones(50) / 50, mode="same")
    return (env - env.mean()) / (env.std() + 1e-9)


def _xcorr(a, b):
    """Full cross-correlation via FFT. -> lags (b shifted by lag lines up with a), values."""
    import numpy
    n = len(a) + len(b) - 1
    size = 1 << (n - 1).bit_length()
    fa = numpy.fft.rfft(a, size)
    fb = numpy.fft.rfft(b, size)
    c = numpy.fft.irfft(fa * numpy.conj(fb), size)
    c = numpy.concatenate([c[-(len(b) - 1):], c[:len(a)]]) if len(b) > 1 else c[:len(a)]
    lags = numpy.arange(-(len(b) - 1), len(a))
    return lags, c


def waveform_offset(camera, recorder):
    """camera, recorder: mono float arrays at RAW_RATE. -> (offset seconds, score) or None."""
    import numpy
    ec, er = envelope(camera, RAW_RATE), envelope(recorder, RAW_RATE)
    if len(ec) < 100 or len(er) < 100:
        return None
    lags, c = _xcorr(er, ec)          # recorder index = camera index + lag
    overlap = numpy.minimum(len(ec), numpy.minimum(len(er) - lags, len(ec) + lags)).clip(1)
    norm = c / numpy.maximum(overlap, min(len(ec), len(er)) * 0.5)
    # The recorder must cover most of the camera clip: a short sound file that happens to
    # resemble a few seconds of it (a voice-over by the same actor) is not its sound.
    norm = numpy.where(overlap >= MIN_COVER * len(ec), norm, -1.0)
    k = int(numpy.argmax(norm))
    peak = float(norm[k])
    mask = numpy.abs(lags - lags[k]) > 50          # elsewhere: more than 0.5 s away
    second = float(norm[mask].max()) if mask.any() else 0.0
    if peak < MIN_CORR or (second > 0 and peak / second < MIN_PEAK_RATIO):
        return None
    coarse = lags[k] / ENV_RATE
    offset = _refine(camera, recorder, coarse)
    # Same recording, or just similar-sounding speech? The raw sound of the same event lines
    # up sample for sample; two different takes don't.
    match = raw_match(camera, recorder, offset)
    if match < MIN_RAW:
        return None
    return offset, round(match, 3)


def raw_match(camera, recorder, offset):
    """Normalised correlation of the raw sound over the overlap at an offset (0..1)."""
    import numpy
    start = int(round(offset * RAW_RATE))
    a0, b0 = max(0, -start), max(0, start)
    n = min(len(camera) - a0, len(recorder) - b0)
    if n < RAW_RATE:
        return 0.0
    a, b = camera[a0:a0 + n], recorder[b0:b0 + n]
    return float(abs(numpy.dot(a, b)) / (numpy.linalg.norm(a) * numpy.linalg.norm(b) + 1e-9))


def _refine(camera, recorder, coarse, window=0.03):
    """Sample-accurate offset near a coarse one, on the raw sound (loudest 4 s of the camera)."""
    import numpy
    seg = 4 * RAW_RATE
    if len(camera) <= seg:
        start = 0
    else:
        energy = numpy.convolve(camera ** 2, numpy.ones(RAW_RATE // 10), mode="valid")[::RAW_RATE // 10]
        start = int(numpy.argmax(numpy.convolve(energy, numpy.ones(40), mode="valid"))) * (RAW_RATE // 10)
        start = min(start, len(camera) - seg)
    a = camera[start:start + seg]
    w = int(window * RAW_RATE)
    base = start + int(round(coarse * RAW_RATE))
    best, best_lag = -1e18, 0
    for lag in range(-w, w + 1):
        lo = base + lag
        if lo < 0 or lo + len(a) > len(recorder):
            continue
        v = float(numpy.dot(a, recorder[lo:lo + len(a)]))
        if v > best:
            best, best_lag = v, lag
    return round((base + best_lag - start) / RAW_RATE, 4)


def clap_time(samples, search=20.0):
    """Time of a clap (slate sticks, hands) in the first `search` seconds, or None.

    A clap is a sudden, very short, very loud burst: it rises sharply, stands well above
    everything else in the stretch (speech after a pause also rises sharply, but isn't much
    louder than the rest of the speech), and is gone within about 60 ms.
    """
    import numpy
    x = samples[:int(search * RAW_RATE)]
    hop = RAW_RATE // 200                               # 5 ms
    n = len(x) // hop
    if n < 20:
        return None
    rms = numpy.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-6
    rise = numpy.log(rms[1:]) - numpy.log(rms[:-1])
    k = int(numpy.argmax(rise * (rms[1:] / rms.max())))
    peak_frame = k + 1 + int(numpy.argmax(rms[k + 1:k + 5]))
    peak = rms[peak_frame]
    loud_rest = float(numpy.percentile(rms, 95))
    after = rms[peak_frame + 12:peak_frame + 16]        # 60-80 ms later
    if rise[k] < 1.5 or peak < 1.5 * loud_rest or (len(after) and after.max() > 0.5 * peak):
        return None
    return (k + 1) * hop / RAW_RATE


def recorder_start(info):
    """Recorder start in seconds since midnight, from BWF time_reference or a timecode tag."""
    if info.get("time_reference_seconds") is not None:
        return info["time_reference_seconds"]
    return info.get("timecode_seconds")


def sync(clips, log=None):
    """Pair every separate sound file with the camera clips it covers.

    clips: probe dicts. -> ({video path: {"audio", "offset", "method", "score"}}, [sound files
    left unsynced]). A sound file may cover several clips (a recorder left rolling), never
    two at once; a clip gets at most one sound file.
    """
    videos = [c for c in clips if c["kind"] == "video" and not c.get("error")]
    sounds = [c for c in clips if c["kind"] == "audio" and not c.get("error")]
    result = {}
    if not sounds or not videos:
        return result, [s["path"] for s in sounds]
    cache = {}

    def raw(path):
        if path not in cache:
            cache[path] = _audio(path, RAW_RATE)
        return cache[path]

    used = {s["path"]: [] for s in sounds}       # (start, end) in recorder time already taken

    def free(snd, offset, vid):
        a, b = offset, offset + (vid.get("duration") or 0)
        return all(b <= x or a >= y for x, y in used[snd["path"]])

    def take(vid, snd, offset, method, score):
        result[vid["path"]] = {"audio": snd["path"], "offset": round(offset, 4),
                               "method": method, "score": score}
        used[snd["path"]].append((offset, offset + (vid.get("duration") or 0)))

    # 1. Timecode (checked by waveform when the camera has sound).
    for snd in sounds:
        s0 = recorder_start(snd)
        if s0 is None:
            continue
        for vid in videos:
            v0, vdur = vid.get("timecode_seconds"), vid.get("duration") or 0
            if vid["path"] in result or v0 is None:
                continue
            offset = v0 - s0
            if not (-0.5 < offset < (snd.get("duration") or 0) and offset + vdur > 0):
                continue
            method, score = "timecode", 1.0
            if vid.get("audio_tracks"):
                check = waveform_offset(raw(vid["path"]), raw(snd["path"]))
                if check and abs(check[0] - offset) > 0.1:
                    offset, method, score = check[0], "waveform (timecode was off)", check[1]
            if free(snd, offset, vid):
                take(vid, snd, offset, method, score)

    # 2. Waveform: every remaining pair scored, best pairs assigned first.
    pairs = []
    for snd in sounds:
        for vid in videos:
            if vid["path"] in result or not vid.get("audio_tracks"):
                continue
            w = waveform_offset(raw(vid["path"]), raw(snd["path"]))
            if w:
                pairs.append((w[1], w[0], vid, snd))
    for score, offset, vid, snd in sorted(pairs, key=lambda p: -p[0]):
        if vid["path"] not in result and free(snd, offset, vid):
            take(vid, snd, offset, "waveform", score)

    # 3. Clap: a sound file still unused, and the clip of most similar length with a clap.
    for snd in sounds:
        if used[snd["path"]]:
            continue
        st = clap_time(raw(snd["path"]))
        if st is None:
            continue
        options = []
        for vid in videos:
            if vid["path"] in result or not vid.get("audio_tracks"):
                continue
            vt = clap_time(raw(vid["path"]))
            if vt is not None:
                ratio = (vid.get("duration") or 1) / max(0.1, snd.get("duration") or 1)
                options.append((abs(1 - ratio), vt, vid))
        if options:
            _r, vt, vid = min(options, key=lambda o: o[0])
            take(vid, snd, st - vt, "clap", 0.5)

    unsynced = [s["path"] for s in sounds if not used[s["path"]]]
    if log:
        for snd in sounds:
            matched = ["{:s} ({:s}, {:+.3f}s)".format(os.path.basename(v), r["method"], r["offset"])
                       for v, r in result.items() if r["audio"] == snd["path"]]
            log("sound {:s}: {:s}".format(snd["name"], ", ".join(matched) or "not synced"))
    return result, unsynced
