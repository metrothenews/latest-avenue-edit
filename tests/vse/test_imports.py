#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The Drive import queue (vse_standalone.assembly.imports) against a stand-in Drive server:
folder expansion (recursive, media only), dedupe, pause and resume mid-file with a byte-exact
result (HTTP Range), cancel, removing the file in flight, adding while running, a token
expiring mid-queue (needs_token, then resume), restart recovery, remove_media, probes and
140p proxies.

    python3 tests/vse/test_imports.py          (needs ffmpeg; generates 124x70 1-second clips)

FakeDrive is also used by the editor test (test_assembly_editor.py) for the tools.
"""

import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts", "modules"))

FOLDER = "application/vnd.google-apps.folder"
GOOD, FRESH = "tok-7f3a9c-good", "tok-91c2e4-fresh"     # distinctive, to search the disk for


def make_clip(path, colour, seconds=1):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=124x70:rate=24:duration={:d},hue=h={:d}".format(seconds, colour),
                    "-f", "lavfi", "-i", "sine=frequency=440:duration={:d}".format(seconds),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "32k", "-shortest", path],
                   check=True)
    return open(path, "rb").read()


class FakeDrive:
    """files: {id: {name, mimeType, parent, data(bytes)|None}}. Tokens in self.valid."""

    def __init__(self, files, valid=(GOOD,), throttle=0.0):
        self.files, self.valid, self.throttle = files, set(valid), throttle
        self.ranges, self.requests = [], 0
        drive = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                drive.requests += 1
                if self.headers.get("Authorization", "")[7:] not in drive.valid:
                    self.send_response(401)
                    self.end_headers()
                    return
                url = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(url.query)
                if url.path == "/drive/v3/files":
                    parent = qs["q"][0].split("'")[1]
                    out = [{"id": fid, "name": f["name"], "mimeType": f["mimeType"],
                            **({"size": str(len(f["data"])), "md5Checksum": hashlib.md5(f["data"]).hexdigest()}
                               if f.get("data") is not None and not f["mimeType"].startswith("application/vnd.google-apps") else {})}
                           for fid, f in drive.files.items() if f.get("parent") == parent]
                    return self._json({"files": out})
                fid = url.path.split("/")[4]
                f = drive.files.get(fid)
                if f is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                if url.path.endswith("/export"):
                    return self._bytes(b"%PDF-1.4 exported " + f["name"].encode(), 200)
                if qs.get("alt") == ["media"]:
                    data, start, code = f["data"], 0, 200
                    rng = self.headers.get("Range")
                    if rng:
                        start = int(rng.split("=")[1].split("-")[0])
                        drive.ranges.append((fid, start))
                        code = 206
                    return self._bytes(data[start:], code, slow=True)
                meta = {"id": fid, "name": f["name"], "mimeType": f["mimeType"]}
                if f.get("data") is not None:
                    meta.update(size=str(len(f["data"])), md5Checksum=hashlib.md5(f["data"]).hexdigest())
                return self._json(meta)

            def _json(self, data):
                self._bytes(json.dumps(data).encode(), 200, ctype="application/json")

            def _bytes(self, body, code, ctype="application/octet-stream", slow=False):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                step = 8192
                try:
                    for k in range(0, len(body), step):
                        self.wfile.write(body[k:k + step])
                        if slow and drive.throttle:
                            time.sleep(drive.throttle)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:{:d}".format(self.httpd.server_port)

    def close(self):
        self.httpd.shutdown()


def build_drive(work):
    """A Drive with a folder tree of 124x70 1-second clips, a song, a PDF, a Doc, junk, and two
    big files for pause/resume (random bytes, so the checksum proves byte-exactness)."""
    clips = os.path.join(work, "clips")
    os.makedirs(clips)
    files = {
        "root": {"name": "Shoot", "mimeType": FOLDER},
        "sub": {"name": "B-roll", "mimeType": FOLDER, "parent": "root"},
        "c1": {"name": "A001_C001.mov", "mimeType": "video/quicktime", "parent": "root",
               "data": make_clip(os.path.join(clips, "1.mov"), 0)},
        "c2": {"name": "A001_C002.mov", "mimeType": "video/quicktime", "parent": "root",
               "data": make_clip(os.path.join(clips, "2.mov"), 120)},
        "b1": {"name": "street.mp4", "mimeType": "video/mp4", "parent": "sub",
               "data": make_clip(os.path.join(clips, "3.mp4"), 240)},
        "pdf": {"name": "script.pdf", "mimeType": "application/pdf", "parent": "root", "data": b"%PDF-1.4 x"},
        "doc": {"name": "Ad Script", "mimeType": "application/vnd.google-apps.document", "parent": "root"},
        "sheet": {"name": "Budget", "mimeType": "application/vnd.google-apps.spreadsheet", "parent": "root"},
        "zip": {"name": "notes.zip", "mimeType": "application/zip", "parent": "root", "data": b"PK"},
        "big1": {"name": "big1.mov", "mimeType": "video/quicktime", "data": os.urandom(600 * 1024)},
        "big2": {"name": "big2.mov", "mimeType": "video/quicktime", "data": os.urandom(500 * 1024)},
        "small": {"name": "small.mov", "mimeType": "video/quicktime",
                  "data": make_clip(os.path.join(clips, "4.mov"), 60)},
    }
    return files


failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + name + ("" if ok else "  " + str(detail)[:400]))
    if not ok:
        failures.append(name)


def wait(job, cond, limit=30):
    t0 = time.time()
    while time.time() - t0 < limit:
        st = job.status()
        if cond(st):
            return st
        time.sleep(0.02)
    return job.status()


def item(st, did):
    return next((i for i in st["items"] if i["drive_id"] == did), None)


def main():
    work = tempfile.mkdtemp()
    data = os.path.join(work, "data")
    os.makedirs(os.path.join(data, "media"))
    os.environ["VSE_DATA"] = data
    files = build_drive(work)
    drive = FakeDrive(files, valid=(GOOD, FRESH), throttle=0.004)
    os.environ["VSE_DRIVE_API"] = drive.url
    from vse_standalone.assembly import imports
    imports.CHUNK = 8192
    try:
        # 1. Folder expansion, media only, structure kept, probe and proxy.
        job = imports.start(GOOD, [{"drive_id": "root", "kind": "folder", "name": "Shoot"}])
        st = wait(job, lambda s: s["state"] in ("done", "failed"))
        names = {i["name"]: i for i in st["items"]}
        check("folder: done", st["state"] == "done", st["state"])
        check("folder: expanded recursively, media, PDFs and Docs only",
              set(names) == {"Shoot", "B-roll", "A001_C001.mov", "A001_C002.mov", "street.mp4", "script.pdf", "Ad Script"},
              sorted(names))
        check("folder: children carry parent_id", names["street.mp4"]["parent_id"] == "sub"
              and names["A001_C001.mov"]["parent_id"] == "root" and names["Shoot"]["parent_id"] is None)
        imp = {m["drive_id"]: m for m in imports.imported()}
        check("folder: files in drive/Shoot/... with the folder structure",
              imp["b1"]["path"].endswith(os.path.join("drive", "Shoot", "B-roll", "street.mp4"))
              and open(imp["c1"]["path"], "rb").read() == files["c1"]["data"], imp.get("b1"))
        check("folder: Google Doc exported as PDF", imp["doc"]["path"].endswith("Ad Script.pdf"))
        check("folder: probed (duration) and 140p proxy made", abs((imp["c1"]["duration"] or 0) - 1.0) < 0.1
              and imp["c1"]["proxy"] and os.path.exists(imp["c1"]["proxy"]), imp["c1"])
        check("folder: totals", st["totals"]["files"] == 5 and st["totals"]["done"] == 5
              and st["totals"]["bytes_done"] == st["totals"]["bytes"], st["totals"])

        # 2. Dedupe.
        job2 = imports.start(GOOD, [{"drive_id": "root", "kind": "folder", "name": "Shoot"},
                                      {"drive_id": "c1", "kind": "file", "name": "A001_C001.mov"}])
        st = wait(job2, lambda s: s["state"] == "done")
        files_st = [i for i in st["items"] if i["kind"] == "file"]
        check("dedupe: everything already imported is skipped", files_st and all(
            i["state"] == "skipped" and i.get("reason") == "already in project" for i in files_st),
            [(i["name"], i["state"]) for i in files_st])

        # 3. Pause and resume mid-file, byte-exact.
        job3 = imports.start(GOOD, [{"drive_id": "big1", "kind": "file", "name": "big1.mov"}])
        wait(job3, lambda s: (item(s, "big1")["bytes_done"] or 0) > 100 * 1024)
        st = job3.pause()
        st = wait(job3, lambda s: item(s, "big1")["state"] == "paused")
        part = job3._part(job3.items[0])
        got = os.path.getsize(part) if os.path.exists(part) else 0
        check("pause: the file in flight stops, partial kept", st["state"] == "paused"
              and 0 < got < len(files["big1"]["data"]), (st["state"], item(st, "big1"), got))
        time.sleep(0.3)
        check("pause: nothing more is downloaded while paused", os.path.getsize(part) == got)
        job3.start(GOOD)
        st = wait(job3, lambda s: s["state"] in ("done", "failed"))
        out = imports.manifest().get("big1", {}).get("path")
        check("resume: continues with a Range request and ends byte-exact",
              st["state"] == "done" and ("big1", got) in drive.ranges
              and out and open(out, "rb").read() == files["big1"]["data"], (st["state"], drive.ranges))

        # 4. Removing the file in flight, and adding while running.
        job4 = imports.start(GOOD, [{"drive_id": "big2", "kind": "file", "name": "big2.mov"}])
        wait(job4, lambda s: (item(s, "big2")["bytes_done"] or 0) > 50 * 1024)
        job4.edit(add=[{"drive_id": "small", "kind": "file", "name": "small.mov"}])
        st = job4.edit(remove=["big2"])
        st = wait(job4, lambda s: s["state"] in ("done", "failed"))
        part = job4._part(job4.items[0])
        check("remove in flight: aborted, partial deleted, never imported",
              item(st, "big2")["state"] == "removed" and not os.path.exists(part)
              and "big2" not in imports.manifest(), (item(st, "big2"), os.path.exists(part)))
        check("add while running: the added file is imported", item(st, "small")["state"] == "done"
              and "small" in imports.manifest(), item(st, "small"))

        # 5. Cancel: partial deleted, finished files kept.
        imports.remove_media(["big2"])
        job5 = imports.start(GOOD, [{"drive_id": "big2", "kind": "file", "name": "big2.mov"}])
        wait(job5, lambda s: (item(s, "big2")["bytes_done"] or 0) > 50 * 1024)
        st = job5.cancel()
        part = job5._part(job5.items[0])
        check("cancel: stops, deletes the partial, keeps earlier imports", st["state"] == "cancelled"
              and item(st, "big2")["state"] == "removed" and not os.path.exists(part)
              and "small" in imports.manifest(), (st["state"], item(st, "big2"), os.path.exists(part)))

        # 6. Token expiring mid-queue -> needs_token -> resume with a fresh one.
        imports.remove_media(["small", "big2"])
        drive.valid = {GOOD}
        job6 = imports.start(GOOD, [{"drive_id": "small", "kind": "file", "name": "small.mov"}])
        wait(job6, lambda s: s["state"] == "done")
        drive.valid = {FRESH}                  # the old token is no longer accepted...
        job6.edit(add=[{"drive_id": "big2", "kind": "file", "name": "big2.mov"}])   # ...for the next file
        st = wait(job6, lambda s: s["state"] in ("needs_token", "failed") or item(s, "big2")["state"] == "done")
        check("token expired: job waits in needs_token", st["state"] == "needs_token", (st["state"], item(st, "big2")))
        st = job6.start(FRESH)
        st = wait(job6, lambda s: s["state"] in ("done", "failed"))
        check("needs_token: resume with a fresh token finishes", st["state"] == "done"
              and open(imports.manifest()["big2"]["path"], "rb").read() == files["big2"]["data"], st["state"])

        # 7. Restart: a paused job comes back from disk and resumes byte-exact.
        imports.remove_media(["big1"])
        drive.valid = {GOOD}
        job7 = imports.start(GOOD, [{"drive_id": "big1", "kind": "file", "name": "big1.mov"}])
        wait(job7, lambda s: (item(s, "big1")["bytes_done"] or 0) > 100 * 1024)
        job7.pause()
        wait(job7, lambda s: item(s, "big1")["state"] == "paused")
        jid = job7.id
        imports._jobs.clear()                    # the editor restarts: memory gone, disk kept
        back = imports.get(jid)
        st = back.status()
        check("restart: the job comes back paused with its partial", st["state"] == "paused"
              and item(st, "big1")["state"] == "paused" and item(st, "big1")["bytes_done"] > 0, st["state"])
        back.start(GOOD)
        st = wait(back, lambda s: s["state"] in ("done", "failed"))
        check("restart: resume finishes byte-exact", st["state"] == "done"
              and open(imports.manifest()["big1"]["path"], "rb").read() == files["big1"]["data"], st["state"])
        check("the token is never written to disk", not any(
            GOOD.encode() in open(os.path.join(dp, f), "rb").read() or FRESH.encode() in open(os.path.join(dp, f), "rb").read()
            for dp, _d, fs in os.walk(os.path.join(data, "media")) for f in fs if f.endswith(".json")))

        # 8. remove_media and folder removal.
        r = imports.remove_media(["c2"])
        check("remove_media: file and proxy deleted, no longer imported", r["removed"] == ["c2"]
              and "c2" not in {m["drive_id"] for m in imports.imported()})
        job8 = imports.start(GOOD, [{"drive_id": "root", "kind": "folder", "name": "Shoot"}])
        st = wait(job8, lambda s: s["state"] == "done")
        check("re-import after remove_media fetches only what was removed",
              [i["drive_id"] for i in st["items"] if i["state"] == "done" and i["kind"] == "file"] == ["c2"],
              [(i["drive_id"], i["state"]) for i in st["items"]])
    finally:
        drive.close()
        shutil.rmtree(work, ignore_errors=True)
    print("{:d} failed".format(len(failures)) if failures else "all passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
