#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The updater: keeps the PC's stack on the released image, with nothing done on the PC.

Pull-based. The PC never accepts deploy commands from outside and never runs CI. Every
``UPDATE_INTERVAL`` seconds (300) it pulls ``EDIT_IMAGE`` (``ghcr.io/…:stable``, moved by CI
when a build passes its tests; moving it back is a rollback). When the image changed:

1. It waits until nobody is editing (``sessions_free``), for up to ``UPDATE_IDLE_WAIT`` (2 h).
2. It takes the stack's files (compose.yaml, compose.gpu.yaml, env.example) out of the new
   image, so compose changes ship with the image and nobody runs ``git pull`` on the PC.
3. It restarts one service at a time: it drains the worker first and waits for its jobs
   (``DRAIN_TIMEOUT``, 30 min), then the gateway, the heartbeat, and anything else.
4. It checks every service's health and the gateway's ``/status`` for the new version
   (``VERIFY_SECONDS``, 240).
5. If that fails, it puts the previous image back, with the previous files, and marks the
   new image as bad, so it is not tried again until ``:stable`` moves. Then it updates itself.

Its state and the stack's files live in the ``updater-state`` volume. The secrets are this
container's environment (``env_file: .env``), never written outside the volume.

An internal API on ``UPDATER_PORT`` (8796), on the compose network, needs ``X-Worker-Key``.
The gateway's owner-only ``/api/admin/*`` calls it:

``GET  /overview``                services (state, health, image, version) and the update state
``GET  /logs?service=worker&lines=200``
``POST /restart`` {service}       ``POST /update`` (check now)   ``POST /rollback`` (previous image)
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

IMAGE = os.environ.get("EDIT_IMAGE", "ghcr.io/metrothenews/latest-avenue-edit:stable")
PROJECT = os.environ.get("COMPOSE_PROJECT") or "latest-avenue"    # replaced by our own (main())
STATE = os.environ.get("UPDATER_STATE", "/state")
KEY = os.environ.get("WORKER_KEY", "")
PORT = int(os.environ.get("UPDATER_PORT", "8796"))
INTERVAL = int(os.environ.get("UPDATE_INTERVAL", "300"))
IDLE_WAIT = int(os.environ.get("UPDATE_IDLE_WAIT", "7200"))
DRAIN_TIMEOUT = int(os.environ.get("DRAIN_TIMEOUT", "1800"))
VERIFY_SECONDS = int(os.environ.get("VERIFY_SECONDS", "240"))
GATEWAY = os.environ.get("UPDATER_GATEWAY", "http://gateway:8080")
WORKER = os.environ.get("UPDATER_WORKER", "http://worker:8795")
PREVIOUS_TAG = "latest-avenue-edit:previous"
KEEP = 4                                    # versions kept for rollbacks
# The order of a rolling restart. The updater itself goes last, on its own (self_update).
ORDER = ["worker", "gateway", "heartbeat"]
SERVICES = ["gateway", "worker", "redis", "s3", "heartbeat", "cloudflared", "updater"]
STACK_FILES = ["compose.yaml", "compose.gpu.yaml", "env.example"]
NOT_SETTINGS = {"HOSTNAME", "HOME", "PWD", "SHLVL", "TERM", "_", "PATH"}

log = logging.getLogger("updater")
_lock = threading.Lock()
_wake = threading.Event()


class UpdateError(Exception):
    pass


# -----------------------------------------------------------------------------
# Helpers.

def sh(*args, check=True, timeout=900, input=None):
    p = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, input=input)
    if check and p.returncode != 0:
        raise UpdateError("{:s}: {:s}".format(" ".join(args[:3]), (p.stderr or p.stdout).strip()[-400:]))
    return p.stdout.strip()


def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def state():
    return _read(os.path.join(STATE, "state.json"), {})


def set_state(**kw):
    st = state()
    st.update(kw, updated=time.time())
    _write(os.path.join(STATE, "state.json"), st)
    log.info("%s", {k: v for k, v in kw.items() if k != "history"})
    return st


def _event(text):
    st = state()
    hist = (st.get("history") or [])[-29:] + [{"time": time.time(), "event": text}]
    set_state(history=hist)


def _get_json(url, headers=None, timeout=5):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as resp:
        return json.load(resp)


def _post_worker(path):
    req = urllib.request.Request(WORKER + path, method="POST", data=b"{}",
                                 headers={"X-Worker-Key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def image_id(ref):
    return sh("docker", "image", "inspect", "--format", "{{.Id}}", ref, check=False) or None


def image_version(ref):
    out = sh("docker", "image", "inspect", "--format",
             '{{index .Config.Labels "org.opencontainers.image.version"}}', ref, check=False)
    return out if out and out != "<no value>" else None


def login():
    user, token = os.environ.get("GHCR_USER"), os.environ.get("GHCR_TOKEN")
    if not (user and token):
        return
    registry = IMAGE.split("/")[0]
    sh("docker", "login", registry, "-u", user, "--password-stdin", input=token, timeout=60)


# -----------------------------------------------------------------------------
# The stack's files.

def settings():
    """This container's settings (its env_file .env), minus the image's own environment."""
    image_env = sh("docker", "image", "inspect", "--format", "{{json .Config.Env}}", IMAGE, check=False)
    defaults = dict(e.split("=", 1) for e in json.loads(image_env or "[]") if "=" in e)
    return {k: v for k, v in os.environ.items()
            if k not in NOT_SETTINGS and defaults.get(k) != v and re.fullmatch(r"[A-Z][A-Z0-9_]*", k)}


def extract(ref, folder):
    """The stack files baked into the image (/opt/cloud/pc), and .env from settings()."""
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    cid = sh("docker", "create", ref, "true")
    try:
        for f in STACK_FILES:
            sh("docker", "cp", "{:s}:/opt/cloud/pc/{:s}".format(cid, f), os.path.join(folder, f), check=f != "env.example")
    finally:
        sh("docker", "rm", "-f", cid, check=False)
    with open(os.path.join(folder, ".env"), "w", encoding="utf-8") as fh:
        for k, v in sorted(settings().items()):
            fh.write("{:s}={:s}\n".format(k, v.replace("\n", "")))
    os.chmod(os.path.join(folder, ".env"), 0o600)


def compose(folder, *args, timeout=900):
    files = ["-f", os.path.join(folder, "compose.yaml")]
    if os.environ.get("USE_GPU") == "1" and os.path.exists(os.path.join(folder, "compose.gpu.yaml")):
        files += ["-f", os.path.join(folder, "compose.gpu.yaml")]
    return sh("docker", "compose", "-p", PROJECT, "--project-directory", folder,
              "--env-file", os.path.join(folder, ".env"), *files, *args, timeout=timeout)


def services_running():
    out = sh("docker", "ps", "-a", "--filter", "label=com.docker.compose.project=" + PROJECT,
             "--format", "{{json .}}", check=False)
    rows = {}
    for line in out.splitlines():
        row = json.loads(line)
        labels = dict(kv.split("=", 1) for kv in row.get("Labels", "").split(",") if "=" in kv)
        svc = labels.get("com.docker.compose.service")
        if svc:
            rows[svc] = row
    return rows


def health(container):
    out = sh("docker", "inspect", "--format",
             "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", container, check=False)
    status, _, h = out.partition(" ")
    return status or "missing", h or "none"


# -----------------------------------------------------------------------------
# A rolling update.

def _wait_idle():
    t0 = time.time()
    while time.time() - t0 < IDLE_WAIT:
        try:
            if _get_json(GATEWAY + "/status").get("sessions_free", 1) >= 1:
                return
        except Exception:
            return                      # no gateway answering: nobody is editing
        set_state(state="waiting", detail="someone is editing; the update waits until they stop")
        time.sleep(30)


def _drain_worker():
    try:
        _post_worker("/drain")
    except Exception:
        return
    t0 = time.time()
    while time.time() - t0 < DRAIN_TIMEOUT:
        try:
            st = _get_json(WORKER + "/status", {"X-Worker-Key": KEY})
        except Exception:
            return
        if st.get("running", 0) == 0 and st.get("imports_running", 0) == 0:
            return
        set_state(state="draining", detail="waiting for {:d} job(s) on the worker".format(
            st.get("running", 0) + st.get("imports_running", 0)))
        time.sleep(10)
    log.warning("drain timed out; restarting the worker anyway (its jobs run again after)")


def verify(folder, version):
    """Every service up (healthy where it has a check) and /status on the new version."""
    expected = set(compose(folder, "config", "--services").split())
    expected.discard("updater")
    t0, last = time.time(), ""
    while time.time() - t0 < VERIFY_SECONDS:
        rows = services_running()
        bad = []
        for svc in expected:
            if svc not in rows:
                bad.append(svc + " missing")
                continue
            status, h = health(rows[svc]["ID"])
            if status != "running" or h not in ("healthy", "none"):
                bad.append("{:s} {:s}/{:s}".format(svc, status, h))
        try:
            st = _get_json(GATEWAY + "/status")
            if version and st.get("version") != version:
                bad.append("gateway reports version {!s}".format(st.get("version")))
            if st.get("worker") is False:
                bad.append("gateway cannot reach the worker")
        except Exception as ex:
            bad.append("gateway /status: {!s}".format(ex))
        if not bad:
            return
        last = ", ".join(sorted(bad))
        time.sleep(5)
    raise UpdateError("not healthy after {:d}s: {:s}".format(VERIFY_SECONDS, last))


def roll_out(folder, version):
    services = set(compose(folder, "config", "--services").split())
    if "worker" in services:
        _drain_worker()
    for svc in ORDER:
        if svc in services:
            set_state(state="restarting", detail=svc)
            compose(folder, "up", "-d", "--no-deps", svc)
            if svc == "worker":
                time.sleep(3)
    rest = sorted(services - set(ORDER) - {"updater"})
    if rest:
        compose(folder, "up", "-d", *rest)    # redis, s3, cloudflared: only if their config changed
    verify(folder, version)


def deploy(new_id, reason):
    """Roll the stack onto the image IMAGE now points at (new_id), or back if it fails."""
    st = state()
    current = os.path.join(STATE, "current")
    nxt = os.path.join(STATE, "next")
    prev_id = st.get("deployed_id")
    version = image_version(IMAGE) or new_id[7:19]
    if prev_id and image_id(prev_id):
        sh("docker", "tag", prev_id, PREVIOUS_TAG)   # the way back if this fails (or later, on request)
    set_state(state="updating", detail=reason, target_version=version)
    _event("updating to {:s} ({:s})".format(version, reason))
    try:
        extract(IMAGE, nxt)
        roll_out(nxt, version)
    except Exception as ex:
        _event("update to {:s} failed: {!s}".format(version, ex))
        bad = set(st.get("bad_ids") or []) | {new_id}
        set_state(bad_ids=sorted(bad))
        if prev_id and image_id(PREVIOUS_TAG) == prev_id and os.path.isdir(current):
            set_state(state="rolling_back", detail=str(ex)[:300])
            sh("docker", "tag", PREVIOUS_TAG, IMAGE)
            try:
                roll_out(current, st.get("deployed_version"))
                set_state(state="rolled_back", detail="{:s} failed its checks: {!s}".format(version, ex)[:500])
                _event("rolled back to {!s}".format(st.get("deployed_version")))
            except Exception as ex2:
                set_state(state="broken", detail="rollback failed too: {!s}".format(ex2)[:500])
                _event("rollback failed: {!s}".format(ex2))
        else:
            set_state(state="failed", detail=str(ex)[:500])
        return False
    if os.path.isdir(current):
        shutil.rmtree(current)
    os.replace(nxt, current)
    _remember(new_id, version)
    set_state(state="deployed", detail="", deployed_id=new_id, deployed_version=version, deployed_at=time.time())
    _event("running {:s}".format(version))
    self_update(new_id)
    return True


def _remember(image, version):
    """Keep the last versions that ran well (tagged, so Docker keeps them) for rollbacks."""
    hist = [h for h in (state().get("deployed_history") or []) if h["id"] != image] + [{"id": image, "version": version}]
    for old in hist[:-KEEP]:
        sh("docker", "rmi", "latest-avenue-edit:v-" + old["version"], check=False)
    hist = hist[-KEEP:]
    tag = re.sub(r"[^A-Za-z0-9_.-]", "-", version or image[7:19])[:100]
    sh("docker", "tag", image, "latest-avenue-edit:v-" + tag, check=False)
    set_state(deployed_history=hist)


def self_update(new_id):
    """Recreate the updater on the new image, from a short-lived helper container."""
    me = services_running().get("updater")
    if not me or image_id(me["Image"]) == new_id:
        return
    vol = "{:s}_updater-state".format(PROJECT)
    sh("docker", "run", "-d", "--rm", "-u", "root", "--name", PROJECT + "-selfupdate", "--env-file",
       os.path.join(STATE, "current", ".env"), "-v", "/var/run/docker.sock:/var/run/docker.sock",
       "-v", "{:s}:{:s}".format(vol, STATE), new_id, "python3", "/opt/cloud/updater.py", "--self-update", check=False)


def bootstrap(fallback=None):
    """First start: what runs now is the deployed version, and the way back."""
    st = state()
    if st.get("deployed_id") and os.path.isdir(os.path.join(STATE, "current")):
        return
    running = services_running().get("gateway")
    run_id = (image_id(running["Image"]) if running else None) or fallback or image_id(IMAGE)
    if not run_id:
        return
    version = image_version(run_id) or "first"
    extract(run_id, os.path.join(STATE, "current"))
    set_state(deployed_id=run_id, deployed_version=version, state="deployed", detail="")
    sh("docker", "tag", run_id, PREVIOUS_TAG, check=False)
    _remember(run_id, version)


def check(reason="scheduled"):
    """One poll: pull, and deploy if the image moved. -> what happened."""
    with _lock:
        st = state()
        try:
            login()
            before = image_id(IMAGE)
            sh("docker", "pull", "-q", IMAGE, timeout=3600)
        except UpdateError as ex:
            set_state(last_check=time.time(), last_error=str(ex)[:300])
            return "pull failed: {!s}".format(ex)
        new_id = image_id(IMAGE)
        set_state(last_check=time.time(), last_error="", available_id=new_id)
        if not st.get("deployed_id"):
            bootstrap(fallback=before or new_id)
            st = state()
        if new_id == st.get("deployed_id"):
            return "up to date"
        if new_id in (st.get("bad_ids") or []) and reason == "scheduled":
            sh("docker", "tag", st["deployed_id"], IMAGE, check=False)   # keep running the good one
            return "the released image failed before; waiting for a new release"
        if reason == "scheduled":
            _wait_idle()
        return "deployed" if deploy(new_id, reason) else "rolled back"


def rollback():
    """Owner asked: go back to the version that ran before this one (marks this one bad)."""
    with _lock:                      # held throughout, so a scheduled check cannot move the tag meanwhile
        st = state()
        hist = [h for h in (st.get("deployed_history") or []) if h["id"] != st.get("deployed_id")
                and h["id"] not in (st.get("bad_ids") or []) and image_id(h["id"])]
        if not hist:
            raise UpdateError("no earlier version to go back to")
        target = hist[-1]
        set_state(bad_ids=sorted(set(st.get("bad_ids") or []) | {st.get("deployed_id")}))
        sh("docker", "tag", target["id"], IMAGE)
        return deploy(target["id"], "rollback to {:s} asked by the owner".format(target["version"] or "?"))


def _loop():
    time.sleep(10)
    try:
        with _lock:
            bootstrap()
    except Exception:
        log.exception("bootstrap")
    while True:
        try:
            if os.environ.get("UPDATE_DISABLED") == "1":
                set_state(detail="automatic updates are off (UPDATE_DISABLED=1)")
            else:
                log.info("check: %s", check())
        except Exception as ex:
            log.exception("check failed")
            set_state(last_error=str(ex)[:300])
        _wake.wait(INTERVAL)
        _wake.clear()


# -----------------------------------------------------------------------------
# Admin API (internal; the gateway calls it for the owner).

def overview():
    rows = services_running()
    out = []
    for svc in SERVICES:
        row = rows.get(svc)
        if not row:
            continue
        status, h = health(row["ID"])
        out.append({"service": svc, "state": status, "health": h, "image": row["Image"],
                    "version": image_version(row["Image"]), "since": row.get("RunningFor")})
    st = state()
    return {"services": out, "update": {k: st.get(k) for k in (
        "state", "detail", "deployed_version", "deployed_at", "target_version", "last_check", "last_error")},
        "history": (st.get("history") or [])[-10:], "image": IMAGE}


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self):
        if self.path == "/healthz":
            return True
        if not KEY or self.headers.get("X-Worker-Key") != KEY:
            self._reply(401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        if not self._ok():
            return
        url = urlsplit(self.path)
        if url.path == "/healthz":
            return self._reply(200, {"ok": True})
        if url.path == "/overview":
            return self._reply(200, overview())
        if url.path == "/logs":
            q = parse_qs(url.query)
            svc = (q.get("service") or [""])[0]
            lines = max(1, min(int((q.get("lines") or ["200"])[0]), 2000))
            row = services_running().get(svc)
            if svc not in SERVICES or not row:
                return self._reply(404, {"error": "no service {!r}".format(svc)})
            p = subprocess.run(["docker", "logs", "--tail", str(lines), "--timestamps", row["ID"]],
                               capture_output=True, text=True, timeout=30)
            text = (p.stdout + p.stderr).splitlines()
            return self._reply(200, {"service": svc, "lines": sorted(text)[-lines:]})
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        if not self._ok():
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/update":
                threading.Thread(target=lambda: log.info("asked: %s", check("asked by the owner")), daemon=True).start()
                return self._reply(200, {"started": True})
            if self.path == "/rollback":
                st = state()
                if not [h for h in (st.get("deployed_history") or []) if h["id"] != st.get("deployed_id")]:
                    return self._reply(400, {"error": "no earlier version to go back to"})
                threading.Thread(target=rollback, daemon=True).start()
                return self._reply(200, {"started": True})
            if self.path == "/restart":
                svc = body.get("service")
                if svc not in SERVICES or svc == "updater":
                    return self._reply(400, {"error": "cannot restart {!r}".format(svc)})
                if svc == "worker" and body.get("drain", True):
                    _drain_worker()
                compose(os.path.join(STATE, "current"), "restart", svc)
                return self._reply(200, {"restarted": svc})
        except (UpdateError, ValueError) as ex:
            return self._reply(400, {"error": str(ex)})
        self._reply(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass


def _own_project():
    """The compose project this container belongs to (so -p is never guessed)."""
    out = sh("docker", "inspect", "--format", '{{index .Config.Labels "com.docker.compose.project"}}',
             os.environ.get("HOSTNAME", ""), check=False)
    return out if out and out != "<no value>" else None


def main():
    global PROJECT
    logging.basicConfig(level=logging.INFO, format="%(asctime)s updater %(message)s")
    os.makedirs(STATE, exist_ok=True)
    if "--self-update" in sys.argv:
        PROJECT = (state().get("project") or PROJECT)
    else:
        PROJECT = _own_project() or PROJECT
        set_state(project=PROJECT)
    if "--self-update" in sys.argv:
        time.sleep(3)
        try:
            compose(os.path.join(STATE, "current"), "up", "-d", "--no-deps", "--force-recreate", "updater")
            _event("the updater moved to the new version")
        except Exception as ex:
            _event("the updater could not update itself: {!s}".format(ex))
            raise
        return
    if not KEY:
        sys.exit("WORKER_KEY is not set")
    threading.Thread(target=_loop, daemon=True).start()
    log.info("updater on :%d watching %s every %ds", PORT, IMAGE, INTERVAL)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
