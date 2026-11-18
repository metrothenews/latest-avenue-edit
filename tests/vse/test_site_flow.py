#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The website's /edit flow, end to end, over the gateway's /api/rpc, exactly in the order of
cloud/TOOLS.md: upload the script, start the assembly, poll the job, read and edit the
editing script, swap a take, fix a sync, take pictures, lay it out on the timeline, render,
export for Resolve / Premiere / Final Cut; then the music-video path (song + lyrics).

    python3 tests/vse/test_site_flow.py GATEWAY_URL SIGNING_KEY.pem SHOOT_FOLDER_IN_MEDIA

The gateway must trust the key's certificate (GATEWAY_CERTS_FILE, key id "kid1") with
FIREBASE_PROJECT_ID=test-project, ALLOWED_UIDS=owner123 and http://localhost:5173 allowed.
SHOOT_FOLDER_IN_MEDIA is a shoot made by tests/vse/assembly/make_samples.py, inside the
editor's media folder (e.g. "shoot" for /data/media/shoot). Standard library + PyJWT.
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

import jwt

BASE, KEY, SHOOT = sys.argv[1].rstrip("/"), open(sys.argv[2]).read(), sys.argv[3]
MEDIA_ROOT = os.environ.get("VSE_MEDIA_ROOT", "/data/media")
ORIGIN = "http://localhost:5173"
failures = []


def token():
    now = int(time.time())
    return jwt.encode({"iss": "https://securetoken.google.com/test-project", "aud": "test-project",
                       "sub": "owner123", "iat": now - 10, "exp": now + 3600, "auth_time": now - 10},
                      KEY, algorithm="RS256", headers={"kid": "kid1"})


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + name + ("" if ok else "  " + str(detail)[:400]))
    if not ok:
        failures.append(name)


def rpc(method, params=None, auth=True, timeout=900):
    """One JSON-RPC call the way the page makes it. -> (http status, reply dict)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    headers = {"Content-Type": "application/json", "Origin": ORIGIN}
    if auth:
        headers["Authorization"] = "Bearer " + token()
    req = urllib.request.Request(BASE + "/api/rpc", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as ex:
        try:
            return ex.code, json.load(ex)
        except ValueError:
            return ex.code, {}


def call(method, params=None, **kw):
    """A call that must succeed. -> result."""
    status, reply = rpc(method, params, **kw)
    if status != 200 or "error" in reply:
        raise RuntimeError("{:s} failed ({:d}): {!s}".format(method, status, reply.get("error", reply)))
    return reply["result"]


def wait_job(job, what, limit=900):
    t0, seen = time.time(), 0
    while time.time() - t0 < limit:
        r = call("job_status", {"job": job})
        for line in r["log"][seen:]:
            print("        " + line)
        seen = len(r["log"])
        if r["state"] != "running":
            check("{:s}: job ends 'done'".format(what), r["state"] == "done", r["log"][-5:])
            return r
        time.sleep(1.5)
    check("{:s}: job finishes within {:d}s".format(what, limit), False)
    return None


def upload(path, name, folder="uploads"):
    data = open(path, "rb").read()
    chunk = 600 * 1024
    r = None
    for k in range(0, max(1, len(data)), chunk):
        r = call("save_upload", {"name": name, "folder": folder, "append": k > 0,
                                 "data_base64": base64.b64encode(data[k:k + chunk]).decode()})
    return r


def main():
    # 0. The gateway is up; only the owner gets through; the editor starts on the first call.
    with urllib.request.urlopen(BASE + "/healthz", timeout=10) as r:
        check("gateway answers /healthz", r.status == 200)
    status, _ = rpc("ping", auth=False)
    check("no sign-in: 401", status == 401, status)
    t0 = time.time()
    while True:
        status, reply = rpc("ping", timeout=30)
        if status == 200 and "result" in reply:
            break
        if time.time() - t0 > 180:
            check("editor starts after the first signed-in request", False, (status, reply))
            return
        time.sleep(2)
    check("editor starts after the first signed-in request ({:.0f}s)".format(time.time() - t0), True)

    names = {t["name"] for t in call("tools/list")}
    wanted = {"assembly_status", "fetch_drive_folder", "probe_media", "save_upload", "read_screenplay",
              "start_assembly", "job_status", "get_editing_script", "edit_editing_script",
              "choose_take", "set_line_cut", "take_thumbnails", "set_clip_sync",
              "lay_out_editing_script", "render_editing_script", "export_sequence", "upload_to_drive"}
    check("every tool in cloud/TOOLS.md is listed", wanted <= names, wanted - names)
    st = call("assembly_status")
    check("assembly environment ready (transcription, ffmpeg, pdftotext)",
          st["transcription"] and all(st["tools"].values()), st)
    print("        picture model:", st["image_model"])

    # 1-2. The footage is there (as after fetch_drive_folder); the script is uploaded.
    media = call("probe_media", {"filepath": SHOOT + "/media"})
    check("probe_media lists the shoot", len(media["files"]) >= 10, len(media["files"]))
    shoot_abs = os.path.join(MEDIA_ROOT, SHOOT)
    up = upload(os.path.join(shoot_abs, "script.pdf"), "the last bus.pdf")
    check("save_upload: script PDF", up["relative"] == "uploads/the last bus.pdf", up)
    s = call("read_screenplay", {"filepath": up["relative"]})
    check("read_screenplay on the upload", s["dialogue_lines"] == 6, s["dialogue_lines"])
    status, reply = rpc("read_screenplay", {"filepath": "uploads/nothing.pdf"})
    check("a missing file is a readable error", status == 200 and "File not found" in reply.get("error", {}).get("message", ""), reply)

    # 3-4. Assembly as a job.
    out = "projects/site-test.edit.json"
    j = call("start_assembly", {"media_folder": SHOOT + "/media", "output": out, "script": up["relative"]})
    check("start_assembly returns a job at once", "job" in j, j)
    r = wait_job(j["job"], "assembly")
    if not r or r["state"] != "done":
        return
    summary = r["result"]
    check("every script line has footage", summary["lines_with_footage"] == summary["dialogue_lines"] == 6, summary)

    # 5. The editing script: read, edit as text, swap a take, fix a sync, pictures.
    lines = call("get_editing_script", {"filepath": out, "format": "lines"})["lines"]
    text = call("get_editing_script", {"filepath": out, "format": "text"})["text"]
    check("text and lines", "BUS STATION" in text and len(lines) >= 8, len(lines))
    edited = text.replace("They make terrible coffee, but the\n          pie is good.", "")
    rep = call("edit_editing_script", {"filepath": out, "text": edited})
    check("edit_editing_script: a shortened line is re-trimmed", len(rep["retrimmed"]) == 1, rep)
    multi = next(l for l in lines if l["type"] == "dialogue" and len(l["takes"]) > 1)
    r = call("choose_take", {"filepath": out, "line": multi["id"], "take": 1})
    check("choose_take", r["cut"]["clip"] == multi["takes"][1]["clip"], r)
    pics = call("take_thumbnails", {"filepath": out, "line": multi["id"], "width": 96})
    check("take_thumbnails: a picture per take", len(pics["takes"]) == len(multi["takes"]) and all(
        t.get("data_url", "").startswith("data:image/jpeg") for t in pics["takes"]), [t.get("error") for t in pics["takes"]])
    doc = call("get_editing_script", {"filepath": out, "format": "json"})
    synced = next(c for c, m in doc["media"].items() if m.get("sound"))
    r = call("set_clip_sync", {"filepath": out, "clip": synced, "nudge": 0.04})
    check("set_clip_sync: nudge", r["sound"]["method"] == "manual", r)
    r = call("set_clip_sync", {"filepath": out, "clip": synced, "nudge": -0.04})
    status, reply = rpc("choose_take", {"filepath": out, "line": "S9-9", "take": 0})
    check("a bad line id is a readable error", "error" in reply and "S9-9" in reply["error"]["message"], reply)

    # 6. Submit: lay it out on the timeline.
    lay = call("lay_out_editing_script", {"filepath": out})
    check("lay_out_editing_script", lay["events"] == summary["events"] - 0 or lay["events"] > 0, lay)
    strips = call("list_strips")
    check("the timeline holds the cut", len(strips) >= lay["events"], len(strips))

    # 7. Render and exports.
    r = call("render_editing_script", {"filepath": out, "output": "renders/site-test.mp4"})
    check("render_editing_script", r["duration"] > 5 and r["events"] > 0, r)
    p = call("probe_media", {"filepath": "renders/site-test.mp4"})
    check("the render is a playable video", p["kind"] == "video" and abs(p["duration"] - r["duration"]) < 0.5, p)
    for fmt, ext in (("fcpxml", "fcpxml"), ("xml", "xml"), ("edl", "edl"), ("otio", "otio")):
        r = call("export_sequence", {"filepath": out, "output": "renders/site-test." + ext, "format": fmt})
        check("export_sequence {:s}".format(fmt), r["events"] > 0 and r["format"] == fmt, r)

    # Music video: song and lyric sheet uploaded, then the job.
    music = os.path.join(shoot_abs, "music")
    if os.path.isdir(music):
        song = upload(os.path.join(music, "song.wav"), "song.wav", folder="uploads")
        lyr = upload(os.path.join(shoot_abs, "lyrics.txt"), "lyrics.txt", folder="uploads")
        j = call("start_assembly", {"media_folder": SHOOT + "/music", "output": "projects/site-mv.edit.json",
                                    "song": song["relative"], "lyrics": lyr["relative"]})
        r = wait_job(j["job"], "music video")
        if r and r["state"] == "done":
            check("music video: sections cut on the song", r["result"]["events"] >= 4, r["result"])
            lay = call("lay_out_editing_script", {"filepath": "projects/site-mv.edit.json"})
            check("music video lays out", lay["events"] == r["result"]["events"], lay)


try:
    main()
except Exception as ex:
    import traceback
    traceback.print_exc()
    failures.append("exception: {!s}".format(ex))
print("{:d} failed".format(len(failures)) if failures else "all passed")
sys.exit(1 if failures else 0)
