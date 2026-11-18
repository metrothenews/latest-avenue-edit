# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Worker restart check (cloud/test_stack.sh): submit [assembly, token job], or report their states."""
import json, sys, time, urllib.request
import os
K = {"X-Worker-Key": os.environ["WORKER_KEY"], "Content-Type": "application/json"}
def call(m, p, b=None):
    r = urllib.request.Request("http://127.0.0.1:8795" + p, method=m, headers=K, data=json.dumps(b).encode() if b else None)
    return json.load(urllib.request.urlopen(r))
if sys.argv[1] == "submit":
    a = call("POST", "/jobs", {"module": "vse_standalone.assembly", "argv": ["assemble", "/data/media/shoot/media", "--script",
             "/data/media/shoot/script.pdf", "-o", "/data/media/projects/restart2.edit.json"],
             "log": "/data/media/projects/restart.log", "outputs": ["/data/media/projects/restart2.edit.json"], "kind": "assembly"})
    b = call("POST", "/jobs", {"module": "vse_standalone.assembly.drive", "argv": ["upload", "/data/media/projects/site-test.edit.json"],
             "log": "/data/media/projects/up.log", "secrets": {"GOOGLE_ACCESS_TOKEN": "tok"}, "kind": "upload_to_drive"})
    time.sleep(1)
    print(json.dumps({"a": a["id"], "b": b["id"], "a_state": call("GET", "/jobs/" + a["id"])["state"]}))
else:
    for j in sys.argv[2:]:
        x = call("GET", "/jobs/" + j); print(j, x["state"], "attempts", x["attempts"], x.get("stored"))
