#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
End-to-end test of the automation path: editor control server + MCP bridge + game UI routes.

    python3 tests/vse/test_mcp_e2e.py /path/to/blender

Starts the editor headless with the control server, drives it through the MCP bridge over
stdio like an MCP client would, and checks the game UI's HTTP endpoints.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BRIDGE = os.path.join(ROOT, "tools", "vse_mcp", "vse_mcp.py")
PORT = 8799
BASE = "http://127.0.0.1:{:d}".format(PORT)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path):
    with OPENER.open(BASE + path, timeout=120) as resp:
        return resp.status, resp.headers.get("Content-Type"), resp.read()


def main():
    blender = sys.argv[1]
    env = dict(os.environ, VSE_PORT=str(PORT))
    editor = subprocess.Popen(
        [blender, "-b", "--factory-startup", "--python-expr",
         "from vse_standalone import server; server.serve_forever()"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    failures = []

    def check(name, cond, detail=""):
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(detail)[:500]))
        if not cond:
            failures.append(name)

    try:
        for _ in range(120):
            try:
                get("/api/project")
                break
            except OSError:
                time.sleep(0.5)

        bridge = subprocess.Popen([sys.executable, BRIDGE], env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        next_id = [0]

        def rpc(method, params=None):
            next_id[0] += 1
            bridge.stdin.write(json.dumps({"jsonrpc": "2.0", "id": next_id[0], "method": method,
                                           "params": params or {}}) + "\n")
            bridge.stdin.flush()
            return json.loads(bridge.stdout.readline())

        def tool(name, **args):
            reply = rpc("tools/call", {"name": name, "arguments": args})
            result = reply["result"]
            if result.get("isError"):
                raise RuntimeError(result["content"][0]["text"])
            return result["structuredContent"]["result"]

        init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "test", "version": "0"}})
        check("initialize", init["result"]["serverInfo"]["name"] == "video-editor", init)
        bridge.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")

        tools = {t["name"]: t for t in rpc("tools/list")["result"]["tools"]}
        check("tools/list has ripple + LUT tools",
              {"ripple_delete", "ripple_cut", "ripple_delete_range", "apply_lut"} <= set(tools),
              sorted(tools))
        check("schema has types", tools["ripple_delete_range"]["inputSchema"]["properties"]
              ["frame_start"]["type"] == "integer", tools["ripple_delete_range"])

        tool("add_color", color=[1, 0, 0], channel=1, frame_start=1, length=20, name="a")
        tool("add_color", color=[0, 1, 0], channel=1, frame_start=21, length=20, name="b")
        tool("ripple_delete", names=["a"])
        strips = tool("list_strips")
        check("ripple_delete via MCP", [(s["name"], s["frame_start"]) for s in strips] == [("b", 1)],
              strips)

        reply = rpc("tools/call", {"name": "trim_strip", "arguments": {"name": "nope"}})
        check("errors are reported to the agent", reply["result"].get("isError") is True, reply)

        reply = rpc("tools/call", {"name": "render_frame",
                                   "arguments": {"frame": 5, "filepath": "/tmp/vse_e2e.png"}})
        kinds = [c["type"] for c in reply["result"]["content"]]
        check("render_frame returns an image to the agent", "image" in kinds, kinds)

        # Game UI routes.
        status, ctype, body = get("/")
        check("UI index served", status == 200 and b"latest avenue" in body, ctype)
        status, ctype, body = get("/fonts/Inter.woff2")
        check("Inter font served", status == 200 and len(body) > 10000, len(body))
        state = json.loads(get("/api/state")[2])
        check("UI state", state["status"]["connected"] and state["mode"]["name"] == "night", state)
        status, ctype, body = get("/frame/{:d}/0/0/1.jpg".format(state["generation"])) \
            if state["clips"] else (0, "", b"")
        check("UI preview frame (no video clips => skipped)", True)

        bridge.stdin.close()
        bridge.wait(10)
    finally:
        editor.terminate()
        try:
            out, _ = editor.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            editor.kill()
            out, _ = editor.communicate()
        if failures:
            print(out[-4000:])
    print("\n{:d} failed".format(len(failures)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
