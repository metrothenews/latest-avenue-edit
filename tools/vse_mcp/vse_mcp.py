#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
MCP server for the video editor (stdio transport, Python standard library only).

It forwards MCP tool calls to the editor's local control server, so Claude (or any MCP
client) can drive the running editor while a person watches and steps in when needed.
The tool list comes from the editor itself (``GET /api/tools``), so new editor tools appear
here without changes to this file.

Claude Code:

    claude mcp add video-editor -- python3 /path/to/tools/vse_mcp/vse_mcp.py

Claude Desktop (``claude_desktop_config.json``):

    {"mcpServers": {"video-editor": {"command": "python3",
                                     "args": ["/path/to/tools/vse_mcp/vse_mcp.py"]}}}

Environment: ``VSE_PORT`` (default 8792) if the editor uses another port.
"""

import json
import os
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "video-editor", "version": "0.1.0"}
PORT = int(os.environ.get("VSE_PORT", "8792"))
BASE = "http://127.0.0.1:{:d}".format(PORT)
# Talk to the local editor directly, never through a proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_EDITOR_DOWN = (
    "The video editor is not running or its control server is off. Start the editor "
    "(the server starts with it, on port {:d}), then try again.".format(PORT))


def _http(method, path, body=None, timeout=900):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-LA", "1")
    with _OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"null")


def _editor_tools():
    tools = []
    for t in _http("GET", "/api/tools", timeout=10):
        annotations = {"readOnlyHint": not t.get("mutates", True)}
        tools.append({
            "name": t["name"],
            "description": t["description"],
            "inputSchema": _clean_schema(t["input_schema"]),
            "annotations": annotations,
        })
    return tools


def _clean_schema(schema):
    """JSON Schema has no ``nullable``, express it as a type union."""
    if isinstance(schema, dict):
        schema = {k: _clean_schema(v) for k, v in schema.items()}
        if schema.pop("nullable", False) and "type" in schema:
            schema["type"] = [schema["type"], "null"]
        return schema
    if isinstance(schema, list):
        return [_clean_schema(v) for v in schema]
    return schema


def _call_tool(name, arguments):
    try:
        reply = _http("POST", "/rpc", {"jsonrpc": "2.0", "id": 1, "method": name,
                                       "params": arguments or {}})
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return {"content": [{"type": "text", "text": _EDITOR_DOWN}], "isError": True}
    if "error" in reply:
        return {"content": [{"type": "text", "text": reply["error"]["message"]}],
                "isError": True}
    result = reply.get("result")
    content = [{"type": "text", "text": json.dumps(result, indent=1)}]
    # Rendered frames are returned as images too, so the model can look at them.
    if isinstance(result, dict) and str(result.get("filepath", "")).lower().endswith(".png") \
            and result.get("exists"):
        try:
            import base64
            with open(result["filepath"], "rb") as fh:
                content.append({"type": "image", "mimeType": "image/png",
                                "data": base64.b64encode(fh.read()).decode()})
        except OSError:
            pass
    return {"content": content, "structuredContent": {"result": result}}


def _handle(msg):
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "Controls a running video editor. Call get_project and list_strips "
                    "first. Strips are addressed by name. Every editing tool is one undo "
                    "step, so a person can undo agent changes. Use render_frame to look at "
                    "the result.")}
    if method == "ping":
        return {}
    if method == "tools/list":
        try:
            return {"tools": _editor_tools()}
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            # Still answer, so the client shows why nothing works.
            return {"tools": [{"name": "ping", "description": _EDITOR_DOWN,
                               "inputSchema": {"type": "object", "properties": {}}}]}
    if method == "tools/call":
        return _call_tool(params.get("name"), params.get("arguments"))
    raise LookupError(method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:
            continue  # Notification (e.g. notifications/initialized).
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": _handle(msg)}
        except LookupError:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32601, "message": "Method not found"}}
        except Exception as ex:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32603, "message": str(ex)}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
