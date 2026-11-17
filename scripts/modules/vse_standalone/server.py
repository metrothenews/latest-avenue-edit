# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Local control server: lets other programs (the game UI, the MCP bridge, scripts) drive the
editor while a person keeps using it.

* Bound to 127.0.0.1 only. The ``Host`` header must be ours and POSTs need ``X-LA: 1``, which
  browsers will not send cross-origin without a preflight that is never answered.
* HTTP is handled on a background thread, but every call runs on Blender's main thread
  (Blender's API is not thread-safe): requests are queued and drained by a timer, so the UI
  stays responsive and agent edits interleave safely with manual edits.
* In ``--background`` mode there is no event loop, use :func:`serve_forever`.

Endpoints:

``GET  /api/tools``  Tool list with JSON schemas (see :func:`vse_standalone.api.describe`).
``GET  /api/state``  Same as the ``get_project`` tool.
``POST /rpc``        JSON-RPC 2.0, ``{"jsonrpc": "2.0", "id": 1, "method": "<tool>", "params": {}}``.
                     Also accepts a list (batch). ``method: "tools/list"`` returns the tool list.
``GET  /files/<p>``  Files written under the server's output directory (rendered frames).
``GET  /<path>``     Static files of the UI (``VSE_UI_DIR`` or the bundled ``ui`` folder).
"""

__all__ = (
    "DEFAULT_PORT",
    "is_running",
    "start",
    "stop",
    "serve_forever",
    "url",
)

import json
import mimetypes
import os
import queue
import re
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
# Game UI preview frames: /frame/<generation>/<clip>/<color>/<look>.jpg
_FRAME = re.compile(r"^/frame/(\d+)/(\d+)/([0-8])/([0-8])\.jpg$")
DEFAULT_PORT = 8792
CALL_TIMEOUT = 600.0  # Renders can be long.

_state = {
    "server": None,
    "thread": None,
    "port": None,
    "queue": queue.Queue(),
    "timer": False,
    "log": [],
}


def _ui_dir():
    path = os.environ.get("VSE_UI_DIR")
    if path:
        return os.path.abspath(path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


def _fonts_dir():
    # Resolved on the main thread in `start()`, the HTTP threads must not call bpy.
    return _state.get("fonts_dir") or ""


def output_dir():
    """Directory for files the server hands out (rendered preview frames)."""
    path = os.path.join(tempfile.gettempdir(), "vse_standalone")
    os.makedirs(path, exist_ok=True)
    return path


def url():
    return "http://{:s}:{:d}".format(HOST, _state["port"]) if _state["port"] else None


def is_running():
    return _state["server"] is not None


def recent_log():
    """Recent calls, newest last (shown in the editor so people can see what agents did)."""
    return list(_state["log"])


# -----------------------------------------------------------------------------
# Main-thread execution.

class _Job:
    __slots__ = ("method", "params", "event", "result", "error")

    def __init__(self, method, params):
        self.method = method
        self.params = params
        self.event = threading.Event()
        self.result = None
        self.error = None


def _execute(job):
    from . import api
    from . import color_pass  # noqa: F401 (registers the color pass tools)
    from . import lyric_video  # noqa: F401 (registers the lyric video tools)
    started = time.time()
    try:
        if job.method == "tools/list":
            job.result = api.describe()
        else:
            job.result = api.call(job.method, job.params)
    except api.APIError as ex:
        job.error = {"code": -32000, "message": str(ex)}
    except Exception as ex:
        traceback.print_exc()
        job.error = {"code": -32603, "message": "{:s}: {:s}".format(type(ex).__name__, str(ex))}
    log = _state["log"]
    log.append({"time": started, "method": job.method, "ok": job.error is None,
                "seconds": round(time.time() - started, 3)})
    del log[:-200]
    job.event.set()


def _drain(budget=0.05):
    """Run queued jobs on the main thread, for at most ``budget`` seconds of wall time."""
    deadline = time.time() + budget
    while True:
        try:
            job = _state["queue"].get_nowait()
        except queue.Empty:
            return
        _execute(job)
        if time.time() > deadline:
            return


def _timer():
    if _state["server"] is None:
        _state["timer"] = False
        return None
    _drain()
    return 0.02


def _submit(method, params):
    job = _Job(method, params)
    _state["queue"].put(job)
    if not job.event.wait(CALL_TIMEOUT):
        return None, {"code": -32001, "message": "Timed out waiting for the editor"}
    return job.result, job.error


# -----------------------------------------------------------------------------
# HTTP.

def _make_handler(port):
    allowed_hosts = {"127.0.0.1:{:d}".format(port), "localhost:{:d}".format(port)}

    class Handler(BaseHTTPRequestHandler):
        server_version = "VSEStandalone/1"

        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype="application/json", cache="no-store"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def _ok_host(self):
            if self.headers.get("Host") in allowed_hosts:
                return True
            self._send(403, {"error": "forbidden"})
            return False

        def _static(self, root, rel):
            path = os.path.normpath(os.path.join(root, rel))
            if path.startswith(root + os.sep) and os.path.isfile(path):
                with open(path, "rb") as fh:
                    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
                    return self._send(200, fh.read(), ctype)
            return self._send(404, {"error": "not found"})

        def do_GET(self):
            if not self._ok_host():
                return
            path = self.path.split("?")[0]
            if path == "/api/tools":
                result, error = _submit("tools/list", {})
                return self._send(200 if error is None else 500, result or {"error": error})
            if path == "/api/state":
                result, error = _submit("color_pass_state", {})
                return self._send(200 if error is None else 500, result or {"error": error})
            if path == "/api/project":
                result, error = _submit("get_project", {})
                return self._send(200 if error is None else 500, result or {"error": error})
            m = _FRAME.match(path)
            if m:
                result, error = _submit("color_pass_preview", {
                    "index": int(m[2]), "color": int(m[3]), "look": int(m[4])})
                if error is not None:
                    return self._send(500, {"error": error["message"][:300]})
                if not result.get("exists"):
                    return self._send(404, {"error": "not ready"})
                with open(result["filepath"], "rb") as fh:
                    return self._send(200, fh.read(), "image/jpeg", "max-age=3600")
            if path == "/api/log":
                return self._send(200, recent_log())
            if path.startswith("/fonts/"):
                return self._static(_fonts_dir(), path[len("/fonts/"):])
            if path.startswith("/files/"):
                return self._static(output_dir(), path[len("/files/"):])
            return self._static(_ui_dir(), "index.html" if path == "/" else path.lstrip("/"))

        def do_POST(self):
            if not self._ok_host():
                return
            if self.headers.get("X-LA") != "1":
                return self._send(403, {"error": "forbidden"})
            if self.path in _UI_POSTS:
                return self._ui_post()
            if self.path != "/rpc":
                return self._send(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, TypeError):
                return self._send(400, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": -32700, "message": "Parse error"}})
            batch = isinstance(payload, list)
            responses = []
            for request in (payload if batch else [payload]):
                if not isinstance(request, dict) or not isinstance(request.get("method"), str):
                    responses.append({"jsonrpc": "2.0", "id": None,
                                      "error": {"code": -32600, "message": "Invalid request"}})
                    continue
                params = request.get("params") or {}
                if not isinstance(params, dict):
                    responses.append({"jsonrpc": "2.0", "id": request.get("id"),
                                      "error": {"code": -32602,
                                                "message": "params must be an object"}})
                    continue
                result, error = _submit(request["method"], params)
                response = {"jsonrpc": "2.0", "id": request.get("id")}
                if error is None:
                    response["result"] = result
                else:
                    response["error"] = error
                responses.append(response)
            return self._send(200, responses if batch else responses[0])

        def _ui_post(self):
            """The game UI's small JSON API, mapped onto tools."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length) or b"{}")
                method, params = _UI_POSTS[self.path](data)
            except (ValueError, KeyError, TypeError) as ex:
                return self._send(400, {"error": str(ex)})
            result, error = _submit(method, params)
            if error is not None:
                return self._send(400, {"error": error["message"]})
            return self._send(200, {"ok": True, "started": True, "result": result})

    return Handler


_UI_POSTS = {
    "/api/load": lambda d: ("color_pass_load", {"mode": d.get("mode", "night")}),
    "/api/zoom": lambda d: ("zoom_all_clips", {"zoom": float(d["zoom"])}),
    "/api/apply": lambda d: ("color_pass_apply", {
        "index": int(d["index"]), "pass_name": str(d["pass"]), "slot": int(d["slot"]),
        "generation": int(d["generation"])}),
}


def start(port=None):
    """Start the server (no-op if running). Returns the URL."""
    import bpy
    if _state["server"] is not None:
        return url()
    port = int(port or os.environ.get("VSE_PORT") or DEFAULT_PORT)
    _state["fonts_dir"] = os.path.abspath(bpy.utils.system_resource('DATAFILES', path="fonts") or "")
    server = ThreadingHTTPServer((HOST, port), _make_handler(port))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="vse-rpc", daemon=True)
    thread.start()
    _state.update(server=server, thread=thread, port=port)
    if not bpy.app.background and not _state["timer"]:
        bpy.app.timers.register(_timer, first_interval=0.1, persistent=True)
        _state["timer"] = True
    print("VSE control server: {:s}".format(url()))
    return url()


def stop():
    server = _state["server"]
    if server is None:
        return
    server.shutdown()
    server.server_close()
    _state.update(server=None, thread=None, port=None)


def serve_forever(port=None):
    """Blocking loop for ``--background`` use: ``blender -b --python-expr "..."``."""
    start(port)
    try:
        while _state["server"] is not None:
            try:
                job = _state["queue"].get(timeout=0.25)
            except queue.Empty:
                continue
            _execute(job)
    except KeyboardInterrupt:
        pass
    finally:
        stop()
