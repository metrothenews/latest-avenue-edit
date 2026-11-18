#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Heartbeat: every ``HEARTBEAT_SECONDS`` (60) write ``editServer/status`` in Firestore:

    {lastSeen: <now>, version: <running version>, online, health: ok|degraded,
     update: deployed|updating|rolled_back|…, queueDepth: <n>, sessionsFree: <n>}

The website reads that document (it can, even when the PC is off) and shows "the edit
server is offline" when ``lastSeen`` is older than about three minutes, instead of a
connection that never answers. Signed in with a Google service account that may only write
Firestore (``HEARTBEAT_CREDENTIALS``: its JSON key file, mounted read-only, never in the image
or the repository; cloud/pc/SERVER.md says how to make one). In the compose stack the key
comes base64-encoded in ``HEARTBEAT_SA_B64`` (the .env file), so nothing is mounted.

Settings: ``FIREBASE_PROJECT_ID``, ``HEARTBEAT_CREDENTIALS``, ``VSE_VERSION``,
``HEARTBEAT_STATUS_URL`` (the gateway's public /status, for queue depth; default
http://gateway:8080/status), ``HEARTBEAT_SECONDS``. Tests only: ``FIRESTORE_URL`` (another
Firestore address) and the key file's own ``token_uri``.
"""

import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request

import jwt

log = logging.getLogger("heartbeat")
SCOPE = "https://www.googleapis.com/auth/datastore"


def _version():
    """VSE_VERSION if set, else the version baked into the image (/opt/cloud/VERSION)."""
    if os.environ.get("VSE_VERSION"):
        return os.environ["VSE_VERSION"]
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip() or "dev"
    except OSError:
        return "dev"


class Heartbeat:
    def __init__(self, credentials, project, firestore=None, status_url=None, version="dev"):
        if isinstance(credentials, dict):
            self.sa = credentials
        else:
            with open(credentials, encoding="utf-8") as fh:
                self.sa = json.load(fh)
        self.project = project
        self.firestore = (firestore or "https://firestore.googleapis.com").rstrip("/")
        self.status_url = status_url
        self.version = version
        self.token, self.token_expiry = None, 0

    def _access_token(self):
        if self.token and time.time() < self.token_expiry - 120:
            return self.token
        now = int(time.time())
        token_uri = self.sa.get("token_uri") or "https://oauth2.googleapis.com/token"
        assertion = jwt.encode({"iss": self.sa["client_email"], "scope": SCOPE, "aud": token_uri,
                                "iat": now, "exp": now + 3600}, self.sa["private_key"], algorithm="RS256",
                               headers={"kid": self.sa.get("private_key_id")} if self.sa.get("private_key_id") else None)
        body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                       "assertion": assertion}).encode()
        with urllib.request.urlopen(urllib.request.Request(token_uri, data=body), timeout=20) as resp:
            reply = json.load(resp)
        self.token, self.token_expiry = reply["access_token"], now + int(reply.get("expires_in", 3600))
        return self.token

    def _local_status(self):
        if not self.status_url:
            return {}
        try:
            with urllib.request.urlopen(self.status_url, timeout=5) as resp:
                return json.load(resp)
        except Exception:
            return {"online": False}

    def beat(self):
        st = self._local_status()
        fields = {"lastSeen": {"timestampValue": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  "version": {"stringValue": self.version},
                  "online": {"booleanValue": bool(st.get("online", True))},
                  "queueDepth": {"integerValue": str(int(st.get("queue_depth") or 0))},
                  "sessionsFree": {"integerValue": str(int(st.get("sessions_free") or 0))},
                  "health": {"stringValue": st.get("health") or ("ok" if st.get("online", True) else "down")},
                  "update": {"stringValue": st.get("update") or ""}}
        mask = "&".join("updateMask.fieldPaths=" + k for k in fields)
        url = "{:s}/v1/projects/{:s}/databases/(default)/documents/editServer/status?{:s}".format(
            self.firestore, self.project, mask)
        req = urllib.request.Request(url, method="PATCH", data=json.dumps({"fields": fields}).encode(),
                                     headers={"Authorization": "Bearer " + self._access_token(),
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s heartbeat %(message)s")
    creds, project = os.environ.get("HEARTBEAT_CREDENTIALS"), os.environ.get("FIREBASE_PROJECT_ID")
    if os.environ.get("HEARTBEAT_SA_B64"):
        import base64
        try:
            creds = json.loads(base64.b64decode(os.environ["HEARTBEAT_SA_B64"]))
        except ValueError:
            log.warning("HEARTBEAT_SA_B64 is not a base64-encoded service account key")
            creds = None
    elif creds and not os.path.isfile(creds):
        creds = None
    if not creds or not project:
        log.warning("no service account key (HEARTBEAT_SA_B64) or FIREBASE_PROJECT_ID: heartbeat off")
        while True:                  # stay up so compose does not restart it in a loop
            time.sleep(3600)
    hb = Heartbeat(creds, project, os.environ.get("FIRESTORE_URL"),
                   os.environ.get("HEARTBEAT_STATUS_URL", "http://gateway:8080/status"),
                   _version())
    every = int(os.environ.get("HEARTBEAT_SECONDS", "60"))
    while True:
        try:
            hb.beat()
            log.info("beat")
        except Exception as ex:      # the internet is down: try again next minute
            log.warning("beat failed: %s", ex)
        time.sleep(every)


if __name__ == "__main__":
    sys.exit(main())
