# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
A controllable import queue from Google Drive: the user builds it, edits it, pauses, resumes
and cancels it. Contract in cloud/TOOLS.md ("Import queue").

* One file downloads at a time, in queue order, into ``<media>/drive/`` (folders keep their
  structure). Folders are expanded on the server (Drive files.list, recursive, media only:
  video, audio, images, PDFs, and Google Docs exported as PDF); children carry ``parent_id``.
* Pause stops the file in flight and keeps its partial download (``*.part``); resume continues
  it with an HTTP Range request. Every finished file is checked against Drive's size and MD5.
* The access token is held in memory only, per job, never written. On a 401 from Drive the job
  goes to ``needs_token`` and waits for ``resume`` with a fresh token.
* Anything already imported (by Drive id) is ``skipped`` ("already in project").
* Queue state is saved in ``<media>/.imports/<job>.json`` after every change, so after an
  editor restart a job comes back ``paused``, partials intact.
* Each finished file is probed (ffprobe), and videos get a 140p proxy for light thumbnails.
* ``<media>/drive/.manifest.json`` lists what has been imported, for ``list_media`` and
  ``remove_media``.

Standard library only; runs in a background thread of the editor (no bpy).
"""

import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import probe

API = os.environ.get("VSE_DRIVE_API", "https://www.googleapis.com")
FOLDER = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"
CHUNK = 256 * 1024
PROXY_HEIGHT = 140
FINAL = ("done", "failed", "skipped", "removed")

_jobs = {}
_lock = threading.RLock()
on_file_done = None           # optional callback(path) after each import (the worker stores it)


class NeedsToken(Exception):
    pass


class Stop(Exception):
    """The download was interrupted on purpose (pause, cancel, removal)."""


def media_root():
    data = os.environ.get("VSE_DATA")
    if data and os.path.isdir(os.path.join(data, "media")):
        return os.path.join(data, "media")
    return os.environ.get("VSE_MEDIA_ROOT") or os.path.expanduser("~")


def _api():
    return os.environ.get("VSE_DRIVE_API", API)


def _keep(mime, name):
    """Media worth importing from a folder: video, audio, images, PDFs, Google Docs."""
    return (mime.startswith(("video/", "audio/", "image/")) or mime == "application/pdf" or mime == GDOC
            or probe.kind_of(name) is not None)


def _safe(name):
    name = (name or "untitled").replace("/", "_").replace("\\", "_").strip()
    return name if name not in ("", ".", "..") else "_"


# -----------------------------------------------------------------------------
# Manifest of imported files.

def _manifest_path():
    return os.path.join(media_root(), "drive", ".manifest.json")


def manifest():
    try:
        with open(_manifest_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    # Files deleted by hand are no longer "in the project".
    return {k: v for k, v in data.items() if os.path.exists(v.get("path", ""))}


def _save_manifest(data):
    path = _manifest_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def imported():
    """-> [{drive_id, name, size, bytes, duration, kind, path, proxy}] of what is in the project."""
    out = []
    for did, m in sorted(manifest().items(), key=lambda kv: kv[1].get("imported_at", 0)):
        out.append({"drive_id": did, "name": m["name"], "size": m.get("size"), "bytes": m.get("size"),
                    "duration": m.get("duration"), "kind": m.get("kind"), "path": m["path"],
                    "proxy": m.get("proxy")})
    return out


def remove_media(drive_ids):
    """Delete imported files (and their proxies) from the project and the disk."""
    with _lock:
        data = manifest()
        removed, missing = [], []
        for did in drive_ids:
            m = data.pop(did, None)
            if not m:
                missing.append(did)
                continue
            for p in (m.get("path"), m.get("proxy")):
                if p and os.path.exists(p):
                    os.remove(p)
            removed.append(did)
        _save_manifest(data)
        for job in _jobs.values():
            for it in job.items:
                if it["drive_id"] in removed and it["state"] == "done":
                    it["state"], it["reason"] = "removed", "removed from the project"
            job._save()
    return {"removed": removed, "not_found": missing}


# -----------------------------------------------------------------------------
# Drive requests.

def _request(token, url, headers=None, method=None, data=None):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=dict(headers or {}, Authorization="Bearer " + (token or "")))
    try:
        return urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as ex:
        if ex.code == 401:
            raise NeedsToken() from None
        detail = ex.read()[:200].decode("utf-8", "replace")
        if ex.code == 403:
            raise RuntimeError("Google Drive refused access ({:s})".format(detail.strip()[:120])) from None
        if ex.code == 404:
            raise RuntimeError("not found on Google Drive (or not shared with this account)") from None
        raise RuntimeError("Google Drive error {:d}: {:s}".format(ex.code, detail)) from None


def _json(token, url):
    with _request(token, url) as resp:
        return json.load(resp)


def _meta(token, drive_id):
    return _json(token, "{:s}/drive/v3/files/{:s}?{:s}".format(_api(), drive_id, urllib.parse.urlencode(
        {"fields": "id,name,mimeType,size,md5Checksum", "supportsAllDrives": "true"})))


def _children(token, folder_id):
    q = "'{:s}' in parents and trashed = false".format(folder_id.replace("'", ""))
    params = {"q": q, "pageSize": "1000", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
              "fields": "nextPageToken,files(id,name,mimeType,size,md5Checksum)"}
    out, page = [], None
    while True:
        if page:
            params["pageToken"] = page
        data = _json(token, _api() + "/drive/v3/files?" + urllib.parse.urlencode(params))
        out += data.get("files", [])
        page = data.get("nextPageToken")
        if not page:
            return sorted(out, key=lambda f: (f["mimeType"] != FOLDER, f["name"].lower()))


# -----------------------------------------------------------------------------
# The job.

def _new_item(raw, parent_id=None, folder_path=""):
    kind = raw.get("kind") or ("folder" if raw.get("mimeType") == FOLDER else "file")
    return {"drive_id": raw["drive_id"] if "drive_id" in raw else raw["id"],
            "name": raw.get("name") or raw.get("drive_id"), "kind": kind, "parent_id": parent_id,
            "size": int(raw["size"]) if raw.get("size") not in (None, "") else None,
            "bytes_done": 0, "state": "queued", "mime": raw.get("mimeType"),
            "md5": raw.get("md5Checksum"), "recursive": raw.get("recursive", True),
            "folder_path": folder_path, "path": None, "flat": bool(raw.get("flat"))}


def _mark_known(items):
    """Files already in the project are skipped as soon as they are queued."""
    known = manifest()
    for it in items:
        if it["kind"] == "file" and it["state"] == "queued" and it["drive_id"] in known:
            it["state"], it["reason"] = "skipped", "already in project"
            it["size"] = it["size"] or known[it["drive_id"]].get("size")
            it["bytes_done"] = it["size"] or 0


class Job:
    def __init__(self, job_id=None, dest=None, proxies=True):
        self.id = job_id or uuid.uuid4().hex[:10]
        self.dest = dest or os.path.join(media_root(), "drive")
        self.proxies = proxies
        self.items = []
        self.state = "paused"
        self.token = None                 # memory only
        self.error = None
        self.created = time.time()
        self._cv = threading.Condition(_lock)
        self._thread = None
        self._abort = None                # drive_id of the in-flight item to abort
        self._current = None
        self._last_save = 0.0

    # -- persistence (no token) --
    def _state_path(self):
        return os.path.join(media_root(), ".imports", self.id + ".json")

    def _save(self, force=True):
        if not force and time.time() - self._last_save < 1.0:
            return
        self._last_save = time.time()
        path = self._state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {"id": self.id, "dest": self.dest, "proxies": self.proxies, "state": self.state,
                "error": self.error, "created": self.created, "items": self.items}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        job = cls(data["id"], data.get("dest"), data.get("proxies", True))
        job.items, job.created, job.error = data["items"], data.get("created", 0), data.get("error")
        job.state = data["state"]
        if job.state in ("running", "needs_token"):
            job.state = "paused"            # the token is gone with the old process
        for it in job.items:
            if it["state"] in ("downloading", "listing"):
                it["state"] = "paused" if it["kind"] == "file" else "queued"
            if it["kind"] == "file" and it["state"] in ("paused", "queued"):
                it["bytes_done"] = os.path.getsize(job._part(it)) if os.path.exists(job._part(it)) else 0
        return job

    # -- status --
    def status(self):
        with _lock:
            files = [it for it in self.items if it["kind"] == "file" and it["state"] != "removed"]
            items = []
            for it in self.items:
                out = {k: it.get(k) for k in ("drive_id", "name", "kind", "parent_id", "size", "bytes_done", "state")}
                if it["kind"] == "folder" and it["state"] not in ("listing", "queued", "removed", "failed"):
                    out["state"] = self._folder_state(it)
                if it.get("error"):
                    out["error"] = it["error"]
                if it.get("reason"):
                    out["reason"] = it["reason"]
                items.append(out)
            return {"job_id": self.id, "state": self.state, "error": self.error,
                    "totals": {"files": len(files),
                               "bytes": sum(it["size"] or 0 for it in files),
                               "bytes_done": sum(it["size"] or 0 if it["state"] in ("done", "skipped")
                                                 else it["bytes_done"] for it in files),
                               "done": sum(1 for it in files if it["state"] in ("done", "skipped")),
                               "failed": sum(1 for it in files if it["state"] == "failed")},
                    "items": items}

    def _descendants(self, drive_id):
        out, frontier = [], {drive_id}
        while frontier:
            kids = [it for it in self.items if it.get("parent_id") in frontier]
            out += kids
            frontier = {k["drive_id"] for k in kids if k["kind"] == "folder"}
        return out

    def _folder_state(self, folder):
        kids = [k for k in self._descendants(folder["drive_id"]) if k["kind"] == "file" and k["state"] != "removed"]
        states = {k["state"] for k in kids}
        if not kids or states <= {"done", "skipped"}:
            return "done"
        if "downloading" in states:
            return "downloading"
        if "paused" in states or (self.state in ("paused", "needs_token") and states & {"queued"}):
            return "paused"
        if states <= set(FINAL):
            return "failed" if "failed" in states else "done"
        return "queued"

    # -- control --
    def start(self, token):
        with _lock:
            self.token = token or self.token
            if self.state in ("done", "cancelled"):
                if not any(it["state"] in ("queued", "paused") for it in self.items):
                    return self.status()
            for it in self.items:
                if it["state"] == "paused":
                    it["state"] = "queued"
            self.state, self.error = "running", None
            self._save()
            if not self._thread or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="import-" + self.id, daemon=True)
                self._thread.start()
            self._cv.notify_all()
            return self.status()

    def pause(self):
        with _lock:
            if self.state == "running":
                self.state = "paused"
                self._save()
                self._cv.notify_all()
            return self.status()

    def cancel(self):
        with _lock:
            self.state = "cancelled"
            for it in self.items:
                if it["state"] not in FINAL:
                    it["state"], it["reason"] = "removed", "cancelled"
                    self._drop_part(it)
            self._abort = self._current
            self._save()
            self._cv.notify_all()
        self._wait_idle()
        with _lock:
            for it in self.items:            # the one in flight may have left a part behind
                if it["state"] == "removed":
                    self._drop_part(it)
            return self.status()

    def edit(self, add=None, remove=None, token=None):
        with _lock:
            if token:
                self.token = token
            known = {it["drive_id"] for it in self.items if it["state"] != "removed"}
            for raw in add or []:
                it = _new_item(raw)
                if it["drive_id"] in known:
                    continue
                self.items.append(it)
                known.add(it["drive_id"])
                _mark_known([it])
                if self.state in ("done", "cancelled") and it["state"] == "queued":
                    self.state = "paused" if not self.token else "running"
            for did in remove or []:
                targets = [it for it in self.items if it["drive_id"] == did and it["state"] != "removed"]
                for it in list(targets):
                    if it["kind"] == "folder":
                        targets += [k for k in self._descendants(did)]
                for it in targets:
                    if it["state"] in ("done", "skipped"):
                        continue             # finished files stay: remove_media deletes them
                    if it["drive_id"] == self._current:
                        self._abort = it["drive_id"]
                    it["state"], it["reason"] = "removed", "removed from the queue"
                    self._drop_part(it)
            self._save()
            self._cv.notify_all()
            need_thread = self.state == "running" and (not self._thread or not self._thread.is_alive())
        if need_thread:
            return self.start(None)
        return self.status()

    def _wait_idle(self, timeout=10):
        t0 = time.time()
        while self._current and time.time() - t0 < timeout:
            time.sleep(0.02)

    # -- work --
    def _part(self, it):
        return self._target(it) + ".part"

    def _target(self, it):
        name = _safe(it["name"]) + (".pdf" if it.get("mime") == GDOC and not it["name"].lower().endswith(".pdf") else "")
        return os.path.join(self.dest, it.get("folder_path") or "", name)

    def _drop_part(self, it):
        if it["kind"] == "file":
            try:
                os.remove(self._part(it))
            except OSError:
                pass

    def _next(self):
        for it in self.items:
            if it["state"] == "queued":
                return it
        return None

    def _run(self):
        while True:
            with _lock:
                while self.state != "running":
                    if self.state in ("cancelled", "done", "failed"):
                        self._current = None
                        return
                    self._current = None
                    self._cv.wait(timeout=5)
                it = self._next()
                if it is None:
                    self.state = "done"
                    self._current = None
                    self._save()
                    return
                self._current = it["drive_id"]
                self._abort = None
                it["state"] = "listing" if it["kind"] == "folder" else "downloading"
                token = self.token
                self._save()
            try:
                if it["kind"] == "folder":
                    self._expand(it, token)
                else:
                    self._download(it, token)
            except NeedsToken:
                with _lock:
                    if it["state"] in ("downloading", "listing"):
                        it["state"] = "paused" if it["kind"] == "file" else "queued"
                        it["reason"] = "needs a fresh Google sign-in"
                    if self.state == "running":
                        self.state = "needs_token"
                    self.token = None
                    self._save()
            except Stop:
                with _lock:
                    if it["state"] == "downloading":
                        it["state"] = "paused"
                    elif it["state"] == "removed":
                        self._drop_part(it)      # removed or cancelled while in flight
                    self._save()
            except Exception as ex:  # one bad file doesn't stop the queue
                with _lock:
                    if it["state"] in ("downloading", "listing"):
                        it["state"], it["error"] = "failed", str(ex)[:300]
                    self._save()
            finally:
                with _lock:
                    self._current = None
                    self._cv.notify_all()

    def _expand(self, folder, token):
        prefix = folder.get("folder_path") or ""
        if not folder.get("flat"):
            prefix = os.path.join(prefix, _safe(folder["name"]))
        listed = _children(token, folder["drive_id"])
        with _lock:
            if folder["state"] == "removed":
                return
            pos = self.items.index(folder) + 1
            known = {it["drive_id"] for it in self.items if it["state"] != "removed"}
            new = []
            for f in listed:
                if f["mimeType"] == FOLDER:
                    if not folder.get("recursive", True):
                        continue
                elif f["mimeType"].startswith("application/vnd.google-apps.") and f["mimeType"] != GDOC:
                    continue
                elif not _keep(f["mimeType"], f["name"]):
                    continue
                if f["id"] in known:
                    continue
                it = _new_item(dict(f, recursive=folder.get("recursive", True)), folder["drive_id"], prefix)
                new.append(it)
            _mark_known(new)
            self.items[pos:pos] = new
            folder["state"] = "done"
            folder["size"] = None
            self._save()

    def _download(self, it, token):
        if it["drive_id"] in manifest():
            with _lock:
                it["state"], it["reason"] = "skipped", "already in project"
                it["bytes_done"] = it["size"] or 0
                self._save()
            return
        if it.get("size") is None and it.get("mime") != GDOC:
            meta = _meta(token, it["drive_id"])
            with _lock:
                it["size"] = int(meta["size"]) if meta.get("size") else None
                it["md5"], it["mime"] = meta.get("md5Checksum"), meta.get("mimeType")
                it["name"] = it["name"] or meta.get("name")
        part, target = self._part(it), self._target(it)
        os.makedirs(os.path.dirname(part), exist_ok=True)
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if it.get("mime") == GDOC:
            url = "{:s}/drive/v3/files/{:s}/export?mimeType=application/pdf".format(_api(), it["drive_id"])
            have, headers = 0, {}
        else:
            url = "{:s}/drive/v3/files/{:s}?alt=media&supportsAllDrives=true".format(_api(), it["drive_id"])
            headers = {"Range": "bytes={:d}-".format(have)} if have else {}
        if it.get("size") is not None and have >= it["size"] > 0:
            headers = None                       # already complete: just finish it
        if headers is not None:
            with _request(token, url, headers=headers) as resp:
                if have and resp.status != 206:
                    have = 0                     # the server ignored the range: start over
                with open(part, "ab" if have else "wb") as fh:
                    while True:
                        with _lock:
                            if self._abort == it["drive_id"] or it["state"] == "removed":
                                raise Stop()
                            if self.state != "running":
                                raise Stop()
                        block = resp.read(CHUNK)
                        if not block:
                            break
                        fh.write(block)
                        have += len(block)
                        with _lock:
                            it["bytes_done"] = have
                            self._save(force=False)
        if it.get("size") is not None and have != it["size"]:
            raise RuntimeError("download incomplete ({:d} of {:d} bytes)".format(have, it["size"]))
        if it.get("md5"):
            h = hashlib.md5()
            with open(part, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
            if h.hexdigest() != it["md5"]:
                os.remove(part)
                raise RuntimeError("checksum mismatch: downloaded again from the start next time")
        final = target
        n = 1
        while os.path.exists(final):             # same name from another Drive file
            n += 1
            stem, ext = os.path.splitext(target)
            final = "{:s} ({:d}){:s}".format(stem, n, ext)
        os.replace(part, final)
        info = self._finish(final)
        with _lock:
            it["path"], it["state"], it["bytes_done"] = final, "done", os.path.getsize(final)
            it["size"] = it["size"] or it["bytes_done"]
            data = manifest()
            data[it["drive_id"]] = {"name": os.path.basename(final), "path": final, "size": it["size"],
                                    "kind": info.get("kind"), "duration": info.get("duration"),
                                    "proxy": info.get("proxy"), "imported_at": time.time(), "job": self.id}
            _save_manifest(data)
            self._save()
        if on_file_done:
            for p in (final, info.get("proxy")):
                if p:
                    on_file_done(p)

    def _finish(self, path):
        """Probe the file (ready for the assembly) and make a small proxy for videos."""
        info = {"kind": probe.kind_of(path) or ("pdf" if path.lower().endswith(".pdf") else None)}
        if info["kind"] in ("video", "audio", "image"):
            try:
                p = probe.probe(path)
                info.update(kind=p["kind"], duration=p.get("duration"))
            except RuntimeError:
                pass
        if self.proxies and info["kind"] == "video":
            proxy = os.path.join(os.path.dirname(path), ".proxies", os.path.basename(path) + ".mp4")
            os.makedirs(os.path.dirname(proxy), exist_ok=True)
            done = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", path, "-vf",
                                   "scale=-2:{:d}".format(PROXY_HEIGHT), "-c:v", "libx264", "-crf", "30",
                                   "-preset", "veryfast", "-c:a", "aac", "-b:a", "48k", proxy],
                                  capture_output=True)
            if done.returncode == 0:
                info["proxy"] = proxy
        return info


# -----------------------------------------------------------------------------
# Module API (what the editor tools call).

def _restore():
    """Jobs saved before a restart come back paused."""
    folder = os.path.join(media_root(), ".imports")
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        if name.endswith(".json"):
            jid = name[:-5]
            if jid not in _jobs:
                try:
                    _jobs[jid] = Job.load(os.path.join(folder, name))
                except (OSError, ValueError, KeyError):
                    pass


def get(job_id):
    with _lock:
        _restore()
        job = _jobs.get(job_id)
    if job is None:
        raise KeyError("No import {!r}".format(job_id))
    return job


def start(token, items, dest=None, proxies=True):
    if not token:
        raise ValueError("An access token is needed to read Google Drive")
    if not items:
        raise ValueError("Nothing to import: add files or folders")
    with _lock:
        _restore()
        job = Job(dest=dest, proxies=proxies)
        seen = set()
        for raw in items:
            if not raw.get("drive_id"):
                raise ValueError("Every item needs a drive_id")
            it = _new_item(raw)
            if it["drive_id"] in seen:
                continue
            seen.add(it["drive_id"])
            job.items.append(it)
        _mark_known(job.items)
        _jobs[job.id] = job
    job.start(token)
    return job


def jobs():
    with _lock:
        _restore()
        return list(_jobs.values())
