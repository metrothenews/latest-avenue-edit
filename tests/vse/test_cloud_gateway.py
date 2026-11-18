#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Security and function test for the cloud gateway (cloud/gateway.py).

Run against a gateway started with ``GATEWAY_CERTS_FILE`` pointing at test certificates:

    python3 tests/vse/test_cloud_gateway.py http://127.0.0.1:8090 key_good.pem key_bad.pem

Arguments: gateway URL, the private key matching the trusted certificate (key ID ``kid1``),
and a private key the gateway does not trust. Expects FIREBASE_PROJECT_ID=test-project,
ALLOWED_UIDS=owner123 and http://localhost:5173 among ALLOWED_ORIGINS.
"""

import asyncio
import sys
import time

import aiohttp
import jwt

BASE, GOOD, BAD = sys.argv[1], open(sys.argv[2]).read(), open(sys.argv[3]).read()
ORIGIN = "http://localhost:5173"
PROJECT = "test-project"
failures = []


def token(key=GOOD, kid="kid1", alg="RS256", **over):
    now = int(time.time())
    claims = {"iss": "https://securetoken.google.com/" + PROJECT, "aud": PROJECT,
              "sub": "owner123", "iat": now - 10, "exp": now + 3600, "auth_time": now - 10,
              "email": "owner@example.com"}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(detail)[:300]))
    if not cond:
        failures.append(name)


async def ws_result(session, tok, origin=ORIGIN):
    """Return 'RFB' if the stream opens and VNC answers, else the HTTP status."""
    protocols = ["binary"] + (["auth." + tok] if tok else [])
    headers = {"Origin": origin} if origin else {}
    try:
        async with session.ws_connect(BASE + "/stream", protocols=protocols, headers=headers,
                                      timeout=aiohttp.ClientWSTimeout(ws_close=5)) as ws:
            msg = await ws.receive(timeout=10)
            return "RFB" if msg.type == aiohttp.WSMsgType.BINARY and msg.data.startswith(b"RFB ") \
                else "unexpected {!r}".format(msg)
    except aiohttp.WSServerHandshakeError as ex:
        return ex.status


async def main():
    now = int(time.time())
    async with aiohttp.ClientSession(trust_env=False) as s:
        async with s.get(BASE + "/healthz") as r:
            check("healthz is open and says nothing", r.status == 200 and (await r.text()) == "ok")

        denied = {
            "no token": None,
            "garbage token": "aaa.bbb.ccc",
            "signed by an untrusted key": token(key=BAD),
            "untrusted key claiming a trusted key id": token(key=BAD, kid="kid1"),
            "unknown key id": token(kid="other"),
            "expired": token(exp=now - 120, iat=now - 4000),
            "wrong project (audience)": token(aud="another-project"),
            "wrong issuer": token(iss="https://securetoken.google.com/another-project"),
            "a different signed-in user": token(sub="someone-else"),
            "no user id": token(sub=None),
            "HS256 signed with the public certificate": None,  # filled below
        }
        for name, tok in denied.items():
            if name.startswith("HS256"):
                continue
            check("stream refused: " + name, await ws_result(s, tok) == 401)
            headers = {"Origin": ORIGIN}
            if tok:
                headers["Authorization"] = "Bearer " + tok
            async with s.post(BASE + "/api/rpc", json={"method": "ping"}, headers=headers) as r:
                body = await r.text()
                check("rpc refused: " + name, r.status == 401 and "forbidden" in body, body)

        # The chat with the editing agent is behind the same check.
        bad = token(sub="someone-else")
        for method, path in (("GET", "/api/chat/state"), ("GET", "/api/chat/events?after=0&wait=0"),
                             ("POST", "/api/chat/send"), ("POST", "/api/chat/new"),
                             ("POST", "/api/chat/stop"), ("POST", "/api/chat/feedback")):
            for name, tok in (("no token", None), ("a different user", bad)):
                headers = {"Origin": ORIGIN}
                if tok:
                    headers["Authorization"] = "Bearer " + tok
                async with s.request(method, BASE + path, headers=headers,
                                     json={"text": "delete everything"} if method == "POST" else None) as r:
                    check("chat refused ({:s}): {:s} {:s}".format(name, method, path.split("?")[0]),
                          r.status == 401)

        # Algorithm confusion: a token "signed" with HMAC must never be accepted.
        hs = jwt.encode({"iss": "https://securetoken.google.com/" + PROJECT, "aud": PROJECT,
                         "sub": "owner123", "iat": now, "exp": now + 3600},
                        "x" * 64, algorithm="HS256", headers={"kid": "kid1"})
        check("stream refused: HS256 token", await ws_result(s, hs) == 401)
        none = jwt.encode({"iss": "https://securetoken.google.com/" + PROJECT, "aud": PROJECT,
                           "sub": "owner123", "iat": now, "exp": now + 3600},
                          None, algorithm="none", headers={"kid": "kid1"})
        check("stream refused: unsigned (alg none) token", await ws_result(s, none) == 401)

        good = token()
        check("stream refused: valid token from another website",
              await ws_result(s, good, origin="https://evil.example") == 401)
        async with s.get(BASE + "/api/status", headers={"Authorization": "Bearer " + good,
                                                         "Origin": "https://evil.example"}) as r:
            check("status refused from another website", r.status == 401)
        async with s.get(BASE + "/stream?token=" + good, headers={"Origin": ORIGIN}) as r:
            check("token in the URL is not accepted", r.status == 401)
        for path in ("/", "/rpc", "/api/tools", "/files/x", "/frame/1/0/0/0.jpg"):
            async with s.get(BASE + path) as r:
                check("editor's own routes are not exposed: " + path, r.status in (404, 405))

        # Allowed. The editor starts on the first signed-in request; wait for it as the page does.
        auth = {"Authorization": "Bearer " + good, "Origin": ORIGIN}
        ready = False
        waited = time.time()
        while time.time() - waited < 120 and not ready:
            async with s.get(BASE + "/api/status", headers=auth) as r:
                ready = r.status == 200 and (await r.json()).get("editor_ready") is True
            if not ready:
                await asyncio.sleep(2)
        check("editor starts for the owner (took {:.0f} s)".format(time.time() - waited), ready)
        check("stream opens for the owner", await ws_result(s, good) == "RFB")
        async with s.get(BASE + "/api/status", headers=auth) as r:
            data = await r.json()
            check("status for the owner", r.status == 200 and data.get("editor_ready") is True, data)
            check("CORS header only for an allowed origin",
                  r.headers.get("Access-Control-Allow-Origin") == ORIGIN)
        async with s.post(BASE + "/api/rpc", json={"id": 7, "method": "ping"}, headers=auth) as r:
            data = await r.json()
            check("rpc reaches the editor", r.status == 200 and data.get("id") == 7
                  and data.get("result", {}).get("ok") is True, data)
        async with s.post(BASE + "/api/rpc", json={"method": "nope"}, headers=auth) as r:
            data = await r.json()
            check("rpc errors come back", "error" in data, data)

        async with s.get(BASE + "/api/chat/state", headers=auth) as r:
            data = await r.json()
            check("chat state for the owner", r.status == 200 and "llm" in data and data["busy"] is False, data)
        async with s.post(BASE + "/api/chat/send", json={"text": "  "}, headers=auth) as r:
            check("empty chat message is rejected", r.status == 400)
        async with s.post(BASE + "/api/chat/feedback", json={"seq": 1, "rating": "maybe"}, headers=auth) as r:
            check("bad feedback is rejected", r.status == 400)

        # A stream ends when its token expires.
        short = token(exp=now + 4)
        started = time.time()
        async with s.ws_connect(BASE + "/stream", protocols=["binary", "auth." + short],
                                headers={"Origin": ORIGIN}) as ws:
            first = await ws.receive(timeout=10)
            closed = False
            while time.time() - started < 50:
                msg = await ws.receive(timeout=50)
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.CLOSING):
                    closed = True
                    break
            took = time.time() - started
            check("stream ends when the token expires", first.data.startswith(b"RFB ") and closed
                  and took < 45, took)

    print("\n{:d} failed".format(len(failures)))
    sys.exit(1 if failures else 0)


asyncio.run(main())
