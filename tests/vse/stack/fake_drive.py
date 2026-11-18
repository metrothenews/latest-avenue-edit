# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""tests/vse/test_imports.py's FakeDrive on 127.0.0.1:9100, inside the worker (cloud/test_stack.sh)."""
import http.server, os, sys, tempfile, time
sys.path.insert(0, "/tmp")
_orig = http.server.ThreadingHTTPServer
class Fixed(_orig):
    def __init__(self, addr, h): super().__init__(("127.0.0.1", 9100), h)
http.server.ThreadingHTTPServer = Fixed
import test_imports as t
d = t.FakeDrive(t.build_drive(tempfile.mkdtemp()), valid=(t.GOOD, t.FRESH))
print("fake drive", d.url, flush=True)
while True: time.sleep(3600)
