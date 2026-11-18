# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Stand-in Google token endpoint + Firestore PATCH (cloud/test_stack.sh). Checks the JWT's signature."""
import json, urllib.parse, jwt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PUB = open("/fake/sa.pub").read()
docs, beats = {}, []
class H(BaseHTTPRequestHandler):
    def _r(self, code, d):
        b = json.dumps(d).encode(); self.send_response(code); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        body = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        try:
            c = jwt.decode(body["assertion"][0], PUB, algorithms=["RS256"], audience="http://fakefs:9000/token")
            assert c["scope"].endswith("/auth/datastore")
        except Exception as ex:
            return self._r(400, {"error": str(ex)})
        self._r(200, {"access_token": "fake-access", "expires_in": 3600})
    def do_PATCH(self):
        if self.headers.get("Authorization") != "Bearer fake-access":
            return self._r(401, {"error": "bad token"})
        d = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        p = urllib.parse.urlsplit(self.path)
        beats.append({"path": p.path, "mask": urllib.parse.parse_qs(p.query).get("updateMask.fieldPaths"), "fields": d["fields"]})
        self._r(200, {"name": p.path, "fields": d["fields"]})
    def do_GET(self):
        self._r(200, beats[-3:] + [{"count": len(beats)}])
    def log_message(self, *a): pass
ThreadingHTTPServer(("0.0.0.0", 9000), H).serve_forever()
