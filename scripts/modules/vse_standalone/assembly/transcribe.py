# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Speech to words with times: ffmpeg pulls the audio, faster-whisper (a small Whisper model,
int8 on the CPU) transcribes it with word timestamps.

Default model ``base.en`` (74 M parameters, ~140 MB on disk, a few times faster than real time
on 4 CPU cores). ``tiny.en`` is the fastest (39 M) and good enough on clean studio sound;
``small.en`` (244 M) for noisy location sound. Any folder holding a CTranslate2 model works
too, e.g. a fine-tuned one.

Results are cached next to the project (``<cache>/<file>.<model>.words.json``), keyed on the
file's size and modification time, so a re-run of the assembly costs nothing.
"""

import hashlib
import json
import os
import re
import subprocess

DEFAULT_MODEL = os.environ.get("VSE_ASSEMBLY_WHISPER")   # None: chosen by default_model()


def default_model():
    """small.en on an NVIDIA card (fits easily in 8 GB, clearly better on location sound),
    base.en on the processor; VSE_ASSEMBLY_WHISPER overrides."""
    return DEFAULT_MODEL or ("small.en" if device() == "cuda" else "base.en")
_models = {}


def norm(word):
    """Matching form of a word: lower case letters and digits."""
    return re.sub(r"[^a-z0-9']", "", word.lower().replace("’", "'")).strip("'")


def load_audio(path, track=None, rate=16000):
    """-> float32 numpy array, mono, ``rate`` Hz. ``track``: audio stream index (0 = first)."""
    import numpy
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", path]
    if track is not None:
        cmd += ["-map", "0:a:{:d}".format(track)]
    cmd += ["-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, check=True, stdout=subprocess.PIPE).stdout
    return numpy.frombuffer(raw, dtype=numpy.float32)


def device():
    """"cuda" when ctranslate2 sees an NVIDIA GPU (and VSE_ASSEMBLY_DEVICE doesn't say "cpu")."""
    wanted = os.environ.get("VSE_ASSEMBLY_DEVICE", "auto").lower()
    if wanted in ("cpu", "cuda"):
        return wanted
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def _model(name, models_dir, on=None):
    on = on or device()
    key = (name, models_dir, on)
    if key not in _models:
        from faster_whisper import WhisperModel
        _models[key] = WhisperModel(name, device=on, compute_type="int8_float16" if on == "cuda" else "int8",
                                    download_root=models_dir)
    return _models[key]


def _run(samples, model, models_dir, **kwargs):
    """Transcribe on the GPU if there is one; on any CUDA failure (missing cuBLAS/cuDNN
    libraries are common on Windows) fall back to the CPU for the rest of the run."""
    global _cuda_failed
    on = "cpu" if _cuda_failed else device()
    try:
        segments, info = _model(model, models_dir, on).transcribe(samples, **kwargs)
        return list(segments), info
    except (RuntimeError, OSError) as ex:
        if on != "cuda":
            raise
        _cuda_failed = True
        import sys
        print("note: GPU transcription failed ({:s}); using the CPU".format(str(ex)[:200]), file=sys.stderr)
        segments, info = _model(model, models_dir, "cpu").transcribe(samples, **kwargs)
        return list(segments), info


_cuda_failed = False


def _cache_path(path, model, cache_dir, track, prompt):
    st = os.stat(path)
    key = hashlib.sha1("{:s}|{:d}|{:f}|{!s}|{!s}|{!s}".format(
        os.path.abspath(path), st.st_size, st.st_mtime, model, track, prompt).encode()).hexdigest()[:12]
    safe_model = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(str(model).rstrip("/")))
    return os.path.join(cache_dir, "{:s}.{:s}.{:s}.words.json".format(
        os.path.basename(path), safe_model, key))


def tighten(words, samples, rate=16000, hop=0.01, min_len=0.08):
    """Trim each word's start and end to where there is sound.

    Whisper's word times often stretch over the pause before a word (seen on clean recorder
    sound with no punctuation): lines then start early and pauses between lines vanish. A 10 ms
    loudness envelope finds the silence; a word keeps at least `min_len` seconds.
    """
    import numpy
    n = int(hop * rate)
    frames = len(samples) // n
    if frames < 10 or not words:
        return words
    env = numpy.sqrt((samples[:frames * n].reshape(frames, n) ** 2).mean(axis=1))
    floor = float(numpy.percentile(env, 10))
    speech = float(numpy.percentile(env, 90))
    if speech < floor * 4:          # no clear silence (music, constant noise): leave as is
        return words
    loud = env > floor + 0.1 * (speech - floor)
    gap = int(0.25 / hop)
    for w in words:
        a, b = int(w["start"] / hop), min(frames, int(w["end"] / hop) + 1)
        # A word holds no long silence: one inside the span means the word starts after it
        # (the end of the previous word leaked in) or ends before it.
        quiet = numpy.concatenate([[False], ~loud[a:b], [False]]).astype(int)
        edges = numpy.diff(quiet)
        runs = list(zip(numpy.nonzero(edges == 1)[0], numpy.nonzero(edges == -1)[0]))
        long_runs = [(x, y) for x, y in runs if y - x >= gap]
        if long_runs:
            x, y = long_runs[-1]
            if y < b - a:
                a = a + y           # after the last long silence
            else:
                b = a + x           # the silence runs to the end: stop before it
        idx = numpy.nonzero(loud[a:b])[0]
        if len(idx) == 0:
            continue
        start, end = float((a + idx[0]) * hop), float((a + idx[-1] + 1) * hop)
        if end - start < min_len:
            end = min(w["end"], start + min_len)
        w["start"], w["end"] = round(max(w["start"], start), 3), round(min(w["end"], end), 3)
    return words


def transcribe(path, model=None, *, prompt=None, language="en", track=None,
               cache_dir=None, models_dir=None):
    """-> {"words": [{"text", "norm", "start", "end", "p"}], "text", "duration", "model"}."""
    model = model or default_model()
    cache = _cache_path(path, model, cache_dir, track, prompt) if cache_dir else None
    if cache and os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            return json.load(fh)
    samples = load_audio(path, track)
    duration = len(samples) / 16000.0
    words = []
    if duration > 0.2 and float(abs(samples).max()) > 1e-4:
        segments, _info = _run(
            samples, model, models_dir, language=language, word_timestamps=True, beam_size=5,
            vad_filter=True, condition_on_previous_text=False, initial_prompt=prompt)
        for segment in segments:
            for w in segment.words or ():
                n = norm(w.word)
                if n:
                    words.append({"text": w.word.strip(), "norm": n,
                                  "start": round(float(w.start), 3), "end": round(float(w.end), 3),
                                  "p": round(float(w.probability), 3)})
    tighten(words, samples)
    result = {"words": words, "text": " ".join(w["text"] for w in words),
              "duration": round(duration, 3), "model": str(model)}
    if cache:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False)
    return result
