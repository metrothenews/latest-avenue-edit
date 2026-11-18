# The editing assistant

A language model that runs on the server, next to the editor, and works the editor for you
from a chat panel on `latestavenue.com/edit`. The model, the editor and the recording of what
they do are all inside the one server program on your machine; the browser only shows the
picture and the chat.

```
latestavenue.com/edit ── chat panel ──┐
                      ── live view  ──┤  (owner sign-in on every request)
                                      ▼
server container            gateway.py
                              ├─ agent.py ──▶ llama-server (127.0.0.1)   base model + LoRA adapters
                              │      └──MCP (stdio)──▶ vse_mcp.py ──▶ the editor's tools
                              └─ dataset: every message, tool call, result, manual edit, rating
```

## The model

- **Base model:** an open model in GGUF form. Default: Qwen2.5 7B Instruct, 4-bit
  (`Qwen2.5-7B-Instruct-Q4_K_M.gguf`, about 4.7 GB), downloaded from Hugging Face into
  `<data folder>/models` the first time someone chats. Change it with `LLM_MODEL_URL`
  (`-ModelUrl` in `cloud\pc\install.ps1`).
- **LoRA adapters:** every `*.gguf` in `<data folder>/models/adapters` is loaded on top of
  the base model when it starts. This is how the model gets specialized on your own editing
  (see [DATASET.md](DATASET.md)). With no adapters it is the plain base model. The chat
  panel shows which model and how many adapters are in use.
- **When it runs:** it starts on the first chat message and stops after 10 idle minutes
  (`LLM_IDLE_MINUTES`), so it holds no memory while nobody is chatting.
- **Speed:** it runs on the processor in this version. Expect the first reply of a chat to
  take the longest (it reads the whole tool list once, then remembers it).

## What it can do

Everything the editor's MCP server offers: the tools in
`scripts/modules/vse_standalone/api.py` and the modules beside it (strips, trims, ripple
edits, LUTs, text, markers, rendering, the lyric video builder, listing media). New editor
tools appear to the model automatically; nothing here needs changing. `run_operator` (the
raw escape hatch) is withheld from the model (`AGENT_TOOLS_DENY`).

Every tool call is one undo step in the editor, and you watch it happen in the live view.

## When it can't

If you ask for something the editor has no tool for, the model reports it
(`report_missing_capability`) and says so. The request is written to
`<data folder>/dataset/gaps.jsonl`: that file is the list of tools to build next. A refusal
in words without the report is recorded too, marked `inferred`.

## The chat panel

On the `/edit` page: **Chat** in the header. Beside the editor on a wide screen, over it on
a phone. Tool calls show as rows you can open to see what was sent and returned. Under each
answer, **Good** / **Not right** (with a note saying what you wanted) go into the dataset as
the training signal. **Stop** halts after the current step; **New chat** starts a fresh
conversation.

## Settings

Environment variables read by `cloud/agent.py` (see its docstring): `LLM_MODEL_URL`,
`LLM_MODEL_FILE`, `LLM_CONTEXT`, `LLM_THREADS`, `LLM_GPU_LAYERS`, `LLM_IDLE_MINUTES`,
`LLM_BASE_URL`, `AGENT_MAX_STEPS`, `AGENT_TOOLS_DENY`.

## What has and hasn't been run

Run on Linux (4 processor cores, no graphics card) with a small test model
(Qwen2.5 1.5B Instruct, 4-bit), both directly and in the container image:

- chat request → model → tool calls over MCP → the editor changed → answer (`add_color`,
  `list_strips`); first reply about 80–100 s on 4 cores, a follow-up 4 s;
- the chat panel in a real browser, desktop and phone size, against that stack: message,
  tool row, answer, rating with a note;
- a request with no tool for it (answered as not possible; see the note on small models);
- the dataset files (events, conversation, tool list, manual edit, rating) and the backup
  to a git branch, including refusing to push a planted credential;
- the chat endpoints refuse requests without the owner's sign-in (security test).

**Not run:** the default 7B model (too slow to be worth testing on 4 cores; it is the
same code path with a different file), the model download inside the container, **LoRA
adapters** (the adapters folder is read and passed to llama.cpp with `--lora`, but no
adapter has been loaded: none exists yet), a graphics card, and anything on the Windows PC.

- **Stop** cancels a reply while the model is writing; replies are capped in length
  (`LLM_MAX_TOKENS`) after the test model once wrote without end.
- The image's own compile of llama.cpp was **not** run in Docker here: this build machine's
  network proxy breaks certificate checks inside image builds. The same commands were run
  directly on the machine, and the image was tested with that binary copied in.

Known limits: the 1.5B test model also answered a question about the project without
looking ("10 strips" when there was one); the instructions now forbid that, but only a
larger model, ratings and adapters will make it dependable. One chat at a time; long chats will eventually fill the model's context
(start a new chat); the 1.5B test model made small mistakes (a color as 0–255 instead of
0–1, not calling the report tool), which is what the larger default and, later, adapters
are for.
