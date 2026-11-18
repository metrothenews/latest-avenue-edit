# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The one place the assembly asks a language model anything: the scene order of a rough cut
made without a screenplay. One request, no chat. Everything else is code.

Uses an OpenAI-compatible server (``LLM_BASE_URL``, e.g. the editor's own llama.cpp server,
``http://127.0.0.1:8793``). With none configured, or on any error, the timecode order stays.
"""

import json
import os
import re
import urllib.request


def _base_url():
    return (os.environ.get("VSE_ASSEMBLY_LLM_URL") or os.environ.get("LLM_BASE_URL") or "").rstrip("/")


def suggest_scene_order(doc, log=None, timeout=120):
    """-> list of scene ids in the suggested order, or None."""
    url = _base_url()
    if not url or len(doc["scenes"]) < 2:
        return None
    summaries = []
    for scene in doc["scenes"]:
        said = " / ".join(el["text"] for el in scene["elements"] if el["type"] == "dialogue")
        pictures = ", ".join(doc["media"][c]["name"] for g in doc["groups"]
                             if g["id"] == scene.get("group") for c in g["clips"])
        summaries.append({"id": scene["id"], "said": said[:600], "clips": pictures[:200]})
    prompt = ("These are scenes of a video, each with what is said in it. Put them in the order "
              "that tells the story best. Reply with only a JSON list of the scene ids.\n\n"
              + json.dumps(summaries, ensure_ascii=False))
    body = {"model": os.environ.get("VSE_ASSEMBLY_LLM_MODEL", "local"), "temperature": 0,
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
    ids = {s["id"] for s in doc["scenes"]}
    found = re.search(r"\[.*?\]", reply, re.S)
    try:
        order = [s for s in json.loads(found.group(0)) if s in ids] if found else []
    except ValueError:
        order = []
    order = list(dict.fromkeys(order))
    if not order:
        return None
    order += [s["id"] for s in doc["scenes"] if s["id"] not in order]
    if log:
        log("scene order suggested by the model: " + " ".join(order))
    return order
