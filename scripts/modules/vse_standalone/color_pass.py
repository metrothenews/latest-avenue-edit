# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Engine for the game UI's Color and Crop tabs (ported from latest avenue resolve).

Color: step through the video clips of the timeline; preview the mode's LUTs with single key
presses; apply one per pass. There are two passes, ``look`` and ``color``. On a strip they are
two LUT modifiers, "Color LUT" first and "Look LUT" on top, the same order as the Resolve
node setup (the look acts on the color-balanced image).

Crop: set the zoom of every video clip.

Previews are rendered by the editor itself, so they are exactly what the export will show.
Everything here is exposed as automation tools too, so an agent can do the same pass.
"""

import json
import os
import time

import bpy

from . import api
from .api import APIError, tool

PASSES = ("look", "color")
MOD_NAME = {"look": "Look LUT", "color": "Color LUT"}
VIDEO_TYPES = {'MOVIE', 'IMAGE', 'SCENE', 'MOVIECLIP'}
PREVIEW_WIDTH = 1280
_HERE = os.path.dirname(os.path.abspath(__file__))

_state = {
    "mode": None,
    "generation": 0,
    "clips": [],          # Strip names in timeline order.
    "loaded_key": None,
    "boot": int(time.time()),
    "crop": {"active": False, "result": None, "error": None},
    "loading": {"active": False, "done": 0, "total": 0, "error": None},
    "errors": {},
}


# -----------------------------------------------------------------------------
# Modes.

def modes_dirs():
    dirs = []
    if os.environ.get("VSE_MODES_DIR"):
        dirs.append(os.environ["VSE_MODES_DIR"])
    dirs.append(os.path.join(bpy.utils.user_resource('CONFIG'), "vse_modes"))
    dirs.append(os.path.join(_HERE, "modes"))
    return dirs


def _stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def load_mode(name):
    for folder in modes_dirs():
        path = os.path.join(folder, name + ".json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        lut_dirs = [os.path.join(folder, "luts"), folder, os.path.join(_HERE, "luts")]

        def resolve(filename):
            for d in lut_dirs:
                candidate = os.path.join(d, filename)
                if os.path.isfile(candidate):
                    return candidate
            raise APIError("Mode {!r}: LUT {!r} not found".format(name, filename))

        mode = {"name": data.get("name", name),
                "look": [resolve(f) for f in data.get("luts", [])],
                "color": [resolve(f) for f in data.get("color_luts", [])]}
        for kind in PASSES:
            if mode[kind] and len(mode[kind]) != 8:
                raise APIError("Mode {!r} must list exactly 8 LUTs per pass".format(name))
        if not mode["look"]:
            raise APIError("Mode {!r} has no look LUTs".format(name))
        return mode
    raise APIError("Mode {!r} not found".format(name))


def _mode():
    if _state["mode"] is None:
        _state["mode"] = load_mode("night")
    return _state["mode"]


@tool(mutates=False)
def list_modes() -> list:
    """List LUT modes available to the color pass (each is 8 look LUTs + 8 color LUTs)."""
    names = set()
    for folder in modes_dirs():
        if os.path.isdir(folder):
            names.update(_stem(f) for f in os.listdir(folder) if f.endswith(".json"))
    return sorted(names)


# -----------------------------------------------------------------------------
# Clips.

def _video_strips():
    strips = [s for s in api._strips() if s.type in VIDEO_TYPES]
    strips.sort(key=lambda s: (api._left(s), s.channel))
    return strips


def _timeline_key():
    return tuple((s.name, api._left(s), s.channel) for s in _video_strips())


def _clip_strip(index):
    if not 0 <= index < len(_state["clips"]):
        raise APIError("Clip list is out of date. Load clips again.")
    strip = api._strips().get(_state["clips"][index])
    if strip is None:
        raise APIError("Clip list is out of date. Load clips again.")
    return strip


def _slot_of(strip, kind):
    mod = strip.modifiers.get(MOD_NAME[kind])
    if mod is None or mod.type != 'LUT':
        return 0
    path = os.path.normcase(os.path.abspath(bpy.path.abspath(mod.filepath)))
    for n, lut in enumerate(_mode()[kind], 1):
        if os.path.normcase(os.path.abspath(lut)) == path:
            return n
    return 0


def _clip_info(index, strip):
    ours = set(MOD_NAME.values())
    others = [m for m in strip.modifiers if m.name not in ours and not m.mute]
    other_luts = [_stem(m.filepath) for m in others if m.type == 'LUT' and m.filepath]
    return {
        "index": index,
        "name": strip.name,
        "still": True,
        "look": _slot_of(strip, "look"),
        "color": _slot_of(strip, "color"),
        "graded": any(m.type != 'LUT' for m in others),
        "other_lut": other_luts[0] if other_luts else "",
        "pending": False,
        "error": _state["errors"].get(strip.name),
    }


def _still_frame(strip):
    scene = api._scene()
    fps = scene.render.fps / scene.render.fps_base
    left, right = api._left(strip), api._right(strip)
    frame = left + int(round(fps))
    return frame if frame < right else (left + right) // 2


def _set_pass_luts(strip, luts):
    """Set the color and look LUT modifiers of a strip (paths or None), in stacking order."""
    for kind in PASSES:
        mod = strip.modifiers.get(MOD_NAME[kind])
        if mod is not None:
            strip.modifiers.remove(mod)
    for kind in ("color", "look"):
        if luts.get(kind):
            mod = strip.modifiers.new(name=MOD_NAME[kind], type='LUT')
            mod.filepath = luts[kind]


def _current_luts(strip):
    out = {}
    for kind in PASSES:
        mod = strip.modifiers.get(MOD_NAME[kind])
        out[kind] = mod.filepath if (mod is not None and mod.type == 'LUT') else None
    return out


# -----------------------------------------------------------------------------
# Tools.

@tool(mutates=False)
def color_pass_load(mode: str = "night") -> dict:
    """Start (or restart) the color pass: read the timeline's video clips in order."""
    _state["mode"] = load_mode(mode)
    _state["clips"] = [s.name for s in _video_strips()]
    _state["loaded_key"] = _timeline_key()
    _state["generation"] += 1
    _state["errors"] = {}
    n = len(_state["clips"])
    _state["loading"] = {"active": False, "done": n, "total": n, "error": None}
    return color_pass_state()


@tool(mutates=False)
def color_pass_state() -> dict:
    """Color pass state: mode, clips in order with the look/color LUT slot (1-8, 0 = none) applied."""
    if _state["generation"] == 0:
        color_pass_load()
    scene = api._scene()
    clips = []
    for index, name in enumerate(_state["clips"]):
        strip = api._strips().get(name)
        if strip is not None:
            clips.append(_clip_info(index, strip))
    mode = _mode()
    project = os.path.basename(bpy.data.filepath) or "Untitled"
    return {
        "status": {"connected": True, "project": project, "timeline": scene.name,
                   "product": "latest avenue", "version": bpy.app.version_string,
                   "message": "" if clips else "Add video clips to the timeline.",
                   "stale": _timeline_key() != _state["loaded_key"]},
        "loading": dict(_state["loading"]),
        "mode": {"name": mode["name"], "look": [_stem(p) for p in mode["look"]],
                 "color": [_stem(p) for p in mode["color"]]},
        "boot": _state["boot"],
        "crop": dict(_state["crop"]),
        "generation": _state["generation"],
        "clips": clips,
    }


@tool()
def color_pass_apply(index: int, pass_name: str, slot: int, generation: int = 0) -> dict:
    """Apply LUT `slot` (1-8, 0 removes) of pass "look" or "color" to clip `index`.

    :arg generation: Clip list generation from color_pass_state (0 skips the check).
    """
    if pass_name not in PASSES or not 0 <= slot <= 8:
        raise APIError("pass_name must be look or color and slot 0-8")
    if generation and generation != _state["generation"]:
        raise APIError("Clip list is out of date. Load clips again.")
    mode = _mode()
    if slot and not mode[pass_name]:
        raise APIError("Mode has no {:s} LUTs".format(pass_name))
    strip = _clip_strip(index)
    luts = _current_luts(strip)
    before = luts.get(pass_name)
    luts[pass_name] = mode[pass_name][slot - 1] if slot else None
    _set_pass_luts(strip, luts)
    _state["errors"].pop(strip.name, None)
    from . import activity
    activity.write_event({"type": "game_decision", "pass": "color_pass." + pass_name,
                          "item": strip.name, "choice": slot,
                          "before": before and _stem(before),
                          "after": luts[pass_name] and _stem(luts[pass_name])})
    _log({"clip": strip.name, "pass": pass_name, "slot": slot,
          "before": before and _stem(before), "after": luts[pass_name] and _stem(luts[pass_name])})
    return _clip_info(index, strip)


@tool(mutates=False)
def color_pass_preview(index: int, color: int = 0, look: int = 0) -> dict:
    """Render clip `index`'s frame at 1 s with the given color and look LUT slots (0 = none).

    Returns the path of a JPEG. Rendered by the editor, so it matches the export exactly.
    """
    from . import server
    mode = _mode()
    folder = os.path.join(server.output_dir(), "pass", str(_state["boot"]), str(_state["generation"]))
    out = os.path.join(folder, "{:04d}_c{:d}_l{:d}.jpg".format(index, color, look))
    if os.path.exists(out):
        return {"filepath": out, "exists": True}
    if (color and not mode["color"]) or not (0 <= color <= 8 and 0 <= look <= 8):
        raise APIError("Bad LUT slot")
    strip = _clip_strip(index)
    os.makedirs(folder, exist_ok=True)

    scene = api._scene()
    render = scene.render
    settings = render.image_settings
    saved_luts = _current_luts(strip)
    saved = (scene.frame_current, render.filepath, render.resolution_percentage,
             settings.media_type, settings.file_format, settings.quality)
    try:
        _set_pass_luts(strip, {"color": mode["color"][color - 1] if color else None,
                               "look": mode["look"][look - 1] if look else None})
        scene.frame_current = _still_frame(strip)
        render.resolution_percentage = max(1, min(100, int(100 * PREVIEW_WIDTH / render.resolution_x)))
        settings.media_type = 'IMAGE'
        settings.file_format = 'JPEG'
        settings.quality = 90
        render.filepath = out
        render.use_file_extension = False
        with bpy.context.temp_override(**api._override()):
            bpy.ops.render.render(write_still=True)
    except Exception as ex:
        _state["errors"][strip.name] = str(ex)
        raise
    finally:
        _set_pass_luts(strip, saved_luts)
        (scene.frame_current, render.filepath, render.resolution_percentage,
         settings.media_type, settings.file_format, settings.quality) = saved
    return {"filepath": out, "exists": os.path.exists(out)}


@tool()
def zoom_all_clips(zoom: float) -> dict:
    """Set the zoom (scale) of every video clip. Position, rotation and crop are left alone."""
    if not 0.1 <= zoom <= 10:
        raise APIError("Zoom must be between 0.1 and 10.")
    changed = unchanged = 0
    log = []
    for strip in _video_strips():
        t = strip.transform
        if abs(t.scale_x - zoom) < 1e-6 and abs(t.scale_y - zoom) < 1e-6:
            unchanged += 1
            continue
        log.append({"clip": strip.name, "before": [t.scale_x, t.scale_y], "after": zoom})
        t.scale_x = t.scale_y = zoom
        changed += 1
    result = {"total": changed + unchanged, "changed": changed, "unchanged": unchanged,
              "failed": 0, "zoom": zoom}
    _state["crop"] = {"active": False, "result": result, "error": None}
    _log({"crop": result, "clips": log}, "crop_log.jsonl")
    if changed:
        _state["generation"] += 1  # Previews show the old framing.
    return result


def _log(entry, name="color_log.jsonl"):
    folder = os.path.join(bpy.utils.user_resource('CONFIG'), "vse_history")
    try:
        os.makedirs(folder, exist_ok=True)
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "project": bpy.data.filepath, **entry}
        with open(os.path.join(folder, name), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass
