# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Activity log: what a person does by hand in the editor, written as dataset events.

Together with the agent's log (chat, model replies, tool calls; ``cloud/agent.py``) this is
the all-time record used later to train and evaluate the editing model: the same edit seen
as "what was asked", "what the model did" and "what the person did instead".

Only active when a dataset folder is configured (``VSE_DATASET_DIR``, or ``VSE_DATA`` on a
server, which uses ``$VSE_DATA/dataset``). Events go to ``events/YYYY-MM.editor.jsonl``, one
JSON object per line:

``{"v": 1, "ts": <unix time>, "source": "editor", "type": "manual_op", "op": "sequencer.split",
"props": {...}, "frame": 120, "strips": 14}``

Nothing is recorded but operator names, their settings, and counts: no pixels, no audio.
"""

import json
import os
import time

import bpy

SCHEMA_VERSION = 1
_state = {"seen": [], "dir": None, "last_strip_count": None}

# Operators that say nothing about editing (pointer movement, redraws, this log's own UI).
_IGNORE_PREFIXES = ("VIEW2D_OT_", "SCREEN_OT_", "WM_OT_", "UI_OT_", "VSE_OT_", "ED_OT_undo_push")


def dataset_dir():
    path = os.environ.get("VSE_DATASET_DIR")
    if not path and os.environ.get("VSE_DATA"):
        path = os.path.join(os.environ["VSE_DATA"], "dataset")
    return path or None


def write_event(event, source="editor"):
    """Append one event to the dataset. Safe to call when no dataset folder is configured."""
    folder = _state["dir"] or dataset_dir()
    if not folder:
        return
    try:
        events = os.path.join(folder, "events")
        os.makedirs(events, exist_ok=True)
        record = {"v": SCHEMA_VERSION, "ts": round(time.time(), 3), "source": source}
        record.update(event)
        name = "{:s}.{:s}.jsonl".format(time.strftime("%Y-%m", time.gmtime()), source)
        with open(os.path.join(events, name), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _plain(value, depth=0):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value if not isinstance(value, str) else value[:300]
    if depth < 2 and hasattr(value, "__len__") and not isinstance(value, dict):
        try:
            return [_plain(v, depth + 1) for v in list(value)[:16]]
        except TypeError:
            pass
    return str(value)[:120]


def _properties(op):
    try:
        return {k: _plain(v) for k, v in op.as_keywords().items()}
    except Exception:
        return {}


def _poll():
    wm = bpy.context.window_manager
    try:
        operators = list(wm.operators)
    except Exception:
        return 1.0
    pointers = [op.as_pointer() for op in operators]
    seen = _state["seen"]
    # New entries are appended at the end; find where the list we saw last time ends.
    start = 0
    if seen:
        last = seen[-1]
        if last in pointers:
            start = len(pointers) - 1 - pointers[::-1].index(last) + 1
    scene = getattr(bpy.context, "sequencer_scene", None) or bpy.context.scene
    for op in operators[start:]:
        idname = op.bl_idname
        if idname.startswith(_IGNORE_PREFIXES):
            continue
        module, _, name = idname.partition("_OT_")
        event = {"type": "manual_op", "op": "{:s}.{:s}".format(module.lower(), name),
                 "props": _properties(op)}
        if scene is not None:
            event["frame"] = scene.frame_current
            editor = scene.sequence_editor
            if editor is not None:
                event["strips"] = len(editor.strips_all)
        write_event(event)
    _state["seen"] = pointers
    return 1.0


def sync():
    """Mark every operator run so far as seen, without logging it. Called after a tool call
    from the control server, so the agent's own work is not recorded as done by hand."""
    try:
        _state["seen"] = [op.as_pointer() for op in bpy.context.window_manager.operators]
    except Exception:
        pass


def start():
    folder = dataset_dir()
    if not folder or bpy.app.background:
        return False
    _state["dir"] = folder
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=2.0, persistent=True)
    return True


def stop():
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
