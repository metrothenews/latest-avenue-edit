#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The job worker: every heavy step (assembly, render, export, Drive upload, the Drive import
queue with probe and proxy) runs here, not inside the Blender process. Same image as the
editor; started with ``python worker.py`` in the assembly environment.

* Queue: Redis (``REDIS_URL``). Job records (no secrets) are ``job:<id>`` hashes; ``queue:jobs``
  holds what waits, ``queue:running`` what runs. Redis keeps them on disk (append-only file),
  so after a reboot running jobs go back to the queue and run again (they are re-runnable:
  outputs are written atomically). A job that needed a Drive token comes back
  ``needs_token``: tokens live only in this process's memory.
* Internal HTTP API on ``WORKER_PORT`` (default 8795), reachable only on the compose network
  and only with the ``X-Worker-Key`` header (``WORKER_KEY``):

  ``POST /jobs``                 {module, argv, log, stdout, outputs, secrets, user} -> {job}
  ``GET  /jobs/<id>``            the job record, with the last log lines
  ``POST /imports/<op>``         the import queue: start, status, pause, resume, edit, cancel,
                                 remove_media, imported
  ``GET  /status``               {queue_depth, running, done, failed, pool}
  ``GET  /healthz``              ok (no key; for the health check)

* Storage: after a job, its outputs (and each imported file) are copied to S3-compatible
  storage under ``users/<uid>/`` (``S3_*`` settings, see vse_standalone/assembly/s3.py).
* Nightly backup at ``BACKUP_HOUR`` (local time, default 3): ``projects/`` and the editing
  scripts are packed and stored under ``backups/``; the newest ``BACKUP_KEEP`` (14) are kept.
"""

import json
import logging
import os
import subprocess
import sys
import tarfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import redis

from vse_standalone.assembly import imports, s3

DATA = os.environ.get("VSE_DATA", "/data")
KEY = os.environ.get("WORKER_KEY", "")
PORT = int(os.environ.get("WORKER_PORT", "8795"))
POOL = os.environ.get("WORKER_POOL", "pc,cpu")
CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "1"))
OWNER = os.environ.get("STORAGE_UID") or (os.environ.get("ALLOWED_UIDS", "owner").split(",")[0].strip())
BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "3"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "14"))
# What a job may run: [python, -m, module, subcommand, ...] for these only.
ALLOWED = {"vse_standalone.assembly": {"assemble", "export", "render", "summary", "text", "timeline"},
           "vse_standalone.assembly.drive": {"fetch", "upload"}}

log = logging.getLogger("worker")
r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
_secrets = {}                 # job id -> {ENV: value}; memory only
_wake = threading.Event()


def _inside_data(path):
    p = os.path.abspath(path)
    return p == os.path.abspath(DATA) or p.startswith(os.path.abspath(DATA) + os.sep)


def _get(job_id):
    raw = r.hget("job:" + job_id, "j")
    return json.loads(raw) if raw else None


def _put(job):
    r.hset("job:" + job["id"], "j", json.dumps(job))


def submit(spec):
    module, argv = spec.get("module"), spec.get("argv") or []
    if module not in ALLOWED or not argv or argv[0] not in ALLOWED[module]:
        raise ValueError("job not allowed: {!r} {!r}".format(module, argv[:1]))
    for p in [spec.get("log"), spec.get("stdout")] + list(spec.get("outputs") or []):
        if p and not _inside_data(p):
            raise ValueError("paths must be inside the data folder")
    job = {"id": uuid.uuid4().hex[:12], "module": module, "argv": [str(a) for a in argv],
           "log": spec.get("log"), "stdout": spec.get("stdout"), "outputs": spec.get("outputs") or [],
           "user": spec.get("user") or OWNER, "kind": spec.get("kind") or argv[0],
           "needs_secret": bool(spec.get("secrets")), "state": "queued", "created": time.time(),
           "started": None, "finished": None, "returncode": None, "attempts": 0, "stored": []}
    if spec.get("secrets"):
        _secrets[job["id"]] = dict(spec["secrets"])
    _put(job)
    r.rpush("queue:jobs", job["id"])
    _wake.set()
    return job


def _tail(path, n=40):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [l.rstrip() for l in fh if l.strip() and "HF_TOKEN" not in l and "Warning:" not in l][-n:]
    except OSError:
        return []


def _run(job_id):
    job = _get(job_id)
    if job is None or job["state"] not in ("queued", "running"):
        return
    if job.get("needs_secret") and job_id not in _secrets:
        job["state"] = "needs_token"
        _put(job)
        return
    job.update(state="running", started=time.time(), attempts=job["attempts"] + 1)
    _put(job)
    env = dict(os.environ, PYTHONUNBUFFERED="1", **_secrets.get(job_id, {}))
    for k in ("WORKER_KEY", "S3_SECRET_KEY", "S3_ACCESS_KEY"):
        env.pop(k, None)               # the step itself needs none of these
    args = [sys.executable, "-m", job["module"]] + job["argv"]
    os.makedirs(os.path.dirname(job["log"]), exist_ok=True) if job.get("log") else None
    errf = open(job["log"], "w", encoding="utf-8") if job.get("log") else subprocess.DEVNULL
    outf = open(job["stdout"], "w", encoding="utf-8") if job.get("stdout") else subprocess.DEVNULL
    try:
        code = subprocess.call(args, stdout=outf, stderr=errf, env=env, cwd=DATA)
    finally:
        for f in (errf, outf):
            if f is not subprocess.DEVNULL:
                f.close()
    job = _get(job_id)
    job.update(state="done" if code == 0 else "failed", finished=time.time(), returncode=code)
    _secrets.pop(job_id, None)
    if code == 0 and job["outputs"]:
        try:
            job["stored"] = s3.mirror_up(job["user"], DATA, [p for p in job["outputs"] if os.path.exists(p)])
        except Exception as ex:  # storage is a copy: never fail the job for it
            job["storage_error"] = str(ex)[:300]
    _put(job)


def _loop():
    while True:
        jid = r.lmove("queue:jobs", "queue:running", "LEFT", "RIGHT")
        if not jid:
            _wake.wait(2)
            _wake.clear()
            continue
        try:
            _run(jid)
        except Exception:
            log.exception("job %s", jid)
            job = _get(jid)
            if job:
                job.update(state="failed", finished=time.time())
                _put(job)
        finally:
            r.lrem("queue:running", 0, jid)


def _recover():
    """After a restart: what was running goes back to the front of the queue."""
    while True:
        jid = r.rpop("queue:running")
        if not jid:
            break
        job = _get(jid)
        if job:
            job["state"] = "queued"
            _put(job)
            r.lpush("queue:jobs", jid)
            log.info("re-queued %s after a restart", jid)


def status():
    states = {}
    for key in r.scan_iter("job:*"):
        job = json.loads(r.hget(key, "j") or "{}")
        states[job.get("state")] = states.get(job.get("state"), 0) + 1
    imp_running = sum(1 for j in imports.jobs() if j.state == "running")
    return {"queue_depth": r.llen("queue:jobs") + imp_running, "running": r.llen("queue:running"),
            "done": states.get("done", 0), "failed": states.get("failed", 0),
            "needs_token": states.get("needs_token", 0), "imports_running": imp_running, "pool": POOL}


# -----------------------------------------------------------------------------
# The import queue lives in this process now; each imported file is stored too.

def _imported_file(path):
    try:
        s3.mirror_up(OWNER, DATA, [path])
    except Exception as ex:
        log.warning("storage copy of %s failed: %s", path, ex)


imports.on_file_done = _imported_file


def _imports(op, body):
    if op == "start":
        job = imports.start(body["access_token"], body["items"], dest=body.get("dest"),
                            proxies=body.get("proxies", True))
        return {"job_id": job.id}
    if op == "imported":
        return imports.imported()
    if op == "remove_media":
        return imports.remove_media(body.get("drive_ids") or [])
    job = imports.get(body["job_id"])
    if op == "status":
        return job.status()
    if op == "pause":
        return job.pause()
    if op == "resume":
        return job.start(body.get("access_token"))
    if op == "edit":
        return job.edit(body.get("add") or [], body.get("remove") or [], body.get("access_token"))
    if op == "cancel":
        return job.cancel()
    raise ValueError("unknown import operation " + op)


# -----------------------------------------------------------------------------
# Nightly backup.

def backup_now():
    if not s3.configured():
        return None
    stamp = time.strftime("%Y-%m-%d_%H%M")
    path = os.path.join(DATA, ".backups", "projects-{:s}.tar.gz".format(stamp))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for sub in ("projects", os.path.join("media", "projects")):
            full = os.path.join(DATA, sub)
            if os.path.isdir(full):
                tar.add(full, arcname=sub)
    key = "backups/{:s}/projects-{:s}.tar.gz".format(OWNER, stamp)
    s3.put_file(key, path)
    os.remove(path)
    old = sorted(o["key"] for o in s3.list_keys("backups/{:s}/".format(OWNER)))
    for k in old[:-BACKUP_KEEP]:
        s3.delete(k)
    r.set("backup:last", json.dumps({"key": key, "time": time.time()}))
    return key


def _backups():
    while True:
        now = time.localtime()
        last = json.loads(r.get("backup:last") or "{}")
        due = now.tm_hour == BACKUP_HOUR and time.time() - last.get("time", 0) > 20 * 3600
        if due:
            try:
                log.info("nightly backup: %s", backup_now())
            except Exception:
                log.exception("nightly backup failed")
        time.sleep(300)


# -----------------------------------------------------------------------------
# HTTP.

class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not KEY or self.headers.get("X-Worker-Key") != KEY:
            self._reply(401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        if self.path == "/healthz":
            try:
                r.ping()
                return self._reply(200, {"ok": True})
            except redis.RedisError as ex:
                return self._reply(503, {"error": str(ex)})
        if not self._authorized():
            return
        if self.path == "/status":
            return self._reply(200, status())
        if self.path.startswith("/jobs/"):
            job = _get(self.path[6:])
            if not job:
                return self._reply(404, {"error": "no such job"})
            job["log_tail"] = _tail(job["log"]) if job.get("log") else []
            return self._reply(200, job)
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/jobs":
                return self._reply(200, submit(body))
            if self.path.startswith("/imports/"):
                return self._reply(200, _imports(self.path[9:], body))
            if self.path == "/backup":
                return self._reply(200, {"key": backup_now()})
        except (ValueError, KeyError) as ex:
            return self._reply(400, {"error": str(ex.args[0]) if ex.args else type(ex).__name__})
        except Exception as ex:
            log.exception("request failed")
            return self._reply(500, {"error": "{:s}: {!s}".format(type(ex).__name__, ex)})
        self._reply(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s worker %(message)s")
    if not KEY:
        sys.exit("WORKER_KEY is not set")
    os.makedirs(os.path.join(DATA, "media"), exist_ok=True)
    for _ in range(60):
        try:
            r.ping()
            break
        except redis.RedisError:
            time.sleep(1)
    if s3.configured():
        for _ in range(60):
            try:
                s3.ensure_bucket()
                break
            except s3.S3Error:
                time.sleep(2)
    _recover()
    imports.jobs()                      # import queues saved before a restart come back paused
    for _ in range(CONCURRENCY):
        threading.Thread(target=_loop, daemon=True).start()
    threading.Thread(target=_backups, daemon=True).start()
    log.info("worker ready on :%d (pool %s, %d at a time)", PORT, POOL, CONCURRENCY)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
