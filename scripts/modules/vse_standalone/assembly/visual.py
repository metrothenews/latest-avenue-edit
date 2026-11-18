# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Group clips that were shot in the same place (same sequence or set-up) by how they look.

* **Shots inside a clip:** ffmpeg's scene-change score (``select='gt(scene,T)'``).
* **Thumbnails:** one frame from the middle of each shot, written as small JPEGs (also what
  the site shows next to each take).
* **What a frame looks like**, two ways:

  - ``mobileclip`` (default when installed): MobileCLIP2-S0 through open_clip, an 11 M
    parameter image encoder that runs at about 100 frames a second on 4 CPU cores. Its
    embeddings put two angles of the same room close together even when the colours shift.
  - ``signature`` (no model, always available): a 4x4 grid of average colours plus a colour
    histogram. Good for "same set-up, same camera", weaker across angles.

* **Grouping:** average-linkage clustering on cosine similarity, stopped at a threshold.

Needs ffmpeg and numpy; open_clip/torch only for ``mobileclip``.
"""

import hashlib
import json
import os
import re
import subprocess

MODEL = os.environ.get("VSE_ASSEMBLY_IMAGE_MODEL", "MobileCLIP2-S0")
PRETRAINED = os.environ.get("VSE_ASSEMBLY_IMAGE_WEIGHTS", "dfndr2b")
# Same-group thresholds (cosine similarity of clip embeddings), per backend.
THRESHOLD = {"mobileclip": 0.80, "signature": 0.80}
_model = {}


def shots(path, duration, threshold=0.3, min_length=0.5):
    """Shot boundaries inside one video -> [(start, end)] in seconds."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-nostdin", "-i", path, "-an", "-vf",
         "scale=160:-2,select='gt(scene,{:.3f})',showinfo".format(threshold), "-f", "null", "-"],
        capture_output=True, text=True)
    cuts = [float(t) for t in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]
    edges = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    out = []
    for a, b in zip(edges, edges[1:]):
        if out and b - a < min_length:
            out[-1] = (out[-1][0], b)
        elif b > a:
            out.append((a, b))
    return out or [(0.0, duration)]


def thumbnail(path, time, out_path, width=320):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y"]
    if time is not None:
        cmd += ["-ss", "{:.3f}".format(time)]
    cmd += ["-i", path, "-frames:v", "1", "-vf", "scale={:d}:-2".format(width), "-q:v", "4",
            out_path]
    subprocess.run(cmd, check=True)
    return out_path


def _rgb(image_path, size):
    import numpy
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", image_path, "-vf",
         "scale={0:d}:{0:d}".format(size), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, stdout=subprocess.PIPE).stdout
    return numpy.frombuffer(raw, dtype=numpy.uint8).reshape(size, size, 3).astype("float32") / 255


def signature(image_path):
    """Model-free look of a frame: 4x4 average colours + 3x8-bin colour histogram."""
    import numpy
    img = _rgb(image_path, 64)
    grid = img.reshape(4, 16, 4, 16, 3).mean(axis=(1, 3)).ravel()
    hist = numpy.concatenate([numpy.histogram(img[..., c], bins=8, range=(0, 1))[0]
                              for c in range(3)]).astype("float32")
    hist /= max(1.0, hist.sum())
    vec = numpy.concatenate([grid - grid.mean(), (hist - hist.mean()) * 4])
    return vec / (numpy.linalg.norm(vec) + 1e-9)


def available_backend():
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401
        return "mobileclip"
    except ImportError:
        return "signature"


def _mobileclip():
    if "m" not in _model:
        import open_clip
        import torch
        torch.set_grad_enabled(False)
        model, _, preprocess = open_clip.create_model_and_transforms(MODEL, pretrained=PRETRAINED)
        on = "cuda" if torch.cuda.is_available() and os.environ.get("VSE_ASSEMBLY_DEVICE") != "cpu" else "cpu"
        model.eval().to(on)
        _model["m"] = (model, preprocess, on)
    return _model["m"]


def embed(image_paths, backend):
    """-> list of unit vectors (numpy), one per image."""
    import numpy
    if backend == "signature":
        return [signature(p) for p in image_paths]
    import torch
    from PIL import Image
    model, preprocess, on = _mobileclip()
    out = []
    for k in range(0, len(image_paths), 32):
        batch = torch.stack([preprocess(Image.open(p).convert("RGB"))
                             for p in image_paths[k:k + 32]]).to(on)
        feats = model.encode_image(batch).float()
        feats = feats / feats.norm(dim=-1, keepdim=True)
        out += [numpy.asarray(f, dtype="float32") for f in feats.cpu().numpy()]
    return out


def describe_clips(clips, cache_dir, backend=None, scene_threshold=0.3):
    """Shots, thumbnails and an embedding for every video/image clip.

    clips: probe dicts. -> {clip path: {"shots": [{start, end, thumbnail}], "embedding": [...]}}
    and the backend used. Thumbnails go to ``cache_dir/thumbs``.
    """
    import numpy
    backend = backend or available_backend()
    result = {}
    images, owners = [], []
    for clip in clips:
        if clip.get("kind") not in ("video", "image") or clip.get("error"):
            continue
        key = hashlib.sha1(clip["path"].encode()).hexdigest()[:8]
        stem = "{:s}.{:s}".format(os.path.splitext(clip["name"])[0], key)
        if clip["kind"] == "image":
            spans = [(0.0, 0.0)]
        else:
            spans = shots(clip["path"], clip.get("duration") or 0.0, scene_threshold)
        entry = {"shots": []}
        for n, (a, b) in enumerate(spans):
            thumb = os.path.join(cache_dir, "thumbs", "{:s}.{:d}.jpg".format(stem, n))
            if not os.path.exists(thumb):
                thumbnail(clip["path"], None if clip["kind"] == "image" else (a + b) / 2, thumb)
            entry["shots"].append({"start": round(a, 3), "end": round(b, 3), "thumbnail": thumb})
            images.append(thumb)
            owners.append(clip["path"])
        result[clip["path"]] = entry
    vectors = embed(images, backend) if images else []
    per_clip = {}
    for owner, vec in zip(owners, vectors):
        per_clip.setdefault(owner, []).append(vec)
    for path, vecs in per_clip.items():
        mean = numpy.mean(vecs, axis=0)
        result[path]["embedding"] = mean / (numpy.linalg.norm(mean) + 1e-9)
    return result, backend


def group(embeddings, threshold):
    """Average-linkage clustering. embeddings: {key: unit vector} -> list of lists of keys."""
    import numpy
    keys = list(embeddings)
    if not keys:
        return []
    vecs = numpy.stack([embeddings[k] for k in keys])
    sim = vecs @ vecs.T
    clusters = [[i] for i in range(len(keys))]
    while len(clusters) > 1:
        best, pair = -2.0, None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                s = float(sim[numpy.ix_(clusters[a], clusters[b])].mean())
                if s > best:
                    best, pair = s, (a, b)
        if best < threshold:
            break
        a, b = pair
        clusters[a] += clusters.pop(b)
    return [[keys[i] for i in sorted(c)] for c in clusters]


def similarity_report(embeddings):
    """Pairwise similarities rounded, for tuning thresholds: {a: {b: s}}."""
    keys = list(embeddings)
    return {os.path.basename(a): {os.path.basename(b): round(float(embeddings[a] @ embeddings[b]), 3)
                                  for b in keys if b != a} for a in keys}


if __name__ == "__main__":  # quick look: python -m vse_standalone.assembly.visual FILES...
    import sys
    from . import probe as _probe
    found = [_probe.probe(p) for p in sys.argv[1:]]
    described, used = describe_clips(found, os.path.join(os.getcwd(), ".assembly_cache"))
    emb = {k: v["embedding"] for k, v in described.items() if "embedding" in v}
    print(used)
    print(json.dumps(similarity_report(emb), indent=1))
    print(group(emb, THRESHOLD[used]))
