#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Authenticated gateway for the cloud editor.

This is the only process that listens on a public port. Everything behind it (the VNC
server showing the editor, the editor's control server) listens on 127.0.0.1 only.

Every request except ``/healthz`` must carry a Firebase ID token for an allowed user
(the site owner). Tokens are verified here, on the server, against Google's public keys:
signature, expiry, project (audience), issuer and user ID. The web page hiding itself from
other people is not what protects the compute; this check is.

Endpoints:

``GET  /healthz``      liveness for the platform. No information, no auth.
``GET  /api/status``   ``Authorization: Bearer <token>``. Editor state.
``POST /api/rpc``      ``Authorization: Bearer <token>``. JSON-RPC to the editor's tools.
``GET  /api/chat/state``   chat and language model state.
``GET  /api/chat/events``  ``?after=<seq>&wait=<seconds>``: the chat's events (long poll).
``POST /api/chat/send``    ``{"text": ...}``: a message for the editing agent (cloud/agent.py).
``POST /api/chat/stop``, ``/api/chat/new``, ``/api/chat/feedback`` (``{"seq", "rating", "note"}``).
                       All of them need the bearer token, like every other call.
``GET  /stream``       WebSocket carrying the VNC (RFB) stream. The token travels in the
                       ``Sec-WebSocket-Protocol`` header as ``auth.<token>`` next to
                       ``binary``, so it never appears in a URL or a request log.

Environment:

``PORT``                     listen port (default 8080; Cloud Run sets it).
``FIREBASE_PROJECT_ID``      required. Tokens must be issued for this project.
``ALLOWED_UIDS``             required. Comma-separated Firebase user IDs allowed in.
``ALLOWED_ORIGINS``          comma-separated web origins allowed to connect.
``VNC_PORT``                 local VNC port (default 5901).
``VSE_PORT``                 local editor control port (default 8792).
``VSE_DATA``                 data folder; autosaves go to ``$VSE_DATA/projects``.
``IDLE_EXIT_MINUTES``        exit after this long with nobody connected (0 = never). For
                             VMs that shut down when the container exits. Cloud Run scales
                             to zero by itself and does not need it.
``EDITOR_AUTOSTART``         ``1`` starts the editor with the gateway instead of on the
                             first signed-in request.
``GATEWAY_CERTS_FILE``       tests only: JSON file of signing certificates to trust instead
                             of fetching Google's.
"""

import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time

import aiohttp
import jwt
from aiohttp import web
from cryptography import x509

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent import Agent  # noqa: E402

CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"

PORT = int(os.environ.get("PORT", "8080"))
PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
ALLOWED_UIDS = {u.strip() for u in os.environ.get("ALLOWED_UIDS", "").split(",") if u.strip()}
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get(
    "ALLOWED_ORIGINS",
    "https://latestavenue.com,https://www.latestavenue.com,https://latest-avenue-app.web.app",
).split(",") if o.strip()}
VNC_PORT = int(os.environ.get("VNC_PORT", "5901"))
VSE_PORT = int(os.environ.get("VSE_PORT", "8792"))
VSE_DATA = os.environ.get("VSE_DATA", "/data")
IDLE_EXIT_MINUTES = float(os.environ.get("IDLE_EXIT_MINUTES", "0"))
AUTOSAVE_SECONDS = 120
MAX_RPC_BYTES = 1 << 20

log = logging.getLogger("gateway")


# -----------------------------------------------------------------------------
# Token verification.

class AuthError(Exception):
    pass


class Verifier:
    def __init__(self):
        self._keys = {}
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def _load_keys(self, session):
        path = os.environ.get("GATEWAY_CERTS_FILE")
        if path:
            with open(path, encoding="utf-8") as fh:
                certs = json.load(fh)
            max_age = 3600
        else:
            async with session.get(CERTS_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                certs = await resp.json()
                match = re.search(r"max-age=(\d+)", resp.headers.get("Cache-Control", ""))
                max_age = int(match.group(1)) if match else 3600
        keys = {}
        for kid, pem in certs.items():
            keys[kid] = x509.load_pem_x509_certificate(pem.encode()).public_key()
        self._keys = keys
        self._expires = time.time() + max(60, min(max_age, 24 * 3600))

    async def verify(self, token, session):
        """Return the token's claims, or raise :class:`AuthError`."""
        if not token or token.count(".") != 2 or len(token) > 8192:
            raise AuthError("missing or malformed token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthError("malformed token") from None
        if header.get("alg") != "RS256":
            raise AuthError("unexpected algorithm")
        kid = header.get("kid")
        async with self._lock:
            if time.time() > self._expires or kid not in self._keys:
                try:
                    await self._load_keys(session)
                except Exception as ex:
                    if not self._keys:
                        raise AuthError("cannot fetch signing keys: {:s}".format(str(ex))) from None
        key = self._keys.get(kid)
        if key is None:
            raise AuthError("unknown signing key")
        try:
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=PROJECT_ID,
                issuer="https://securetoken.google.com/" + PROJECT_ID,
                options={"require": ["exp", "iat", "aud", "iss", "sub"]}, leeway=30)
        except jwt.PyJWTError as ex:
            raise AuthError(str(ex)) from None
        uid = claims.get("sub") or ""
        if claims.get("auth_time", 0) > time.time() + 30:
            raise AuthError("auth_time in the future")
        if uid not in ALLOWED_UIDS:
            raise AuthError("user is not allowed")
        return claims


def _origin_ok(request):
    origin = request.headers.get("Origin")
    # Browsers always send Origin on cross-site and WebSocket requests. Requests without one
    # (curl, scripts) are still required to carry a valid token.
    return origin is None or origin.rstrip("/") in ALLOWED_ORIGINS


def _cors(request, response):
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
    return response


def _deny(request, status, reason):
    log.warning("denied %s %s from %s: %s", request.method, request.path,
                request.headers.get("X-Forwarded-For", request.remote), reason)
    # The reason is logged, not returned: a caller without access learns nothing.
    return _cors(request, web.json_response({"error": "forbidden"}, status=status))


async def _require_bearer(request):
    if not _origin_ok(request):
        raise AuthError("origin not allowed")
    value = request.headers.get("Authorization", "")
    if not value.startswith("Bearer "):
        raise AuthError("no bearer token")
    return await request.app["verifier"].verify(value[7:].strip(), request.app["http"])


# -----------------------------------------------------------------------------
# Editor process.

def _ensure_editor(app):
    """Start the editor if it is not running. Only called after a request passed sign-in."""
    proc = app["state"].get("editor")
    if proc is not None and proc.poll() is None:
        return
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "editor.sh")
    log.info("starting the editor")
    app["state"]["editor"] = subprocess.Popen([script], stdin=subprocess.DEVNULL)


# -----------------------------------------------------------------------------
# Editor control server.

async def _editor_rpc(app, method, params=None, timeout=900):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    url = "http://127.0.0.1:{:d}/rpc".format(VSE_PORT)
    headers = {"X-LA": "1", "Host": "127.0.0.1:{:d}".format(VSE_PORT)}
    async with app["http"].post(url, json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        return await resp.json()


async def _autosave(app, why):
    """Save the project so a stopped instance loses nothing."""
    proc = app["state"].get("editor")
    if proc is None or proc.poll() is not None:
        return
    try:
        reply = await _editor_rpc(app, "get_project", timeout=20)
        project = reply.get("result") or {}
        if project.get("filepath"):
            if not project.get("is_dirty"):
                return
            await _editor_rpc(app, "save_project", timeout=120)
        else:
            folder = os.path.join(VSE_DATA, "projects")
            os.makedirs(folder, exist_ok=True)
            await _editor_rpc(app, "save_project",
                              {"filepath": os.path.join(folder, "autosave.blend")}, timeout=120)
        log.info("autosaved (%s)", why)
    except Exception as ex:
        log.warning("autosave failed (%s): %s", why, ex)


# -----------------------------------------------------------------------------
# Handlers.

async def healthz(_request):
    return web.Response(text="ok")


async def options(request):
    return _cors(request, web.Response(status=204))


async def status(request):
    try:
        claims = await _require_bearer(request)
    except AuthError as ex:
        return _deny(request, 401, str(ex))
    _ensure_editor(request.app)
    try:
        reply = await _editor_rpc(request.app, "get_project", timeout=15)
        editor = reply.get("result")
    except Exception:
        editor = None
    return _cors(request, web.json_response({
        "ok": True, "user": claims.get("email") or claims["sub"],
        "editor_ready": editor is not None, "project": editor,
        "viewers": len(request.app["streams"]),
    }))


async def rpc(request):
    try:
        await _require_bearer(request)
    except AuthError as ex:
        return _deny(request, 401, str(ex))
    _ensure_editor(request.app)
    body = await request.read()
    if len(body) > MAX_RPC_BYTES:
        return _cors(request, web.json_response({"error": "too large"}, status=413))
    try:
        payload = json.loads(body or b"{}")
        method = payload["method"]
        params = payload.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return _cors(request, web.json_response({"error": "bad request"}, status=400))
    try:
        reply = await _editor_rpc(request.app, method, params)
    except Exception:
        return _cors(request, web.json_response({"error": "editor not responding"}, status=503))
    reply["id"] = payload.get("id")
    return _cors(request, web.json_response(reply))


# -----------------------------------------------------------------------------
# Chat with the editing agent.

async def _chat_auth(request):
    await _require_bearer(request)
    _ensure_editor(request.app)
    return request.app["agent"]


async def _json_body(request):
    body = await request.read()
    if len(body) > MAX_RPC_BYTES:
        raise ValueError("too large")
    data = json.loads(body or b"{}")
    if not isinstance(data, dict):
        raise ValueError("expected an object")
    return data


async def chat_state(request):
    try:
        agent = await _chat_auth(request)
    except AuthError as ex:
        return _deny(request, 401, str(ex))
    return _cors(request, web.json_response(agent.state()))


async def chat_events(request):
    try:
        agent = await _chat_auth(request)
    except AuthError as ex:
        return _deny(request, 401, str(ex))
    try:
        after = int(request.query.get("after", "0"))
        wait = max(0.0, min(float(request.query.get("wait", "25")), 30.0))
    except ValueError:
        return _cors(request, web.json_response({"error": "bad request"}, status=400))
    events = await agent.wait_events(after, wait)
    return _cors(request, web.json_response({"events": events, **agent.state()}))


async def chat_post(request):
    try:
        agent = await _chat_auth(request)
    except AuthError as ex:
        return _deny(request, 401, str(ex))
    action = request.match_info["action"]
    try:
        data = await _json_body(request)
        if action == "send":
            agent.send(data.get("text", ""))
        elif action == "stop":
            agent.stop()
        elif action == "new":
            if agent.busy:
                raise RuntimeError("still working on the last message")
            agent.new_session()
        elif action == "feedback":
            agent.feedback(data["seq"], data["rating"], data.get("note", ""))
        else:
            return _cors(request, web.json_response({"error": "not found"}, status=404))
    except RuntimeError as ex:
        return _cors(request, web.json_response({"error": str(ex)}, status=409))
    except (ValueError, KeyError, TypeError) as ex:
        return _cors(request, web.json_response({"error": "bad request: " + str(ex)[:100]},
                                                status=400))
    request.app["state"]["last_activity"] = time.time()
    return _cors(request, web.json_response({"ok": True, **agent.state()}))


async def stream(request):
    """WebSocket <-> VNC TCP relay, after verifying the token."""
    offered = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")]
    token = next((p[5:] for p in offered if p.startswith("auth.")), "")
    try:
        if not _origin_ok(request):
            raise AuthError("origin not allowed")
        claims = await request.app["verifier"].verify(token, request.app["http"])
    except AuthError as ex:
        return _deny(request, 401, str(ex))

    _ensure_editor(request.app)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", VNC_PORT)
    except OSError:
        return _cors(request, web.json_response({"error": "editor display not ready"}, status=503))

    ws = web.WebSocketResponse(protocols=["binary"], max_msg_size=4 << 20, heartbeat=25)
    await ws.prepare(request)
    app = request.app
    app["streams"].add(ws)
    app["state"]["last_activity"] = time.time()
    log.info("stream opened for %s (%d viewing)", claims.get("email") or claims["sub"],
             len(app["streams"]))
    # A token is valid for at most an hour; end the stream when it expires so access
    # that has been revoked does not live on in an open connection. The page reconnects
    # with a fresh token by itself.
    expires = float(claims["exp"])

    async def vnc_to_ws():
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await ws.send_bytes(data)

    async def ws_to_vnc():
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                writer.write(msg.data)
                await writer.drain()
                app["state"]["last_activity"] = time.time()
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                break

    async def until_expiry():
        await asyncio.sleep(max(0.0, expires - time.time()))

    tasks = [asyncio.ensure_future(t) for t in (vnc_to_ws(), ws_to_vnc(), until_expiry())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        writer.close()
        app["streams"].discard(ws)
        app["state"]["last_activity"] = time.time()
        await ws.close()
        log.info("stream closed (%d viewing)", len(app["streams"]))
        if not app["streams"]:
            asyncio.ensure_future(_autosave(app, "last viewer left"))
    return ws


# -----------------------------------------------------------------------------
# Background work.

async def _housekeeping(app):
    last_save = time.time()
    while True:
        await asyncio.sleep(15)
        now = time.time()
        await app["agent"].housekeeping()
        if app["agent"].busy:
            app["state"]["last_activity"] = now
        if app["streams"] and now - last_save > AUTOSAVE_SECONDS:
            last_save = now
            await _autosave(app, "periodic")
        if IDLE_EXIT_MINUTES > 0 and not app["streams"] and \
                now - app["state"]["last_activity"] > IDLE_EXIT_MINUTES * 60:
            log.info("idle for %.0f minutes, exiting", IDLE_EXIT_MINUTES)
            await _autosave(app, "idle exit")
            os.kill(os.getpid(), signal.SIGTERM)
            return


async def _on_startup(app):
    app["http"] = aiohttp.ClientSession(trust_env=False)
    app["agent"] = Agent(app["http"])
    app["housekeeping"] = asyncio.ensure_future(_housekeeping(app))
    if os.environ.get("EDITOR_AUTOSTART") == "1":
        _ensure_editor(app)


async def _on_cleanup(app):
    app["housekeeping"].cancel()
    # Cloud Run sends SIGTERM before stopping an instance: save first.
    await app["agent"].close()
    proc = app["state"].get("editor")
    if proc is not None and proc.poll() is None:
        await _autosave(app, "shutdown")
        proc.terminate()
    await app["http"].close()


def make_app():
    if not PROJECT_ID or not ALLOWED_UIDS:
        sys.exit("FIREBASE_PROJECT_ID and ALLOWED_UIDS must be set: refusing to start "
                 "without an access list.")
    app = web.Application(client_max_size=MAX_RPC_BYTES)
    app["verifier"] = Verifier()
    app["streams"] = set()
    app["state"] = {"last_activity": time.time()}
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/rpc", rpc)
    app.router.add_get("/stream", stream)
    app.router.add_get("/api/chat/state", chat_state)
    app.router.add_get("/api/chat/events", chat_events)
    app.router.add_post("/api/chat/{action}", chat_post)
    app.router.add_route("OPTIONS", "/api/{tail:.*}", options)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    web.run_app(make_app(), host="0.0.0.0", port=PORT, access_log=None)
