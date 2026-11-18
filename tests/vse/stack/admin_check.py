# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The owner's /api/admin calls through the gateway (cloud/test_stack.sh). Prints "ok <what>"."""
import json
import time
import urllib.error
import urllib.request

import jwt

KEY = open("/tmp/key.pem").read()
BASE = "http://127.0.0.1:8080/api/admin/"


def tok():
    now = int(time.time())
    return jwt.encode({"iss": "https://securetoken.google.com/test-project", "aud": "test-project", "sub": "owner123",
                       "iat": now - 10, "exp": now + 3600, "auth_time": now - 10}, KEY, algorithm="RS256",
                      headers={"kid": "kid1"})


def call(path, body=None, auth=True, timeout=300):
    headers = {"Content-Type": "application/json", "Origin": "http://localhost:5173"}
    if auth:
        headers["Authorization"] = "Bearer " + tok()
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as ex:
        return ex.code, json.load(ex)


code, _ = call("overview", auth=False)
print("ok unauthenticated" if code == 401 else "FAIL unauthenticated", code)

code, ov = call("overview")
names = {s["service"] for s in ((ov.get("stack") or {}).get("services") or [])}
ok = code == 200 and {"gateway", "worker", "redis", "s3", "updater"} <= names and "queue_depth" in (ov.get("queue") or {})
print("ok overview" if ok else "FAIL overview", code, sorted(names), (ov.get("stack") or {}).get("update"))

code, logs = call("logs?service=worker&lines=20")
print("ok logs" if code == 200 and logs.get("lines") else "FAIL logs", code, (logs.get("lines") or [""])[-1][:120])

code, st = call("drain", {})
print("ok drain" if code == 200 and st.get("draining") is True else "FAIL drain", code, st)
code, st = call("undrain", {})
print("ok undrain" if code == 200 and st.get("draining") is False else "FAIL undrain", code, st)

code, st = call("restart", {"service": "worker"})
time.sleep(2)
healthy = False
for _ in range(60):
    _, ov = call("overview")
    w = [s for s in ov["stack"]["services"] if s["service"] == "worker"]
    if w and w[0]["state"] == "running" and w[0]["health"] == "healthy" and (ov.get("queue") or {}).get("draining") is False:
        healthy = True
        break
    time.sleep(2)
print("ok restart" if code == 200 and healthy else "FAIL restart", code, st)
code, _ = call("restart", {"service": "nonsense"})
print("refused bad service:", code)
