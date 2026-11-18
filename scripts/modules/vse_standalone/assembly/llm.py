# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The one place the assembly asks a language model anything: the scene order of a rough cut
made without a screenplay. One request, no chat. Everything else is code.

Uses an OpenAI-compatible server (``VSE_ASSEMBLY_LLM_URL`` or ``LLM_BASE_URL``, e.g. the
editor's own llama.cpp server). If none answers and a small GGUF model is on disk
(``VSE_ASSEMBLY_LLM_MODEL``, or the first ``*.gguf`` in ``$VSE_DATA/models/assembly``), the
image's ``llama-server`` is started for this one request and stopped again (Qwen2.5 0.5B
Instruct, Q4_K_M, about 490 MB, is enough for ordering a handful of scenes; on an NVIDIA card
``VSE_ASSEMBLY_LLM_GPU_LAYERS=99`` puts it on the GPU). Otherwise, or on any error, the
timecode order stays.
"""

import contextlib
import glob
import shutil
import subprocess
import time

import json
import os
import re
import urllib.request


def _base_url():
    return (os.environ.get("VSE_ASSEMBLY_LLM_URL") or os.environ.get("LLM_BASE_URL") or "").rstrip("/")


def _answers(url, timeout=3):
    try:
        with urllib.request.urlopen(url + "/v1/models", timeout=timeout):
            return True
    except Exception:
        return False


def _local_model():
    path = os.environ.get("VSE_ASSEMBLY_LLM_MODEL")
    if path and os.path.exists(path):
        return path
    data = os.environ.get("VSE_DATA", "/data")
    found = sorted(glob.glob(os.path.join(data, "models", "assembly", "*.gguf")))
    return found[0] if found else None


@contextlib.contextmanager
def _local_server(log=None, port=8794):
    """llama-server with the local model, for the length of the block. Yields its URL or None."""
    model, exe = _local_model(), shutil.which(os.environ.get("LLAMA_SERVER", "llama-server"))
    if not model or not exe:
        yield None
        return
    args = [exe, "-m", model, "--host", "127.0.0.1", "--port", str(port), "-c", "4096"]
    if os.environ.get("VSE_ASSEMBLY_LLM_GPU_LAYERS"):
        args += ["-ngl", os.environ["VSE_ASSEMBLY_LLM_GPU_LAYERS"]]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = "http://127.0.0.1:{:d}".format(port)
    try:
        t0 = time.time()
        while time.time() - t0 < 120 and proc.poll() is None:
            if _answers(url, 1):
                if log:
                    log("scene order: started the local model {:s} ({:.0f}s)".format(
                        os.path.basename(model), time.time() - t0))
                yield url
                return
            time.sleep(0.5)
        yield None
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def suggest_scene_order(doc, log=None, timeout=120):
    """-> list of scene ids in the suggested order, or None."""
    if len(doc["scenes"]) < 2:
        return None
    url = _base_url()
    if url and _answers(url):
        return _ask(doc, url, log, timeout)
    with _local_server(log) as local:
        if local:
            return _ask(doc, local, log, timeout)
    if log:
        log("scene order: no language model available; keeping timecode order")
    return None


def _ask(doc, url, log, timeout):
    # Scene ids are numbered in timecode order; the model gets neutral random letters instead,
    # so it orders by content and can't just sort the labels.
    import random
    letters = random.Random(len(doc["scenes"])).sample("ABCDEFGHJKLMNPQRSTUVWXYZ", min(24, len(doc["scenes"])))
    label = {sc["id"]: letters[k] for k, sc in enumerate(doc["scenes"][:24])}
    back = {v: k for k, v in label.items()}
    summaries = []
    for scene in doc["scenes"][:24]:
        said = " / ".join(el["text"] for el in scene["elements"] if el["type"] == "dialogue")
        pictures = ", ".join(doc["media"][c]["name"] for g in doc["groups"]
                             if g["id"] == scene.get("group") for c in g["clips"])
        summaries.append({"id": label[scene["id"]], "said": said[:600], "clips": pictures[:200]})
    prompt = ("These are scenes of a video, each with what is said in it, in no particular order. "
              "Put them in the order that tells the story best: how it starts, what follows, "
              "how it ends. Reply with only a JSON list of the scene ids (the letters), for "
              "example [\"{:s}\"].\n\n".format('", "'.join(sorted(back)[:3]))
              + json.dumps(summaries, ensure_ascii=False))
    body = {"model": os.environ.get("VSE_ASSEMBLY_LLM_NAME", "local"), "temperature": 0,
            "max_tokens": 400, "messages": [{"role": "user", "content": prompt}]}
    try:
        req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            reply = json.load(resp)["choices"][0]["message"]["content"]
    except Exception as ex:  # any failure: keep the timecode order
        if log:
            log("scene order: model not reachable ({:s}); keeping timecode order".format(str(ex)))
        return None
    found = re.search(r"\[.*?\]", reply, re.S)
    try:
        items = [str(x).strip().strip(".") for x in json.loads(found.group(0))] if found else []
        presented = [sc["id"] for sc in doc["scenes"][:24]]
        if items and all(i.isdigit() for i in items):      # positions, 1-based, instead of ids
            order = [presented[int(i) - 1] for i in items if 1 <= int(i) <= len(presented)]
        else:
            order = [back[i] for i in items if i in back]
    except ValueError:
        order = []
    order = list(dict.fromkeys(order))
    if not order:
        return None
    order += [s["id"] for s in doc["scenes"] if s["id"] not in order]
    if log:
        log("scene order suggested by the model: " + " ".join(order))
    return order
