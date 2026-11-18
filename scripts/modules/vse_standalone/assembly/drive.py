# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Google Drive in and out, with the Drive API v3 and an OAuth access token from the site's
Google sign-in (``drive.readonly`` to fetch, ``drive.file`` to upload). Standard library only.

* :func:`fetch_folder`: a folder (and its subfolders) into a local folder: footage, photos,
  audio, PDFs; Google Docs are exported as PDF (scripts). Files already there with the same
  size (and checksum, when Drive gives one) are skipped, so fetching again only gets what's new.
* :func:`upload`: a render, sequence or editing script into a Drive folder (resumable upload,
  any size).

The token is read from the ``GOOGLE_ACCESS_TOKEN`` environment variable by the command line,
never from its arguments, and never written to disk.
"""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import probe

API = os.environ.get("VSE_DRIVE_API", "https://www.googleapis.com")
FOLDER = "application/vnd.google-apps.folder"
EXPORT_AS_PDF = {"application/vnd.google-apps.document"}
KEEP_EXT = {".pdf", ".fountain", ".txt", ".fdx", ".lrc", ".srt", ".cube"}
CHUNK = 8 << 20


class DriveError(Exception):
    pass


def _request(token, url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=dict(headers or {}, Authorization="Bearer " + token))
    try:
        return urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as ex:
        detail = ex.read()[:300].decode("utf-8", "replace")
        if ex.code in (401, 403):
            raise DriveError("Google Drive refused the request ({:d}): sign in to Drive again. {:s}".format(
                ex.code, detail)) from None
        if ex.code == 404:
            raise DriveError("Not found on Google Drive (or not shared with this account)") from None
        raise DriveError("Google Drive error {:d}: {:s}".format(ex.code, detail)) from None


def _json(token, url, **kw):
    with _request(token, url, **kw) as resp:
        return json.load(resp)


def list_folder(token, folder_id):
    q = "'{:s}' in parents and trashed = false".format(folder_id.replace("'", ""))
    params = {"q": q, "pageSize": "1000", "supportsAllDrives": "true",
              "includeItemsFromAllDrives": "true",
              "fields": "nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime)"}
    files, page = [], None
    while True:
        if page:
            params["pageToken"] = page
        data = _json(token, API + "/drive/v3/files?" + urllib.parse.urlencode(params))
        files += data.get("files", [])
        page = data.get("nextPageToken")
        if not page:
            return files


def _safe(name):
    name = name.replace("/", "_").replace("\\", "_").strip()
    return name if name not in ("", ".", "..") else "_"


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _download(token, url, dest):
    tmp = dest + ".part"
    with _request(token, url) as resp, open(tmp, "wb") as fh:
        while True:
            block = resp.read(1 << 20)
            if not block:
                break
            fh.write(block)
    os.replace(tmp, dest)


def fetch_folder(token, folder_id, dest, log=print, _depth=0):
    """-> list of {name, path, bytes, skipped, kind, drive_id}."""
    os.makedirs(dest, exist_ok=True)
    out = []
    for f in sorted(list_folder(token, folder_id), key=lambda f: f["name"]):
        name = _safe(f["name"])
        if f["mimeType"] == FOLDER:
            if _depth < 8:
                out += fetch_folder(token, f["id"], os.path.join(dest, name), log, _depth + 1)
            continue
        if f["mimeType"] in EXPORT_AS_PDF:
            path = os.path.join(dest, name + ".pdf")
            url = "{:s}/drive/v3/files/{:s}/export?mimeType=application/pdf".format(API, f["id"])
            fresh = os.path.exists(path) and os.path.getmtime(path) >= _mtime(f)
        elif f["mimeType"].startswith("application/vnd.google-apps."):
            continue                    # sheets, slides, forms: nothing the editor reads
        else:
            if probe.kind_of(name) is None and os.path.splitext(name)[1].lower() not in KEEP_EXT:
                continue
            path = os.path.join(dest, name)
            url = "{:s}/drive/v3/files/{:s}?alt=media&supportsAllDrives=true".format(API, f["id"])
            size = int(f.get("size") or -1)
            fresh = os.path.exists(path) and os.path.getsize(path) == size and (
                not f.get("md5Checksum") or _md5(path) == f["md5Checksum"])
        if not fresh:
            t0 = time.time()
            _download(token, url, path)
            log("fetched {:s} ({:.1f} MB, {:.1f}s)".format(
                os.path.relpath(path, dest if _depth == 0 else os.path.dirname(dest)),
                os.path.getsize(path) / 1e6, time.time() - t0))
        out.append({"name": name, "path": path, "bytes": os.path.getsize(path), "skipped": fresh,
                    "kind": probe.kind_of(name) or os.path.splitext(name)[1].lstrip(".").lower(),
                    "drive_id": f["id"]})
    return out


def _mtime(f):
    try:
        import datetime
        return datetime.datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return time.time()


def upload(token, path, folder_id, name=None, log=print):
    """Resumable upload of a local file into a Drive folder. -> {id, name, bytes}."""
    import mimetypes
    name = name or os.path.basename(path)
    size = os.path.getsize(path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    meta = json.dumps({"name": name, "parents": [folder_id]}).encode()
    with _request(token, API + "/upload/drive/v3/files?uploadType=resumable&supportsAllDrives=true",
                  data=meta, method="POST",
                  headers={"Content-Type": "application/json; charset=UTF-8",
                           "X-Upload-Content-Type": mime,
                           "X-Upload-Content-Length": str(size)}) as resp:
        session = resp.headers["Location"]
    sent = 0
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            end = sent + len(block) - 1
            headers = {"Content-Length": str(len(block)),
                       "Content-Range": "bytes {:d}-{:d}/{:d}".format(sent, end, size) if block
                       else "bytes */{:d}".format(size)}
            try:
                with _request(token, session, data=block, method="PUT", headers=headers) as resp:
                    result = json.load(resp)
                    log("uploaded {:s} ({:.1f} MB)".format(name, size / 1e6))
                    return {"id": result.get("id"), "name": result.get("name", name), "bytes": size}
            except DriveError as ex:
                if "308" not in str(ex):
                    raise
            sent += len(block)
            log("uploading {:s}: {:.0%}".format(name, sent / max(1, size)))


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Google Drive in/out (token in GOOGLE_ACCESS_TOKEN)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("folder_id")
    p.add_argument("dest")
    p = sub.add_parser("upload")
    p.add_argument("path")
    p.add_argument("folder_id")
    p.add_argument("--name")
    args = parser.parse_args(argv)
    token = os.environ.get("GOOGLE_ACCESS_TOKEN")
    if not token:
        sys.exit("GOOGLE_ACCESS_TOKEN is not set")
    log = lambda t: print(t, file=sys.stderr, flush=True)  # noqa: E731
    try:
        if args.cmd == "fetch":
            files = fetch_folder(token, args.folder_id, args.dest, log)
            result = {"folder": os.path.abspath(args.dest), "files": files,
                      "fetched": sum(1 for f in files if not f["skipped"])}
            with open(os.path.join(args.dest, ".drive.json"), "w", encoding="utf-8") as fh:
                json.dump(dict(result, folder_id=args.folder_id, time=time.time()), fh, indent=1)
        else:
            result = upload(token, args.path, args.folder_id, args.name, log)
    except DriveError as ex:
        sys.exit(str(ex))
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
