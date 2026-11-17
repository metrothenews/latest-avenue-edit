# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Forced alignment: given an audio clip and the exact words in it, find when each word starts
and ends. Used by ``align_lyrics.py`` to refine the speech recognizer's word times.

A recognizer's word times are a by-product of working out *what* was said. Here the words are
already known (from the lyric sheet), so an acoustic model only has to place them: it scores
every 20 ms of audio against every letter, and the best path through the known letter
sequence gives each word's first and last frame (CTC forced alignment, Viterbi).

Model: wav2vec 2.0 base, fine-tuned on 960 h of LibriSpeech (facebook/wav2vec2-base-960h,
Apache-2.0 licence), in ONNX form so it runs on onnxruntime, which the recognizer already
installs. No PyTorch needed. About 380 MB, downloaded once into the models folder.
"""

import json
import os
import re
import subprocess

MODEL_REPO = "Xenova/wav2vec2-base-960h"
MODEL_FILE = "onnx/model.onnx"
SAMPLE_RATE = 16000
STRIDE = 320  # samples per output frame (20 ms)


def load_audio(path):
    import numpy
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        check=True, stdout=subprocess.PIPE).stdout
    return numpy.frombuffer(raw, dtype=numpy.float32)


def load_model(models_dir=None):
    """-> (onnxruntime session, {letter: index}). Downloads the model on first use."""
    import onnxruntime
    from huggingface_hub import hf_hub_download
    model = hf_hub_download(MODEL_REPO, MODEL_FILE, cache_dir=models_dir)
    vocab_path = hf_hub_download(MODEL_REPO, "vocab.json", cache_dir=models_dir)
    with open(vocab_path, encoding="utf-8") as fh:
        vocab = json.load(fh)
    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3
    session = onnxruntime.InferenceSession(model, options, providers=["CPUExecutionProvider"])
    return session, vocab


def emissions(session, samples):
    """Log-probability of every letter for every 20 ms frame: array [frames, letters]."""
    import numpy
    x = samples.astype(numpy.float32)
    x = (x - x.mean()) / (x.std() + 1e-7)
    logits = session.run(None, {session.get_inputs()[0].name: x[None, :]})[0][0]
    logits = logits - logits.max(axis=1, keepdims=True)
    return logits - numpy.log(numpy.exp(logits).sum(axis=1, keepdims=True))


def spell(word):
    """A word as the model's letters: capitals and apostrophes only."""
    word = word.upper().replace("’", "'").replace("IN'", "ING")
    return re.sub(r"[^A-Z']", "", word).strip("'")


def viterbi(log_probs, tokens, blank):
    """Best path of `tokens` through the frames, blanks allowed between tokens.

    :return: For every token, (first frame, last frame), or None when the audio is too short.
    """
    import numpy
    frames = log_probs.shape[0]
    # States: blank, token 0, blank, token 1, ..., blank.
    states = [blank]
    for token in tokens:
        states += [token, blank]
    count = len(states)
    if frames < len(tokens):
        return None
    emit = log_probs[:, states]                       # [frames, states]
    neg = -1e30
    score = numpy.full((frames, count), neg)
    back = numpy.zeros((frames, count), dtype=numpy.int8)
    score[0, 0] = emit[0, 0]
    score[0, 1] = emit[0, 1]
    # A token may be entered from two states back only when that skips a blank between two
    # different letters.
    can_skip = numpy.zeros(count, dtype=bool)
    for s in range(2, count):
        can_skip[s] = states[s] != blank and states[s] != states[s - 2]
    for t in range(1, frames):
        stay = score[t - 1]
        step = numpy.concatenate(([neg], score[t - 1, :-1]))
        skip = numpy.concatenate(([neg, neg], score[t - 1, :-2]))
        skip = numpy.where(can_skip, skip, neg)
        best = numpy.maximum(numpy.maximum(stay, step), skip)
        back[t] = numpy.where(best == stay, 0, numpy.where(best == step, 1, 2))
        score[t] = best + emit[t]
    s = count - 1 if score[-1, count - 1] >= score[-1, count - 2] else count - 2
    spans = [[None, None] for _ in tokens]
    for t in range(frames - 1, -1, -1):
        if s % 2 == 1:
            k = s // 2
            spans[k][0] = t
            if spans[k][1] is None:
                spans[k][1] = t
        s -= int(back[t, s])
    return [tuple(span) for span in spans]


def align_words(samples, words, session, vocab):
    """Times for `words` (a list of strings sung in this order in `samples`).

    :return: A list with, for every word, {"start", "end", "confidence"} in seconds, or None
       for a word with no letters the model knows.
    """
    import numpy
    log_probs = emissions(session, samples)
    blank = vocab["<pad>"]
    bar = vocab["|"]
    tokens, owner = [], []
    for index, word in enumerate(words):
        letters = [vocab[c] for c in spell(word) if c in vocab]
        if not letters:
            continue
        if tokens:
            tokens.append(bar)
            owner.append(None)
        tokens += letters
        owner += [index] * len(letters)
    spans = viterbi(log_probs, tokens, blank) if tokens else None
    result = [None] * len(words)
    if spans is None:
        return result
    for index in range(len(words)):
        mine = [k for k, o in enumerate(owner) if o == index]
        if not mine:
            continue
        first, last = spans[mine[0]][0], spans[mine[-1]][1]
        # How sure the model is of this word's letters where it placed them.
        probs = [float(numpy.exp(log_probs[spans[k][0]:spans[k][1] + 1, tokens[k]].max())) for k in mine]
        result[index] = {"start": first * STRIDE / SAMPLE_RATE,
                         "end": (last + 1) * STRIDE / SAMPLE_RATE,
                         "confidence": round(sum(probs) / len(probs), 3)}
    return result
