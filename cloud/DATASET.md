# The dataset

Everything that happens in the editor on the server is recorded as text, so the editing
model can later be trained and measured on real work: what was asked, what the model did,
what a person did by hand, and whether the result was good.

It lives in `<data folder>/dataset` and is backed up to the `dataset` branch of the private
repository by `cloud/backup_dataset.sh` (Linux) or `cloud\pc\backup_dataset.ps1` (Windows).

**It contains no media, no models, no project files, no sign-in tokens and no account
details.** It does contain what was typed in the chat, file names and the text of edits
(titles, lyrics), so it only ever goes to a private repository; the backup refuses a public
one and stops if anything looks like a credential.

## Layout

| Path | What | Written by |
|---|---|---|
| `events/YYYY-MM.agent.jsonl` | Every chat event, one JSON object per line, append only | `cloud/agent.py` |
| `events/YYYY-MM.editor.jsonl` | What a person did by hand in the editor, and game-view decisions | `vse_standalone/activity.py` |
| `conversations/<id>.json` | One chat, in OpenAI chat format, ready to turn into training examples | `cloud/agent.py` |
| `tools/<hash>.json` | The tool list the model was given, stored once per version | `cloud/agent.py` |
| `gaps.jsonl` | Things the editor could not do: the list of tools to build next | `cloud/agent.py` |

Every record has `"v"` (schema version, now 1) and `"ts"` (Unix time, seconds).

## Events

Agent events also have `seq` (order within the server's run) and `session` (the chat's id).

| `type` | Fields | Meaning |
|---|---|---|
| `session_start` | | A new chat |
| `user_message` | `text` | What was asked |
| `model_status` | `state` (`loading`, `ready`), `file`, `adapters` | The model being started |
| `assistant_message` | `text`, `final`, `usage`, `model`, `adapters` | What the model said; `final` is its answer |
| `tool_call` | `id`, `name`, `arguments` | A tool the model called, through MCP |
| `tool_result` | `id`, `name`, `ok`, `text`, `seconds` | What the editor returned |
| `capability_gap` | `capability`, `wanted_for`, `inferred` | The editor can't do this yet. `inferred` means the model said so in words without reporting it, so it is a guess |
| `feedback` | `about` (the `seq` it rates), `rating` (`up`/`down`), `note` | A person's judgement of an answer |
| `error`, `stopped`, `turn_end` | `text` / `seconds` | How a turn ended |

Editor events (`"source": "editor"`):

| `type` | Fields | Meaning |
|---|---|---|
| `manual_op` | `op`, `props`, `frame`, `strips` | An editor command run by a person (not by the agent), with its settings |
| `game_decision` | `pass`, `item`, `choice`, `before`, `after` | A choice made in the game view |

## Conversations

```json
{"v": 1, "id": "20261118-015635-44c12c", "model": "…gguf", "adapters": [],
 "tools_hash": "f95558bc22a318a6", "system": "…the system prompt…",
 "messages": [{"role": "user", "content": "…"},
              {"role": "assistant", "content": "", "tool_calls": [{"id": "…", "type": "function",
                "function": {"name": "add_color", "arguments": "{…}"}}]},
              {"role": "tool", "tool_call_id": "…", "content": "…"},
              {"role": "assistant", "content": "…"}],
 "feedback": [{"seq": 9, "rating": "up", "note": ""}], "turns": 1}
```

With `tools/<tools_hash>.json` this is a complete training example: system prompt, tools,
the request, the calls, the results and the answer. Use `feedback` to choose which to learn
from (`up`), which to learn against or correct (`down`, with the note saying what was
wanted), and `events/*.editor.jsonl` around the same time to see what the person did
afterwards by hand, which is the correction itself.

## From dataset to a LoRA adapter (not built yet)

The model is an open base model plus LoRA adapters. Any `*.gguf` adapter placed in
`<data folder>/models/adapters` is loaded with the base model the next time it starts, and
each conversation records which adapters it ran with. Training an adapter from this dataset
(pick examples, fine-tune, convert to GGUF, compare against the base model on held-out
requests) is the next piece of work and has not been built or run.
