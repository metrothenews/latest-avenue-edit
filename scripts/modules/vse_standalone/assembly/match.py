# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Match what was said in each take to the script's dialogue lines.

1. **Which scene.** The take's words are compared with each scene's vocabulary (words weighted
   by how rare they are in the script), so the expensive step only looks at a few scenes.
2. **Which lines, and where.** A sequence alignment (Smith-Waterman style, word by word, with
   fuzzy word similarity from rapidfuzz) runs the take's words against those scenes' dialogue.
   Skipping whole script lines is free (a close-up only covers one character's lines), skipping
   words inside a line costs, and extra spoken words (the slate, "action", ad-libs) cost a
   little. A take that repeats lines (a pickup) is aligned again on what is left over.
3. **How good.** Each covered line gets in/out times from its first and last matched word and
   a score from coverage (how much of the line was said), accuracy (how close the words were)
   and the recognizer's confidence.

Works without rapidfuzz (``difflib`` instead, slower). No model.
"""

import collections
import difflib
import math
import re

try:
    from rapidfuzz.distance import Levenshtein as _lev

    def word_similarity(a, b):
        return 1.0 if a == b else _lev.normalized_similarity(a, b)
except ImportError:  # pragma: no cover
    def word_similarity(a, b):
        return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
         "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _number_words(n):
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        return [_TENS[n // 10]] + ([_ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        rest = _number_words(n % 100) if n % 100 else []
        return [_ONES[n // 100], "hundred"] + rest
    return [str(n)]


CONTRACTIONS = {"it's": "it is", "there's": "there is", "that's": "that is", "what's": "what is",
                "he's": "he is", "she's": "she is", "i'm": "i am", "you're": "you are",
                "we're": "we are", "they're": "they are", "can't": "can not", "won't": "will not",
                "don't": "do not", "doesn't": "does not", "didn't": "did not", "isn't": "is not",
                "aren't": "are not", "wasn't": "was not", "i'll": "i will", "you'll": "you will",
                "we'll": "we will", "i've": "i have", "you've": "you have", "we've": "we have",
                "i'd": "i would", "let's": "let us", "gonna": "going to", "wanna": "want to"}


def tokens(text):
    """Script or heard text -> matching tokens (lower case, digits spelled out, contractions
    expanded, so "It's" and "It is" match)."""
    out = []
    for raw in re.findall(r"[A-Za-z0-9'’]+", text):
        word = raw.lower().replace("’", "'").strip("'")
        if word.isdigit() and len(word) <= 3:
            out += _number_words(int(word))
        elif word in CONTRACTIONS:
            out += CONTRACTIONS[word].split()
        elif word:
            out.append(word.replace("'", ""))
    return out


def heard_tokens(words):
    """Recognizer words -> (token, word index) pairs (a word may give several tokens)."""
    out = []
    for i, w in enumerate(words):
        for t in tokens(w["text"]):
            out.append((t, i))
    return out


# -----------------------------------------------------------------------------
# Step 1: which scenes.

class ScriptIndex:
    def __init__(self, lines):
        """lines: dialogue elements in script order (``screenplay.dialogue_lines``)."""
        self.lines = lines
        self.tokens = []            # (token, line index, position in line)
        self.line_tokens = []
        self.scene_of_line = []
        self.scene_vocab = collections.defaultdict(collections.Counter)
        doc_freq = collections.Counter()
        for li, line in enumerate(lines):
            toks = tokens(line.get("spoken") or line["text"])
            self.line_tokens.append(toks)
            self.scene_of_line.append(line["scene"])
            for pi, t in enumerate(toks):
                self.tokens.append((t, li, pi))
            self.scene_vocab[line["scene"]].update(toks)
        for vocab in self.scene_vocab.values():
            doc_freq.update(vocab.keys())
        n = max(1, len(self.scene_vocab))
        self.idf = {t: math.log(1 + n / c) for t, c in doc_freq.items()}
        self.scenes = list(dict.fromkeys(self.scene_of_line))

    def rank_scenes(self, heard, limit=3):
        """-> [(scene id, score 0..1)] best first, for a list of heard tokens."""
        heard_set = set(heard)
        if not heard_set:
            return []
        scored = []
        for scene, vocab in self.scene_vocab.items():
            weight = sum(self.idf.get(t, 0) for t in vocab)
            hit = sum(self.idf.get(t, 0) for t in vocab if t in heard_set)
            # Heard words that exist in this scene, against everything heard.
            heard_hit = sum(1 for t in heard if t in vocab) / len(heard)
            if weight:
                scored.append((scene, round(0.5 * hit / weight + 0.5 * heard_hit, 3)))
        scored.sort(key=lambda s: -s[1])
        best = scored[0][1] if scored else 0
        return [s for s in scored[:limit] if s[1] >= max(0.15, best * 0.5)]


# -----------------------------------------------------------------------------
# Step 2: alignment.

MATCH_MIN = 0.6         # words less similar than this count as a mismatch
SKIP_HEARD = -0.4       # an extra spoken word
SKIP_SCRIPT = -0.9      # a script word inside a line that wasn't said
MISMATCH = -1.0
UTTERANCE_GAP = 1.0     # seconds of silence that end an utterance (a sentence pause is ~0.7)


def _pair_score(sim):
    return 2.0 * sim if sim >= MATCH_MIN else MISMATCH


def align(heard, script_tokens):
    """Local alignment of heard tokens against script tokens with free line skips.

    heard: list of (token, word index). script_tokens: list of (token, line, position).
    -> list of (heard index, script index, similarity) pairs, in order, or [].
    """
    n, m = len(heard), len(script_tokens)
    if not n or not m:
        return []
    is_start = [script_tokens[j][2] == 0 for j in range(m)]
    is_end = [j == m - 1 or script_tokens[j + 1][2] == 0 for j in range(m)]
    sims = {}

    def sim(i, j):
        key = (heard[i][0], script_tokens[j][0])
        s = sims.get(key)
        if s is None:
            s = word_similarity(*key)
            if s < 1.0 and min(len(key[0]), len(key[1])) <= 3:
                s = min(s, MATCH_MIN - 0.01)    # "cut" is not "but": short words match exactly
            s = sims[key] = s
        return s

    merges = {}

    def merged(i, j):
        key = (heard[i][0] + heard[i + 1][0], script_tokens[j][0])
        v = merges.get(key)
        if v is None:
            v = merges[key] = word_similarity(*key) if len(key[1]) >= 3 else 0.0
        return v

    # score[i][j]: best alignment ending with heard[i-1] and script[j-1] consumed.
    neg = -1e9
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    best = (0.0, 0, 0)
    for i in range(1, n + 1):
        row, prev = score[i], score[i - 1]
        brow = back[i]
        # Best score at row i-1 ending at a line end, for free jumps to any later line start.
        best_end_prev = (neg, None)
        for j in range(1, m + 1):
            jj = j - 1
            candidates = [(0.0, None)]
            pair = _pair_score(sim(i - 1, jj))
            candidates.append((prev[j - 1] + pair, (i - 1, j - 1, "pair")))
            if is_start[jj] and best_end_prev[1] is not None:
                candidates.append((best_end_prev[0] + pair, (i - 1, best_end_prev[1], "jump")))
            if i >= 2:
                # Two heard tokens for one script word ("and go" for "ago", "every one").
                ms = merged(i - 2, jj)
                if ms >= MATCH_MIN:
                    candidates.append((score[i - 2][j - 1] + _pair_score(ms) - 0.2, (i - 2, j - 1, "merge")))
            candidates.append((prev[j] + SKIP_HEARD, (i - 1, j, "heard")))
            candidates.append((row[j - 1] + SKIP_SCRIPT, (i, j - 1, "script")))
            value, ptr = max(candidates, key=lambda c: c[0])
            row[j], brow[j] = value, ptr
            if is_end[jj] and prev[j] > best_end_prev[0]:
                best_end_prev = (prev[j], j)
            if value > best[0]:
                best = (value, i, j)
    # Trace back.
    pairs = []
    i, j = best[1], best[2]
    while i > 0 and j > 0 and back[i][j] is not None and score[i][j] > 0:
        pi, pj, kind = back[i][j]
        if kind == "merge":
            ms = merged(i - 2, j - 1)
            pairs += [(i - 1, j - 1, ms), (i - 2, j - 1, ms)]
        elif kind in ("pair", "jump"):
            s = sim(i - 1, j - 1)
            if s >= MATCH_MIN:
                pairs.append((i - 1, j - 1, s))
        i, j = pi, pj
    pairs.reverse()
    return pairs


# -----------------------------------------------------------------------------
# Step 3: per-line results.

def _line_hits(pairs, heard, script_tokens, index, words):
    by_line = collections.defaultdict(list)
    for hi, sj, s in pairs:
        _t, line, pos = script_tokens[sj]
        by_line[line].append((hi, pos, s))
    word_line = {}
    for line, items in by_line.items():
        for hi, _pos, _s in items:
            word_line[heard[hi][1]] = line
    hits = []
    for line, items in by_line.items():
        n_tokens = len(index.line_tokens[line])
        positions = {pos for _hi, pos, _s in items}
        coverage = len(positions) / max(1, n_tokens)
        accuracy = sum(s for _hi, _pos, s in items) / len(items)
        first_word = heard[items[0][0]][1]
        last_word = heard[items[-1][0]][1]
        # A reworded ending ("the coffee is terrible") can't align in order: the line runs on
        # to the end of the utterance, unless those words belong to another line.
        while (last_word + 1 < len(words) and word_line.get(last_word + 1, line) == line
               and words[last_word + 1]["start"] - words[last_word]["end"] < UTTERANCE_GAP):
            last_word += 1
        span = words[first_word:last_word + 1]
        said = [w for w in span]
        confidence = sum(w.get("p", 1.0) for w in said) / max(1, len(said))
        extra = max(0, len(heard_tokens(said)) - len(items))
        score = coverage * accuracy * (0.7 + 0.3 * confidence) - 0.03 * extra
        hits.append({
            "line": line,
            "line_id": index.lines[line]["id"],
            "in": span[0]["start"],
            "out": span[-1]["end"],
            "first_word": first_word,
            "last_word": last_word,
            "heard": " ".join(w["text"] for w in said),
            "matched": len(positions),
            "coverage": round(coverage, 3),
            "accuracy": round(accuracy, 3),
            "confidence": round(confidence, 3),
            "score": round(max(0.0, score), 3),
        })
    return hits


def match_take(words, index, *, min_coverage=0.3, min_words=3, passes=3):
    """Recognizer words of one take -> {"scenes": [...], "hits": [...per line...]}."""
    heard = heard_tokens(words)
    scenes = index.rank_scenes([t for t, _ in heard])
    if not scenes:
        return {"scenes": [], "hits": []}
    wanted = {s for s, _ in scenes}
    script_tokens = [t for t in index.tokens if index.scene_of_line[t[1]] in wanted]
    hits = []
    remaining = list(range(len(heard)))
    for _ in range(passes):
        sub = [heard[k] for k in remaining]
        pairs = align(sub, script_tokens)
        if len(pairs) < 2:
            break
        # Map back to indices into `heard`.
        mapped = [(remaining[hi], sj, s) for hi, sj, s in pairs]
        new = [h for h in _line_hits(mapped, heard, script_tokens, index, words)
               if h["coverage"] >= min_coverage
               and h["matched"] >= min(min_words, len(index.line_tokens[h["line"]]))]
        if not new:
            break
        hits += new
        used = {hi for hi, _sj, _s in mapped}
        lo, hi_ = min(used), max(used)
        remaining = [k for k in remaining if k < lo or k > hi_]
        if len(remaining) < 3:
            break
    hits.sort(key=lambda h: h["in"])
    return {"scenes": [{"id": s, "score": sc} for s, sc in scenes], "hits": hits}
