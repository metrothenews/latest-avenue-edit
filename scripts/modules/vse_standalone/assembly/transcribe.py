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

DEFAULT_MODEL = os.environ.get("VSE_ASSEMBLY_WHISPER", "base.en")
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


def _model(name, models_dir):
    key = (name, models_dir)
    if key not in _models:
        from faster_whisper import WhisperModel
        _models[key] = WhisperModel(name, device="cpu", compute_type="int8",
                                    download_root=models_dir)
    return _models[key]


def _cache_path(path, model, cache_dir, track, prompt):
    st = os.stat(path)
    key = hashlib.sha1("{:s}|{:d}|{:f}|{!s}|{!s}|{!s}".format(
        os.path.abspath(path), st.st_size, st.st_mtime, model, track, prompt).encode()).hexdigest()[:12]
    safe_model = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(str(model).rstrip("/")))
    return os.path.join(cache_dir, "{:s}.{:s}.{:s}.words.json".format(
        os.path.basename(path), safe_model, key))


def transcribe(path, model=DEFAULT_MODEL, *, prompt=None, language="en", track=None,
               cache_dir=None, models_dir=None):
    """-> {"words": [{"text", "norm", "start", "end", "p"}], "text", "duration", "model"}."""
    cache = _cache_path(path, model, cache_dir, track, prompt) if cache_dir else None
    if cache and os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            return json.load(fh)
    samples = load_audio(path, track)
    duration = len(samples) / 16000.0
    words = []
    if duration > 0.2 and float(abs(samples).max()) > 1e-4:
        segments, _info = _model(model, models_dir).transcribe(
            samples, language=language, word_timestamps=True, beam_size=5,
            vad_filter=True, condition_on_previous_text=False, initial_prompt=prompt)
        for segment in segments:
            for w in segment.words or ():
                n = norm(w.word)
                if n:
                    words.append({"text": w.word.strip(), "norm": n,
                                  "start": round(float(w.start), 3), "end": round(float(w.end), 3),
                                  "p": round(float(w.probability), 3)})
    result = {"words": words, "text": " ".join(w["text"] for w in words),
              "duration": round(duration, 3), "model": str(model)}
    if cache:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False)
    return result
