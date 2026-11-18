# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The editing agent: a local language model that works the editor through its MCP server.

Everything runs inside the server program, on the same machine as the editor:

    chat (web page) ──gateway──▶ Agent ──▶ language model  (llama.cpp server, 127.0.0.1)
                                   │         base model + LoRA adapters from disk
                                   └──▶ MCP client ──stdio──▶ vse_mcp.py ──▶ the editor

* The model is an open base model in GGUF form plus any LoRA adapters found in
  ``$VSE_DATA/models/adapters`` (``*.gguf``). Adapters are how the model is specialized on
  the dataset this module records. With none present the base model is used as is.
* The model process starts on the first chat message and stops after being idle, so it uses
  no memory while nobody is chatting. The first start downloads the base model if missing.
* Tools come from the editor's MCP server (``tools/list``), so every editor tool is available
  to the model without changes here. Calls go over real MCP (``tools/call``).
* Every step is an event: stored in memory for the web page to read, and written to the
  dataset (see :class:`Dataset`).

Environment:

``VSE_DATA``              data folder. Models in ``models/``, dataset in ``dataset/``.
``LLM_MODEL_URL``         where to download the base model (GGUF) if it is not on disk.
``LLM_MODEL_FILE``        file name of the base model inside ``$VSE_DATA/models``.
``LLM_CONTEXT``           context size in tokens (default 16384).
``LLM_THREADS``           processor threads for the model (default: all the container has).
``LLM_GPU_LAYERS``        layers to put on a graphics card (default 0; needs a GPU build).
``LLM_IDLE_MINUTES``      stop the model after this long without a chat (default 10).
``LLM_BASE_URL``          use this OpenAI-compatible server instead of starting llama.cpp
                          (for tests, or a model served elsewhere on the same machine).
``LLAMA_SERVER``          path of the llama.cpp server program (default ``llama-server``).
``AGENT_MAX_STEPS``       most model/tool rounds per message (default 16).
``LLM_MAX_TOKENS``        longest single reply from the model, in tokens (default 1024).
``AGENT_TOOLS_DENY``      comma-separated tool names the model is not given.
"""

import asyncio
import glob
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid

import aiohttp

log = logging.getLogger("agent")

VSE_DATA = os.environ.get("VSE_DATA", "/data")
MODELS_DIR = os.path.join(VSE_DATA, "models")
ADAPTERS_DIR = os.path.join(MODELS_DIR, "adapters")
MODEL_URL = os.environ.get(
    "LLM_MODEL_URL",
    "https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/"
    "Qwen2.5-7B-Instruct-Q4_K_M.gguf")
MODEL_FILE = os.environ.get("LLM_MODEL_FILE") or MODEL_URL.rsplit("/", 1)[-1]
CONTEXT = int(os.environ.get("LLM_CONTEXT", "16384"))
THREADS = int(os.environ.get("LLM_THREADS", "0"))
GPU_LAYERS = int(os.environ.get("LLM_GPU_LAYERS", "0"))
IDLE_MINUTES = float(os.environ.get("LLM_IDLE_MINUTES", "10"))
BASE_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
LLAMA_SERVER = os.environ.get("LLAMA_SERVER", "llama-server")
LLAMA_PORT = int(os.environ.get("LLAMA_PORT", "8793"))
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "16"))
# Longest single reply. A model that loses the thread can otherwise write forever.
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "1024"))
TOOLS_DENY = {t.strip() for t in os.environ.get("AGENT_TOOLS_DENY", "run_operator").split(",")
              if t.strip()}
MCP_SERVER = os.environ.get(
    "VSE_MCP_SERVER", os.path.join(os.path.dirname(os.path.abspath(__file__)), "vse_mcp.py"))
MAX_RESULT_CHARS = 6000
SCHEMA_VERSION = 1

SYSTEM_PROMPT = """\
You are the editing assistant built into "latest avenue video", a video editor. You work the \
editor for the user by calling its tools. The user watches the editor live next to this chat \
and can undo anything you do.

How to work:
- Before changing anything, look: call get_project and list_strips. Use list_media to find files.
- Strips are addressed by name. Times are frame numbers; get_project gives the frames per second.
- Never state a fact about the project (how many strips, their names, times, settings) \
from memory or by guessing: call a tool and read the answer.
- Do what was asked, one tool call at a time, and check results that matter (for example \
list_strips after moving or deleting).
- Media lives under /data/media. Renders go to /data/renders.
- If the editor has no tool for something the user asked for, call report_missing_capability \
with a precise description of what is missing, then tell the user plainly. Never pretend a \
step was done.
- When finished, say in one or two sentences what you did. No preamble.
"""

GAP_TOOL = {
    "type": "function",
    "function": {
        "name": "report_missing_capability",
        "description": (
            "Record that the editor cannot do something the user asked for, so the tool can "
            "be built. Call this instead of approximating with the wrong tool."),
        "parameters": {
            "type": "object",
            "properties": {
                "capability": {"type": "string",
                               "description": "What the editor would need to be able to do."},
                "wanted_for": {"type": "string",
                               "description": "The user's request it was needed for."},
            },
            "required": ["capability"],
        },
    },
}


# -----------------------------------------------------------------------------
# Dataset.

class Dataset:
    """
    The all-time record, laid out for machine learning and safe to back up as text:

    ``events/YYYY-MM.agent.jsonl``   every event, one JSON object per line, append only.
    ``conversations/<id>.json``      each chat in OpenAI chat format (messages with tool calls
                                     and results), with the model, adapters and tool-list
                                     hash it ran with, and any feedback. Ready to convert to
                                     fine-tuning examples.
    ``tools/<hash>.json``            the tool list the model was given, stored once per version.
    ``gaps.jsonl``                   things the editor could not do (the build queue).

    The editor writes what a person does by hand to ``events/YYYY-MM.editor.jsonl``
    (``vse_standalone/activity.py``). No media, no credentials and no account details are
    ever written: only text about the edit.
    """

    def __init__(self, root=None):
        self.root = root or os.environ.get("VSE_DATASET_DIR") or os.path.join(VSE_DATA, "dataset")

    def _append(self, relative, record):
        try:
            path = os.path.join(self.root, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as ex:
            log.warning("dataset write failed: %s", ex)

    def event(self, record):
        record = {"v": SCHEMA_VERSION, "source": "agent", **record}
        self._append("events/{:s}.agent.jsonl".format(time.strftime("%Y-%m", time.gmtime())),
                     record)

    def gap(self, record):
        self._append("gaps.jsonl", {"v": SCHEMA_VERSION, "ts": round(time.time(), 3), **record})

    def tools(self, tools):
        text = json.dumps(tools, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        path = os.path.join(self.root, "tools", digest + ".json")
        try:
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
        except OSError as ex:
            log.warning("dataset write failed: %s", ex)
        return digest

    def conversation(self, session):
        try:
            path = os.path.join(self.root, "conversations", session["id"] + ".json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            temp = path + ".tmp"
            with open(temp, "w", encoding="utf-8") as fh:
                json.dump({
                    "v": SCHEMA_VERSION,
                    "id": session["id"],
                    "started": session["started"],
                    "updated": round(time.time(), 3),
                    "model": session.get("model"),
                    "adapters": session.get("adapters", []),
                    "tools_hash": session.get("tools_hash"),
                    "system": SYSTEM_PROMPT,
                    "messages": session["messages"],
                    "feedback": session["feedback"],
                    "turns": session["turns"],
                }, fh, ensure_ascii=False, indent=1, default=str)
            os.replace(temp, path)
        except OSError as ex:
            log.warning("dataset write failed: %s", ex)


# -----------------------------------------------------------------------------
# MCP client (stdio), talking to the editor's MCP server.

class MCPClient:
    def __init__(self):
        self.proc = None
        self._id = 0
        self._lock = asyncio.Lock()

    async def _start(self):
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, MCP_SERVER, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, limit=64 << 20)
        await self._request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "latest-avenue-agent", "version": "1"}})
        self.proc.stdin.write(
            (json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode())
        await self.proc.stdin.drain()

    async def _request(self, method, params=None):
        self._id += 1
        message = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()
        line = await self.proc.stdout.readline()
        if not line:
            raise RuntimeError("the editor's MCP server stopped")
        reply = json.loads(line)
        if "error" in reply:
            raise RuntimeError(reply["error"].get("message", "MCP error"))
        return reply["result"]

    async def request(self, method, params=None):
        async with self._lock:
            if self.proc is None or self.proc.returncode is not None:
                await self._start()
            try:
                return await self._request(method, params)
            except (RuntimeError, BrokenPipeError, ConnectionError, ValueError):
                # One restart: the bridge is stateless, the editor keeps the project.
                await self.close()
                await self._start()
                return await self._request(method, params)

    async def list_tools(self):
        return (await self.request("tools/list"))["tools"]

    async def call_tool(self, name, arguments):
        return await self.request("tools/call", {"name": name, "arguments": arguments or {}})

    async def close(self):
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
        self.proc = None


# -----------------------------------------------------------------------------
# Language model process.

class LLM:
    def __init__(self, http, emit):
        self.http = http
        self.emit = emit          # callable(type, **fields): progress events for the chat
        self.proc = None
        self.last_used = time.time()
        self.adapters = []
        self._lock = asyncio.Lock()

    @property
    def url(self):
        return BASE_URL or "http://127.0.0.1:{:d}".format(LLAMA_PORT)

    @property
    def name(self):
        return "external" if BASE_URL else MODEL_FILE

    def status(self):
        path = os.path.join(MODELS_DIR, MODEL_FILE)
        return {
            "model": self.name,
            "adapters": [os.path.basename(a) for a in self._find_adapters()],
            "downloaded": bool(BASE_URL) or os.path.exists(path),
            "running": bool(BASE_URL) or (self.proc is not None and self.proc.returncode is None),
        }

    @staticmethod
    def _find_adapters():
        return sorted(glob.glob(os.path.join(ADAPTERS_DIR, "*.gguf")))

    async def _download(self, path):
        os.makedirs(MODELS_DIR, exist_ok=True)
        temp = path + ".part"
        have = os.path.getsize(temp) if os.path.exists(temp) else 0
        headers = {"Range": "bytes={:d}-".format(have)} if have else {}
        self.emit("model_status", state="downloading", done=have, total=0, file=MODEL_FILE)
        timeout = aiohttp.ClientTimeout(total=None, sock_read=120)
        # Its own session: downloads honor the machine's proxy settings, local calls never do.
        async with aiohttp.ClientSession(trust_env=True) as outside, \
                outside.get(MODEL_URL, headers=headers, timeout=timeout) as resp:
            if resp.status == 416:
                os.replace(temp, path)
                return
            if resp.status not in (200, 206):
                raise RuntimeError("model download failed (HTTP {:d})".format(resp.status))
            if resp.status == 200:
                have = 0
            total = have + int(resp.headers.get("Content-Length") or 0)
            last = 0.0
            with open(temp, "ab" if have else "wb") as fh:
                async for chunk in resp.content.iter_chunked(1 << 20):
                    fh.write(chunk)
                    have += len(chunk)
                    if time.time() - last > 2:
                        last = time.time()
                        self.emit("model_status", state="downloading", done=have, total=total,
                                  file=MODEL_FILE, transient=True)
        os.replace(temp, path)

    async def ensure(self):
        """Make sure a model is answering; download and start it if needed."""
        self.last_used = time.time()
        if BASE_URL:
            return
        async with self._lock:
            if self.proc is not None and self.proc.returncode is None:
                return
            path = os.path.join(MODELS_DIR, MODEL_FILE)
            if not os.path.exists(path):
                await self._download(path)
            self.adapters = self._find_adapters()
            args = [LLAMA_SERVER, "-m", path, "--host", "127.0.0.1", "--port", str(LLAMA_PORT),
                    "-c", str(CONTEXT), "--jinja", "-np", "1", "--no-webui"]
            if THREADS > 0:
                args += ["-t", str(THREADS)]
            if GPU_LAYERS:
                args += ["-ngl", str(GPU_LAYERS)]
            for adapter in self.adapters:
                args += ["--lora", adapter]
            self.emit("model_status", state="loading", file=MODEL_FILE,
                      adapters=[os.path.basename(a) for a in self.adapters])
            log.info("starting the model: %s", " ".join(args))
            logfile = open("/tmp/llama.log", "ab")
            self.proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.DEVNULL, stdout=logfile, stderr=logfile)
            started = time.time()
            while True:
                if self.proc.returncode is not None:
                    raise RuntimeError("the model program stopped while loading (see /tmp/llama.log)")
                try:
                    async with self.http.get(self.url + "/health",
                                             timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        if resp.status == 200:
                            break
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
                if time.time() - started > 600:
                    raise RuntimeError("the model did not finish loading in 10 minutes")
                await asyncio.sleep(1)
            self.emit("model_status", state="ready", file=MODEL_FILE,
                      seconds=round(time.time() - started, 1))

    async def chat(self, messages, tools):
        await self.ensure()
        body = {"model": self.name, "messages": messages, "tools": tools,
                "tool_choice": "auto", "temperature": 0.2, "stream": False,
                "max_tokens": MAX_TOKENS}
        async with self.http.post(self.url + "/v1/chat/completions", json=body,
                                  timeout=aiohttp.ClientTimeout(total=1800)) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError("the model returned an error ({:d}): {:s}".format(
                    resp.status, text[:300]))
        self.last_used = time.time()
        data = json.loads(text)
        choice = data["choices"][0]
        return choice["message"], data.get("usage") or {}, choice.get("finish_reason")

    async def stop_if_idle(self):
        if BASE_URL or self.proc is None or self.proc.returncode is not None:
            return
        if time.time() - self.last_used > IDLE_MINUTES * 60:
            await self.stop()

    async def stop(self):
        if self.proc is not None and self.proc.returncode is None:
            log.info("stopping the model")
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 15)
            except asyncio.TimeoutError:
                self.proc.kill()
        self.proc = None


# -----------------------------------------------------------------------------
# The agent.

_REFUSAL = re.compile(
    r"\b(can(?:'|’)?t|cannot|unable to|not able to|don(?:'|’)?t have (?:the |a )?"
    r"(?:capability|ability|tool)|no tool|not (?:currently )?(?:possible|supported|available)|"
    r"isn(?:'|’)?t (?:possible|supported|available))\b", re.IGNORECASE)


def _result_text(result):
    """Text of an MCP tool result, for the model. Images are left out."""
    parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    text = "\n".join(p for p in parts if p)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n… (cut: {:d} characters in all)".format(len(text))
    return text or "(no output)"


class Agent:
    def __init__(self, http):
        self.http = http
        self.dataset = Dataset()
        self.mcp = MCPClient()
        self.llm = LLM(http, self._emit_model)
        self.events = []            # Events of the current chat, for the web page.
        self.seq = 0
        self.session = None
        self.task = None
        self.stop_requested = False
        self.in_tool = False
        self.waiters = set()
        self.new_session()

    # -- events ---------------------------------------------------------------

    def _emit(self, type_, transient=False, **fields):
        self.seq += 1
        event = {"seq": self.seq, "ts": round(time.time(), 3), "session": self.session["id"],
                 "type": type_, **fields}
        self.events.append(event)
        del self.events[:-2000]
        if not transient:
            self.dataset.event(event)
        for waiter in list(self.waiters):
            waiter.set()
        return event

    def _emit_model(self, type_, **fields):
        self._emit(type_, **fields)

    async def wait_events(self, after, timeout):
        """Events with ``seq`` greater than ``after``; waits up to ``timeout`` for one."""
        def newer():
            return [e for e in self.events if e["seq"] > after]
        found = newer()
        if not found and timeout > 0:
            waiter = asyncio.Event()
            self.waiters.add(waiter)
            try:
                await asyncio.wait_for(waiter.wait(), timeout)
            except asyncio.TimeoutError:
                pass
            finally:
                self.waiters.discard(waiter)
            found = newer()
        return found

    # -- sessions -------------------------------------------------------------

    def new_session(self):
        if self.session is not None and self.session["messages"]:
            self.dataset.conversation(self.session)
        self.session = {"id": time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6],
                        "started": round(time.time(), 3), "messages": [], "feedback": [],
                        "turns": 0}
        self.events = []
        self._emit("session_start")
        return self.session["id"]

    def state(self):
        return {"session": self.session["id"], "busy": self.busy, "last": self.seq,
                "llm": self.llm.status(), "turns": self.session["turns"]}

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def feedback(self, seq, rating, note=""):
        if rating not in ("up", "down"):
            raise ValueError("rating must be up or down")
        entry = {"seq": int(seq), "rating": rating, "note": str(note)[:2000]}
        self.session["feedback"].append(entry)
        self._emit("feedback", **{"about": entry["seq"], "rating": rating, "note": entry["note"]})
        self.dataset.conversation(self.session)

    def stop(self):
        """Stop the current turn: at once if the model is writing, else after the tool that
        is running (a tool call is never abandoned halfway)."""
        self.stop_requested = True
        if self.busy and not self.in_tool:
            self.task.cancel()

    # -- a turn ---------------------------------------------------------------

    def send(self, text):
        text = str(text).strip()
        if not text:
            raise ValueError("empty message")
        if self.busy:
            raise RuntimeError("still working on the last message")
        self.stop_requested = False
        self.session["messages"].append({"role": "user", "content": text})
        self._emit("user_message", text=text)
        self.task = asyncio.ensure_future(self._turn())

    async def _tools(self):
        # The editor may still be starting: its MCP server lists the real tools once it is up.
        for _ in range(60):
            mcp_tools = await self.mcp.list_tools()
            if any(t["name"] == "get_project" for t in mcp_tools):
                break
            await asyncio.sleep(2)
        else:
            raise RuntimeError("the editor is not running")
        tools = [{"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}}
            for t in mcp_tools if t["name"] not in TOOLS_DENY]
        tools.append(GAP_TOOL)
        return tools

    async def _turn(self):
        session = self.session
        started = time.time()
        try:
            tools = await self._tools()
            session["tools_hash"] = self.dataset.tools(tools)
            await self.llm.ensure()
            session["model"] = self.llm.name
            session["adapters"] = [os.path.basename(a) for a in self.llm.adapters]
            request = session["messages"][-1]["content"]
            used_tools = reported_gap = False
            for step in range(MAX_STEPS):
                if self.stop_requested:
                    self._emit("stopped")
                    break
                messages = [{"role": "system", "content": SYSTEM_PROMPT}] + session["messages"]
                self._emit("thinking", transient=True, step=step + 1)
                message, usage, finish = await self.llm.chat(messages, tools)
                calls = message.get("tool_calls") or []
                if finish == "length":
                    # Cut off at the length limit: whatever it was writing is not usable.
                    session["messages"].append({"role": "assistant",
                                                "content": message.get("content") or ""})
                    self._emit("error", text="The model's reply ran past the length limit and "
                                             "was cut off. Try rephrasing, or start a new chat.")
                    break
                content = message.get("content") or ""
                record = {"role": "assistant", "content": content}
                if calls:
                    record["tool_calls"] = calls
                session["messages"].append(record)
                if content.strip():
                    self._emit("assistant_message", text=content, final=not calls, usage=usage,
                               model=session["model"], adapters=session["adapters"])
                if not calls:
                    # Smaller models often say they can't do something without calling
                    # report_missing_capability. Catch that here so the build queue still
                    # gets the request; "inferred" marks it as a guess to be reviewed.
                    if not used_tools and _REFUSAL.search(content) and not reported_gap:
                        gap = {"capability": content.strip()[:500], "wanted_for": request,
                               "inferred": True}
                        self.dataset.gap({"session": session["id"], **gap})
                        self._emit("capability_gap", **gap)
                    break
                used_tools = True
                reported_gap = reported_gap or any(
                    (c.get("function") or {}).get("name") == "report_missing_capability"
                    for c in calls)
                for call in calls:
                    await self._run_tool(session, call)
                    if self.stop_requested:
                        break
            else:
                self._emit("error", text="Stopped after {:d} steps without finishing.".format(MAX_STEPS))
        except asyncio.CancelledError:
            # Stop was pressed while the model was writing: drop the half-made reply.
            self._emit("stopped")
        except Exception as ex:
            log.exception("turn failed")
            self._emit("error", text=str(ex)[:500])
        finally:
            session["turns"] += 1
            self._emit("turn_end", seconds=round(time.time() - started, 1))
            self.dataset.conversation(session)

    async def _run_tool(self, session, call):
        function = call.get("function") or {}
        name = function.get("name", "")
        call_id = call.get("id") or uuid.uuid4().hex[:8]
        raw = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else dict(raw)
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
        except ValueError as ex:
            arguments, bad = {}, "The arguments were not valid JSON: {:s}".format(str(ex))
        else:
            bad = None
        self._emit("tool_call", id=call_id, name=name, arguments=arguments)
        started = time.time()
        if bad:
            text, ok = bad, False
        elif name == "report_missing_capability":
            self.dataset.gap({"session": session["id"], **arguments})
            self._emit("capability_gap", **{k: str(v)[:1000] for k, v in arguments.items()})
            text, ok = "Recorded. Tell the user this is not possible yet.", True
        elif name in TOOLS_DENY:
            text, ok = "That tool is not available.", False
        else:
            self.in_tool = True
            try:
                result = await self.mcp.call_tool(name, arguments)
                text, ok = _result_text(result), not result.get("isError")
            except Exception as ex:
                text, ok = "The tool could not be run: {:s}".format(str(ex)[:300]), False
            finally:
                self.in_tool = False
        session["messages"].append({"role": "tool", "tool_call_id": call_id, "content": text})
        self._emit("tool_result", id=call_id, name=name, ok=ok, text=text,
                   seconds=round(time.time() - started, 2))

    # -- housekeeping ---------------------------------------------------------

    async def housekeeping(self):
        if not self.busy:
            await self.llm.stop_if_idle()

    async def close(self):
        if self.session["messages"]:
            self.dataset.conversation(self.session)
        await self.llm.stop()
        await self.mcp.close()
