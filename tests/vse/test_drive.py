#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Google Drive fetch and upload (vse_standalone.assembly.drive) against a stand-in Drive API
server on localhost: folder listing with pages and subfolders, downloads, Google Docs exported
as PDF, skipping files already fetched, resumable upload in chunks, refused tokens.

    python3 tests/vse/test_drive.py
"""

import hashlib
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts", "modules"))

TOKEN = "test-token"
FILES = {
    "root": [("f1", "A001_C001.mov", "video/quicktime", b"movie-bytes" * 1000),
             ("sub", "sound", "application/vnd.google-apps.folder", None),
             ("doc", "Ad Script 1", "application/vnd.google-apps.document", None),
             ("sheet", "Budget", "application/vnd.google-apps.spreadsheet", None),
             ("junk", "notes.zip", "application/zip", b"zip")],
    "sub": [("f2", "ZOOM0001.WAV", "audio/wav", b"RIFF-wav" * 500)],
}
BLOBS = {fid: data for items in FILES.values() for fid, _n, _m, data in items if data}
state = {"downloads": 0, "upload": bytearray(), "uploaded": None, "chunks": 0}


class Drive(http.server.BaseHTTPRequestHandler):
    def _auth(self):
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            self.send_response(401)
            self.end_headers()
            return False
        return True

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._auth():
            return
        url = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(url.query)
        if url.path == "/drive/v3/files":
            parent = qs["q"][0].split("'")[1]
            items = [{"id": fid, "name": n, "mimeType": m, "modifiedTime": "2026-11-18T00:00:00Z",
                      **({"size": str(len(d)), "md5Checksum": hashlib.md5(d).hexdigest()} if d else {})}
                     for fid, n, m, d in FILES.get(parent, [])]
            page = int(qs.get("pageToken", ["0"])[0])     # two per page, to test paging
            chunk = items[page:page + 2]
            out = {"files": chunk}
            if page + 2 < len(items):
                out["nextPageToken"] = str(page + 2)
            return self._json(out)
        if url.path.endswith("/export"):
            body = b"%PDF-1.4 exported"
        else:
            body = BLOBS[url.path.rsplit("/", 1)[-1]]
        state["downloads"] += 1
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._auth():
            return
        meta = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state["meta"], state["size"] = meta, int(self.headers["X-Upload-Content-Length"])
        self.send_response(200)
        self.send_header("Location", "http://127.0.0.1:{:d}/session/1".format(self.server.server_port))
        self.end_headers()

    def do_PUT(self):
        if not self._auth():
            return
        state["upload"] += self.rfile.read(int(self.headers["Content-Length"]))
        state["chunks"] += 1
        if len(state["upload"]) < state["size"]:
            self.send_response(308)
            self.end_headers()
            return
        self._json({"id": "new1", "name": state["meta"]["name"]})

    def log_message(self, *a):
        pass


def main():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Drive)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    os.environ["VSE_DRIVE_API"] = "http://127.0.0.1:{:d}".format(httpd.server_port)
    from vse_standalone.assembly import drive
    drive.API = os.environ["VSE_DRIVE_API"]
    drive.CHUNK = 4096
    failures = []

    def check(name, ok, detail=""):
        print(("ok    " if ok else "FAIL  ") + name + ("" if ok else "  " + str(detail)))
        if not ok:
            failures.append(name)

    tmp = tempfile.mkdtemp()
    try:
        quiet = lambda t: None  # noqa: E731
        files = drive.fetch_folder(TOKEN, "root", tmp, quiet)
        names = sorted(os.path.relpath(f["path"], tmp) for f in files)
        check("fetches media, subfolders and Docs as PDF; skips the rest",
              names == ["A001_C001.mov", "Ad Script 1.pdf", os.path.join("sound", "ZOOM0001.WAV")], names)
        check("bytes are right", open(os.path.join(tmp, "A001_C001.mov"), "rb").read() == BLOBS["f1"])
        before = state["downloads"]
        again = drive.fetch_folder(TOKEN, "root", tmp, quiet)
        check("fetching again skips what's there", state["downloads"] == before
              and all(f["skipped"] for f in again), (state["downloads"] - before))
        with open(os.path.join(tmp, "A001_C001.mov"), "ab") as fh:
            fh.write(b"x")
        drive.fetch_folder(TOKEN, "root", tmp, quiet)
        check("a changed file is fetched again", state["downloads"] == before + 1)
        try:
            drive.fetch_folder("wrong", "root", tmp, quiet)
            check("a refused token is an error", False)
        except drive.DriveError as ex:
            check("a refused token is an error", "sign in" in str(ex), ex)
        up = os.path.join(tmp, "cut.mp4")
        with open(up, "wb") as fh:
            fh.write(os.urandom(10000))
        r = drive.upload(TOKEN, up, "outfolder", log=quiet)
        check("resumable upload in chunks", r["id"] == "new1" and bytes(state["upload"]) == open(up, "rb").read()
              and state["chunks"] == 3 and state["meta"]["parents"] == ["outfolder"], (r, state["chunks"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        httpd.shutdown()
    print("{:d} failed".format(len(failures)) if failures else "all passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
