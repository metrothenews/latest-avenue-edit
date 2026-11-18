# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The import queue over /api/rpc through gateway -> worker -> storage (cloud/test_stack.sh)."""
import sys, time
import json, urllib.request, urllib.error, jwt, types
KEY = open("/tmp/key.pem").read()
def tok():
    now = int(time.time())
    return jwt.encode({"iss": "https://securetoken.google.com/test-project", "aud": "test-project", "sub": "owner123",
                       "iat": now - 10, "exp": now + 3600, "auth_time": now - 10}, KEY, algorithm="RS256", headers={"kid": "kid1"})
def rpc(m, p):
    req = urllib.request.Request("http://127.0.0.1:8080/api/rpc", method="POST",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
        headers={"Content-Type": "application/json", "Origin": "http://localhost:5173", "Authorization": "Bearer " + tok()})
    try:
        with urllib.request.urlopen(req, timeout=300) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as ex: return ex.code, json.load(ex)
def call(m, p):
    s, r = rpc(m, p)
    if "error" in r: raise RuntimeError(m + ": " + str(r["error"]))
    return r["result"]
f = types.SimpleNamespace(call=call, rpc=rpc)
GOOD = "tok-7f3a9c-good"
j = f.call("import_start", {"access_token": GOOD, "items": [{"drive_id": "root", "kind": "folder", "name": "Shoot"}]})
for _ in range(300):
    st = f.call("import_status", {"job_id": j["job_id"]})
    if st["state"] in ("done", "failed"): break
    time.sleep(1)
print("import", st["state"], st["totals"])
media = f.call("list_media", {})
print("imported", sorted(m["name"] for m in media.get("imported", [])))
j2 = f.call("fetch_drive_folder", {"folder_id": "sub", "access_token": GOOD})
for _ in range(120):
    r = f.call("job_status", {"job": j2["job"]})
    if r["state"] != "running": break
    time.sleep(1)
print("fetch_drive_folder", r["state"], r["result"])
print("bad id:", f.rpc("import_status", {"job_id": "nope"})[1].get("error"))
