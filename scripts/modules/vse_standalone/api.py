# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Automation API for the video editor.

Design rules (keep these when adding functions):

* Every public function takes explicit, JSON-serializable arguments and returns
  JSON-serializable data. Strips are addressed by name, never by selection or mouse position.
* Functions work with or without a visible UI (``--background`` included).
* Every function that changes the project pushes one undo step named ``"Agent: <name>"``,
  so a person can undo any automated step from Edit > Undo History.
* Errors raise :class:`APIError` with a message meant for the caller (human or agent).
* Functions decorated with :func:`tool` are exported automatically to the JSON-RPC server
  (:mod:`vse_standalone.server`) and from there to MCP clients. The docstring's first line
  becomes the tool description; type hints become the JSON schema.
"""

__all__ = (
    "APIError",
    "TOOLS",
    "call",
    "describe",
    "tool",
)

import inspect
import os
import typing

import bpy

TOOLS = {}


class APIError(Exception):
    pass


# -----------------------------------------------------------------------------
# Registration & schema.

_JSON_TYPES = {
    int: {"type": "integer"},
    float: {"type": "number"},
    str: {"type": "string"},
    bool: {"type": "boolean"},
    list: {"type": "array"},
    dict: {"type": "object"},
}


def _schema_for(annotation):
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union and type(None) in args:
        inner = [a for a in args if a is not type(None)]
        schema = _schema_for(inner[0]) if len(inner) == 1 else {}
        return {**schema, "nullable": True}
    if origin in (list, typing.List):
        schema = {"type": "array"}
        if args:
            schema["items"] = _schema_for(args[0])
        return schema
    if origin in (dict, typing.Dict):
        return {"type": "object"}
    if origin is typing.Literal:
        return {"enum": list(args)}
    return dict(_JSON_TYPES.get(annotation, {}))


def tool(*, mutates=True):
    """Register a function as an automation tool."""
    def decorator(fn):
        sig = inspect.signature(fn)
        hints = typing.get_type_hints(fn)
        properties = {}
        required = []
        doc = inspect.getdoc(fn) or ""
        param_docs = {}
        for line in doc.splitlines():
            line = line.strip()
            if line.startswith(":arg ") and ":" in line[5:]:
                name, text = line[5:].split(":", 1)
                param_docs[name.strip()] = text.strip()
        for name, param in sig.parameters.items():
            schema = _schema_for(hints.get(name, str))
            if name in param_docs:
                schema["description"] = param_docs[name]
            if param.default is inspect.Parameter.empty:
                required.append(name)
            else:
                schema["default"] = param.default
            properties[name] = schema
        description = "\n".join(l for l in doc.splitlines() if not l.strip().startswith(":arg "))
        TOOLS[fn.__name__] = {
            "fn": fn,
            "mutates": mutates,
            "description": description.strip(),
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        }
        return fn
    return decorator


def describe():
    """Return every tool as ``{name, description, input_schema, mutates}``."""
    return [
        {"name": name, "description": t["description"], "input_schema": t["input_schema"],
         "mutates": t["mutates"]}
        for name, t in sorted(TOOLS.items())
    ]


def call(name, arguments=None):
    """Call a tool by name with a dict of arguments (main thread only)."""
    entry = TOOLS.get(name)
    if entry is None:
        raise APIError("Unknown tool: {!r}".format(name))
    arguments = arguments or {}
    try:
        inspect.signature(entry["fn"]).bind(**arguments)
    except TypeError as ex:
        raise APIError("Invalid arguments for {:s}: {:s}".format(name, str(ex))) from None
    result = entry["fn"](**arguments)
    if entry["mutates"]:
        _undo_push("Agent: " + name)
    return result


# -----------------------------------------------------------------------------
# Internal helpers.

def _scene():
    context = bpy.context
    scene = getattr(context, "sequencer_scene", None) or context.scene
    if scene is None:
        raise APIError("No scene loaded")
    if scene.sequence_editor is None:
        scene.sequence_editor_create()
    return scene


def _sequencer_area():
    wm = bpy.context.window_manager
    for window in getattr(wm, "windows", ()):
        for area in window.screen.areas:
            if area.type == 'SEQUENCE_EDITOR':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if region is not None:
                    return window, area, region
    return None


def _override():
    """Context override so sequencer operators run with or without a visible editor."""
    scene = _scene()
    override = {"scene": scene, "sequencer_scene": scene}
    found = _sequencer_area()
    if found is not None:
        window, area, region = found
        override.update(window=window, area=area, region=region, screen=window.screen)
    return override


def _undo_push(message):
    if bpy.app.background:
        return
    try:
        with bpy.context.temp_override(**_override()):
            bpy.ops.ed.undo_push(message=message)
    except Exception:
        pass


def _strips(scene=None):
    scene = scene or _scene()
    return scene.sequence_editor.strips_all


def _strip(name):
    strip = _strips().get(name)
    if strip is None:
        raise APIError("No strip named {!r}".format(name))
    return strip


def _left(strip):
    return strip.left_handle if hasattr(strip, "left_handle") else strip.frame_final_start


def _right(strip):
    return strip.right_handle if hasattr(strip, "right_handle") else strip.frame_final_end


def _strip_info(strip):
    info = {
        "name": strip.name,
        "type": strip.type,
        "channel": strip.channel,
        "frame_start": _left(strip),
        "frame_end": _right(strip),
        "duration": _right(strip) - _left(strip),
        "content_start": strip.frame_start,
        "mute": strip.mute,
        "lock": strip.lock,
        "select": strip.select,
        "modifiers": [{"name": m.name, "type": m.type, "mute": m.mute} for m in strip.modifiers],
    }
    for attr in ("filepath", "volume", "blend_alpha", "blend_type", "text"):
        if hasattr(strip, attr):
            info[attr] = getattr(strip, attr)
    if strip.type == 'SOUND' and getattr(strip, "sound", None) is not None:
        info["filepath"] = strip.sound.filepath
    if strip.type == 'IMAGE' and getattr(strip, "directory", None) is not None:
        info["filepath"] = os.path.join(strip.directory, strip.elements[0].filename)
    return info


def _select_only(names):
    strips = _strips()
    for strip in strips:
        strip.select = False
    for name in names:
        _strip(name).select = True
    if names:
        _scene().sequence_editor.active_strip = _strip(names[0])


def _run_op(op, **kwargs):
    with bpy.context.temp_override(**_override()):
        result = op('EXEC_DEFAULT', **kwargs)
    if 'FINISHED' not in result:
        raise APIError("Operation did not run: {:s}".format(op.idname_py()))
    return result


def _channel_end(channel):
    end = _scene().frame_start
    for strip in _strips():
        if strip.channel == channel:
            end = max(end, _right(strip))
    return end


def _abspath(filepath):
    path = bpy.path.abspath(filepath)
    if not os.path.exists(path):
        raise APIError("File not found: {:s}".format(filepath))
    return path


# -----------------------------------------------------------------------------
# Project.

@tool(mutates=False)
def ping() -> dict:
    """Check the editor is reachable. Returns version and whether a UI is running."""
    return {"ok": True, "version": bpy.app.version_string, "background": bpy.app.background}


@tool(mutates=False)
def get_project() -> dict:
    """Get project settings, playhead, markers, preview range and a summary of all strips."""
    scene = _scene()
    render = scene.render
    return {
        "filepath": bpy.data.filepath,
        "is_dirty": bpy.data.is_dirty,
        "scene": scene.name,
        "fps": render.fps / render.fps_base,
        "resolution": [render.resolution_x, render.resolution_y],
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "frame_current": scene.frame_current,
        "preview_range": ([scene.frame_preview_start, scene.frame_preview_end]
                          if scene.use_preview_range else None),
        "markers": [{"name": m.name, "frame": m.frame} for m in scene.timeline_markers],
        "strip_count": len(_strips()),
        "channels_used": sorted({s.channel for s in _strips()}),
    }


@tool()
def set_project(fps: typing.Optional[float] = None,
                width: typing.Optional[int] = None,
                height: typing.Optional[int] = None,
                frame_start: typing.Optional[int] = None,
                frame_end: typing.Optional[int] = None) -> dict:
    """Change project settings. Only the given values are changed.

    :arg fps: Frames per second, e.g. 23.976, 24, 25, 29.97, 30, 60.
    :arg width: Output width in pixels.
    :arg height: Output height in pixels.
    """
    scene = _scene()
    render = scene.render
    if fps is not None:
        known = {23.976: (24, 1.001), 29.97: (30, 1.001), 59.94: (60, 1.001)}
        base = known.get(round(fps, 3))
        if base:
            render.fps, render.fps_base = base
        else:
            render.fps, render.fps_base = int(round(fps)), int(round(fps)) / fps
    if width is not None:
        render.resolution_x = width
    if height is not None:
        render.resolution_y = height
    if width is not None or height is not None:
        render.resolution_percentage = 100
    if frame_start is not None:
        scene.frame_start = frame_start
    if frame_end is not None:
        scene.frame_end = frame_end
    return get_project()


@tool()
def open_project(filepath: str) -> dict:
    """Open a project (.blend) file, replacing the current one (unsaved changes are lost)."""
    bpy.ops.wm.open_mainfile(filepath=_abspath(filepath))
    return get_project()


@tool(mutates=False)
def save_project(filepath: typing.Optional[str] = None) -> dict:
    """Save the project. Without a path, saves to the current file.

    :arg filepath: Path ending in .blend. Required if the project was never saved.
    """
    if filepath:
        bpy.ops.wm.save_as_mainfile(filepath=bpy.path.abspath(filepath), check_existing=False)
    elif bpy.data.filepath:
        bpy.ops.wm.save_mainfile()
    else:
        raise APIError("Project has never been saved, pass a filepath")
    return {"filepath": bpy.data.filepath}


@tool()
def fit_project_to_strips() -> dict:
    """Set the project start/end frames to cover all strips."""
    strips = list(_strips())
    if not strips:
        raise APIError("No strips")
    scene = _scene()
    scene.frame_start = min(_left(s) for s in strips)
    scene.frame_end = max(_right(s) for s in strips) - 1
    return get_project()


# -----------------------------------------------------------------------------
# Inspecting.

@tool(mutates=False)
def list_strips(channel: typing.Optional[int] = None) -> list:
    """List strips (optionally on one channel), ordered by channel then start frame."""
    strips = [s for s in _strips() if channel is None or s.channel == channel]
    strips.sort(key=lambda s: (s.channel, _left(s)))
    return [_strip_info(s) for s in strips]


@tool(mutates=False)
def get_strip(name: str) -> dict:
    """Get full details of one strip, including all of its editable properties."""
    strip = _strip(name)
    info = _strip_info(strip)
    props = {}
    for prop in strip.bl_rna.properties:
        if prop.is_readonly or prop.type in {'POINTER', 'COLLECTION'}:
            continue
        value = getattr(strip, prop.identifier)
        if prop.type in {'FLOAT', 'INT', 'BOOLEAN'} and getattr(prop, "array_length", 0):
            value = list(value)
        elif prop.type == 'ENUM' and prop.is_enum_flag:
            value = sorted(value)
        props[prop.identifier] = value
    info["properties"] = props
    return info


@tool(mutates=False)
def get_strips_at_frame(frame: int) -> list:
    """List strips that cover a given frame."""
    return [_strip_info(s) for s in _strips() if _left(s) <= frame < _right(s)]


# -----------------------------------------------------------------------------
# Finding media.

_MEDIA_KINDS = {
    "video": {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mxf", ".mts"},
    "audio": {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".aif", ".aiff"},
    "image": {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".webp", ".bmp"},
    "lut": {".cube"},
    "project": {".blend"},
    "text": {".txt", ".srt", ".json"},
}


def media_root():
    """Folder media is looked for in: ``$VSE_DATA/media`` on a server, else the user's home."""
    data = os.environ.get("VSE_DATA")
    if data and os.path.isdir(os.path.join(data, "media")):
        return os.path.join(data, "media")
    return os.path.expanduser("~")


@tool(mutates=False)
def list_media(folder: str = "", kind: typing.Optional[str] = None, limit: int = 200) -> dict:
    """List media files available to the editor, with the path to pass to add_movie etc.

    :arg folder: Folder to look in, absolute or relative to the media folder. Default: the media folder.
    :arg kind: Only this kind: video, audio, image, lut, project or text.
    :arg limit: Most files to return.

    Also returns "imported": every file imported from Google Drive, with its drive_id.
    """
    root = media_root()
    path = folder if os.path.isabs(folder) else os.path.join(root, folder)
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        raise APIError("No such folder: {:s}".format(folder or path))
    by_ext = {ext: name for name, exts in _MEDIA_KINDS.items() for ext in exts}
    files, folders = [], []
    for base, dirs, names in os.walk(path):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        if base == path:
            folders = list(dirs)
        for name in sorted(names):
            file_kind = by_ext.get(os.path.splitext(name)[1].lower())
            if file_kind is None or (kind and file_kind != kind):
                continue
            full = os.path.join(base, name)
            files.append({"path": full, "name": name, "kind": file_kind,
                          "megabytes": round(os.path.getsize(full) / 1e6, 2)})
            if len(files) >= limit:
                return {"folder": path, "folders": folders, "files": files, "truncated": True,
                        "imported": _imported()}
    return {"folder": path, "folders": folders, "files": files, "truncated": False,
            "imported": _imported()}


def _imported():
    """What has been imported from Google Drive (drive_id, name, size, duration, kind, path),
    so the site can mark files "already added"."""
    try:
        from .assembly import imports
        return imports.imported()
    except Exception:
        return []


# -----------------------------------------------------------------------------
# Adding media.

def _place(channel, frame_start):
    return frame_start if frame_start is not None else _channel_end(channel)


@tool()
def add_movie(filepath: str, channel: int = 1, frame_start: typing.Optional[int] = None,
              with_audio: bool = True, name: typing.Optional[str] = None) -> list:
    """Add a video file. Without frame_start it is appended at the end of the channel.

    :arg with_audio: Also add the file's audio on the channel above (channel + 1).
    """
    path = _abspath(filepath)
    frame = _place(channel, frame_start)
    strips = _scene().sequence_editor.strips
    name = name or os.path.basename(path)
    movie = strips.new_movie(name=name, filepath=path, channel=channel, frame_start=frame)
    added = [movie]
    if with_audio:
        try:
            sound = strips.new_sound(name=name + " (audio)", filepath=path,
                                     channel=channel + 1, frame_start=frame)
            if _right(sound) > _left(sound):
                sound.right_handle = _right(movie) if hasattr(sound, "right_handle") else None
                added.append(sound)
            else:
                strips.remove(sound)
        except (RuntimeError, TypeError):
            pass  # No audio stream.
    return [_strip_info(s) for s in added]


@tool()
def add_sound(filepath: str, channel: int = 1, frame_start: typing.Optional[int] = None,
              name: typing.Optional[str] = None) -> dict:
    """Add an audio file (wav, mp3, flac, ogg, aac...)."""
    path = _abspath(filepath)
    strip = _scene().sequence_editor.strips.new_sound(
        name=name or os.path.basename(path), filepath=path, channel=channel,
        frame_start=_place(channel, frame_start))
    return _strip_info(strip)


@tool()
def add_image(filepath: str, channel: int = 1, frame_start: typing.Optional[int] = None,
              length: int = 120, name: typing.Optional[str] = None) -> dict:
    """Add a still image shown for `length` frames."""
    path = _abspath(filepath)
    strip = _scene().sequence_editor.strips.new_image(
        name=name or os.path.basename(path), filepath=path, channel=channel,
        frame_start=_place(channel, frame_start))
    strip.frame_final_duration = length
    return _strip_info(strip)


@tool()
def add_text(text: str, channel: int = 2, frame_start: typing.Optional[int] = None,
             length: int = 120, font_size: float = 60.0,
             color: typing.Optional[typing.List[float]] = None,
             location: typing.Optional[typing.List[float]] = None,
             name: typing.Optional[str] = None) -> dict:
    """Add a text (title) strip.

    :arg color: RGBA, each 0..1. Default white.
    :arg location: [x, y] position as a fraction of the frame, [0.5, 0.5] is the center.
    """
    strip = _scene().sequence_editor.strips.new_effect(
        name=name or "Text", type='TEXT', channel=channel,
        frame_start=_place(channel, frame_start), length=length)
    strip.text = text
    strip.font_size = font_size
    if color is not None:
        strip.color = (list(color) + [1.0])[:4]
    if location is not None:
        strip.location = location
    return _strip_info(strip)


@tool()
def add_color(color: typing.List[float], channel: int = 1,
              frame_start: typing.Optional[int] = None, length: int = 120,
              name: typing.Optional[str] = None) -> dict:
    """Add a solid color strip (e.g. black slug). Color is RGB 0..1."""
    strip = _scene().sequence_editor.strips.new_effect(
        name=name or "Color", type='COLOR', channel=channel,
        frame_start=_place(channel, frame_start), length=length)
    strip.color = list(color)[:3]
    return _strip_info(strip)


# -----------------------------------------------------------------------------
# Editing.

@tool()
def move_strip(name: str, frame_start: typing.Optional[int] = None,
               channel: typing.Optional[int] = None) -> dict:
    """Move a strip so its visible start is at frame_start, and/or to another channel."""
    strip = _strip(name)
    if frame_start is not None:
        strip.frame_start += frame_start - _left(strip)
    if channel is not None:
        strip.channel = channel
    return _strip_info(strip)


@tool()
def trim_strip(name: str, frame_start: typing.Optional[int] = None,
               frame_end: typing.Optional[int] = None) -> dict:
    """Set a strip's visible start and/or end (exclusive) frame without moving its content."""
    strip = _strip(name)
    if frame_start is not None:
        strip.left_handle = frame_start
    if frame_end is not None:
        strip.right_handle = frame_end
    return _strip_info(strip)


@tool()
def split_strip(name: str, frame: int, method: typing.Literal["SOFT", "HARD"] = "SOFT") -> list:
    """Split a strip at a frame. Returns [left, right] strips."""
    strip = _strip(name)
    if not (_left(strip) < frame < _right(strip)):
        raise APIError("Frame {:d} is not inside strip {!r}".format(frame, name))
    right = strip.split(frame=frame, split_method=method)
    if right is None:
        raise APIError("Strip could not be split")
    return [_strip_info(strip), _strip_info(right)]


@tool()
def split_at_frame(frame: int, channel: typing.Optional[int] = None) -> list:
    """Split every strip under a frame (optionally only on one channel), like a blade cut."""
    results = []
    for strip in list(_strips()):
        if channel is not None and strip.channel != channel:
            continue
        if _left(strip) < frame < _right(strip) and not strip.lock:
            right = strip.split(frame=frame, split_method='SOFT')
            if right is not None:
                results.append({"left": strip.name, "right": right.name})
    return results


@tool()
def delete_strips(names: typing.List[str]) -> dict:
    """Delete strips, leaving gaps."""
    _select_only(names)
    _run_op(bpy.ops.sequencer.delete)
    return {"deleted": names}


@tool()
def ripple_delete(names: typing.List[str], all_channels: bool = True,
                  markers: bool = True) -> dict:
    """Delete strips and close the gaps they leave, shifting later strips left.

    :arg all_channels: Shift strips on every channel (keeps sync), else only the same channels.
    :arg markers: Shift markers along with strips.
    """
    _select_only(names)
    _run_op(bpy.ops.sequencer.ripple_delete, all_channels=all_channels, markers=markers)
    return {"deleted": names, "strips": list_strips()}


@tool()
def ripple_cut(names: typing.List[str], all_channels: bool = True, markers: bool = True) -> dict:
    """Cut strips to the clipboard and close the gaps (paste later with paste_strips)."""
    _select_only(names)
    _run_op(bpy.ops.sequencer.ripple_cut, all_channels=all_channels, markers=markers)
    return {"cut": names, "strips": list_strips()}


@tool()
def ripple_delete_range(frame_start: int, frame_end: int, channel: int = 0,
                        markers: bool = True) -> dict:
    """Remove frames [frame_start, frame_end) from the timeline and close the gap.

    Strips crossing the range edges are trimmed or split.
    :arg frame_end: Exclusive end frame.
    :arg channel: Only this channel (0 = all channels, keeps audio/video in sync).
    """
    _run_op(bpy.ops.sequencer.ripple_delete_range, frame_start=frame_start,
            frame_end=frame_end, channel=channel, markers=markers)
    return {"removed": [frame_start, frame_end], "strips": list_strips()}


@tool()
def paste_strips(frame: typing.Optional[int] = None) -> list:
    """Paste strips from the clipboard at a frame (default: the playhead)."""
    scene = _scene()
    if frame is not None:
        scene.frame_current = frame
    before = {s.name for s in _strips()}
    _run_op(bpy.ops.sequencer.paste)
    return [_strip_info(s) for s in _strips() if s.name not in before]


@tool()
def set_strip_properties(name: str, properties: dict) -> dict:
    """Set any editable strip properties, e.g. {"volume": 0.5, "blend_alpha": 0.8, "mute": true}.

    Use get_strip to see the available property names and current values.
    """
    strip = _strip(name)
    errors = {}
    for key, value in properties.items():
        prop = strip.bl_rna.properties.get(key)
        if prop is None or prop.is_readonly:
            errors[key] = "not an editable property"
            continue
        try:
            setattr(strip, key, value)
        except (TypeError, ValueError, AttributeError) as ex:
            errors[key] = str(ex)
    if errors:
        raise APIError("Could not set: " + ", ".join(
            "{:s} ({:s})".format(k, v) for k, v in errors.items()))
    return _strip_info(strip)


@tool()
def rename_strip(name: str, new_name: str) -> dict:
    """Rename a strip."""
    strip = _strip(name)
    strip.name = new_name
    return _strip_info(strip)


# -----------------------------------------------------------------------------
# Color.

@tool()
def apply_lut(name: str, filepath: str, intensity: float = 1.0, clamp: bool = False,
              modifier_name: str = "LUT") -> dict:
    """Color grade a strip with a LUT file (.cube). Re-applying updates the same modifier.

    :arg intensity: 0 = original image, 1 = fully graded.
    :arg modifier_name: Name of the LUT modifier, use different names to stack LUTs.
    """
    strip = _strip(name)
    path = _abspath(filepath)
    mod = strip.modifiers.get(modifier_name)
    if mod is not None and mod.type != 'LUT':
        raise APIError("Modifier {!r} exists and is not a LUT".format(modifier_name))
    if mod is None:
        mod = strip.modifiers.new(name=modifier_name, type='LUT')
    mod.filepath = path
    mod.intensity = intensity
    mod.use_clamp = clamp
    return _strip_info(strip)


@tool()
def add_modifier(name: str, type: str, properties: typing.Optional[dict] = None,
                 modifier_name: typing.Optional[str] = None) -> dict:
    """Add a strip modifier and set its properties.

    :arg type: One of LUT, BRIGHT_CONTRAST, COLOR_BALANCE, CURVES, HUE_CORRECT, TONEMAP,
        WHITE_BALANCE, MASK, COMPOSITOR, SOUND_EQUALIZER, PITCH, ECHO.
    """
    strip = _strip(name)
    mod = strip.modifiers.new(name=modifier_name or type.title(), type=type)
    for key, value in (properties or {}).items():
        if not hasattr(mod, key):
            raise APIError("Modifier {:s} has no property {!r}".format(type, key))
        setattr(mod, key, value)
    return _strip_info(strip)


@tool()
def remove_modifier(name: str, modifier_name: str) -> dict:
    """Remove a modifier from a strip."""
    strip = _strip(name)
    mod = strip.modifiers.get(modifier_name)
    if mod is None:
        raise APIError("Strip {!r} has no modifier {!r}".format(name, modifier_name))
    strip.modifiers.remove(mod)
    return _strip_info(strip)


# -----------------------------------------------------------------------------
# Timeline.

@tool()
def set_frame(frame: int) -> dict:
    """Move the playhead."""
    _scene().frame_current = frame
    return {"frame_current": frame}


@tool()
def set_preview_range(frame_start: typing.Optional[int] = None,
                      frame_end: typing.Optional[int] = None) -> dict:
    """Set the In/Out (preview) range. Call with no arguments to clear it."""
    scene = _scene()
    if frame_start is None and frame_end is None:
        scene.use_preview_range = False
    else:
        scene.use_preview_range = True
        if frame_start is not None:
            scene.frame_preview_start = frame_start
        if frame_end is not None:
            scene.frame_preview_end = frame_end
    return get_project()


@tool()
def add_marker(frame: int, name: str = "") -> dict:
    """Add a timeline marker."""
    marker = _scene().timeline_markers.new(name=name or "F_{:d}".format(frame), frame=frame)
    return {"name": marker.name, "frame": marker.frame}


@tool(mutates=False)
def undo() -> dict:
    """Undo the last step (agent or manual)."""
    with bpy.context.temp_override(**_override()):
        bpy.ops.ed.undo()
    return {"ok": True}


@tool(mutates=False)
def redo() -> dict:
    """Redo the last undone step."""
    with bpy.context.temp_override(**_override()):
        bpy.ops.ed.redo()
    return {"ok": True}


# -----------------------------------------------------------------------------
# Output.

_FORMATS = {
    "H264_MP4": ("MPEG4", "H264", "AAC"),
    "H265_MP4": ("MPEG4", "H265", "AAC"),
    "PRORES_MOV": ("QUICKTIME", "PRORES", "PCM"),
    "WEBM": ("WEBM", "WEBM", "OPUS"),
}


@tool(mutates=False)
def render_video(filepath: str, format: typing.Literal["H264_MP4", "H265_MP4", "PRORES_MOV",
                                                        "WEBM"] = "H264_MP4",
                 frame_start: typing.Optional[int] = None,
                 frame_end: typing.Optional[int] = None,
                 quality: typing.Literal["LOW", "MEDIUM", "HIGH", "LOSSLESS"] = "HIGH") -> dict:
    """Render the edit to a video file. Blocks until finished.

    :arg frame_end: Inclusive. Defaults to the project range.
    """
    scene = _scene()
    render = scene.render
    container, codec, audio = _FORMATS[format]
    saved = (scene.frame_start, scene.frame_end, render.filepath)
    try:
        if frame_start is not None:
            scene.frame_start = frame_start
        if frame_end is not None:
            scene.frame_end = frame_end
        render.image_settings.media_type = 'VIDEO'
        render.image_settings.file_format = 'FFMPEG'
        render.ffmpeg.format = container
        render.ffmpeg.codec = codec
        render.ffmpeg.audio_codec = audio
        if codec in {"H264", "H265", "WEBM"}:
            render.ffmpeg.constant_rate_factor = quality
        render.use_file_extension = False
        render.filepath = bpy.path.abspath(filepath)
        os.makedirs(os.path.dirname(render.filepath) or ".", exist_ok=True)
        with bpy.context.temp_override(scene=scene):
            bpy.ops.render.render(animation=True, use_sequencer_scene=False)
    finally:
        scene.frame_start, scene.frame_end, render.filepath = saved
    out = bpy.path.abspath(filepath)
    return {"filepath": out, "exists": os.path.exists(out),
            "bytes": os.path.getsize(out) if os.path.exists(out) else 0}


@tool(mutates=False)
def render_frame(frame: int, filepath: str) -> dict:
    """Render one frame of the edit to a PNG, so an agent can look at the result."""
    scene = _scene()
    render = scene.render
    saved = (scene.frame_current, render.filepath, render.image_settings.media_type,
             render.image_settings.file_format)
    try:
        scene.frame_current = frame
        render.image_settings.media_type = 'IMAGE'
        render.image_settings.file_format = 'PNG'
        render.filepath = bpy.path.abspath(filepath)
        with bpy.context.temp_override(scene=scene):
            bpy.ops.render.render(write_still=True)
    finally:
        (scene.frame_current, render.filepath, render.image_settings.media_type,
         render.image_settings.file_format) = saved
    out = bpy.path.abspath(filepath)
    if not os.path.exists(out) and os.path.exists(out + ".png"):
        out += ".png"
    return {"filepath": out, "exists": os.path.exists(out)}


@tool(mutates=False)
def export_otio(filepath: str) -> dict:
    """Export the timeline as OpenTimelineIO (.otio), for interchange with other editors."""
    op = getattr(bpy.ops.wm, "otio_export", None) or getattr(bpy.ops.sequencer, "export_otio", None)
    if op is None:
        raise APIError("OpenTimelineIO export is not available in this build")
    with bpy.context.temp_override(**_override()):
        op(filepath=bpy.path.abspath(filepath))
    return {"filepath": bpy.path.abspath(filepath)}


@tool(mutates=False)
def run_operator(operator: str, properties: typing.Optional[dict] = None) -> dict:
    """Escape hatch: run any operator by id, e.g. "sequencer.gap_remove", in sequencer context.

    Prefer dedicated tools; this exists so new features are usable before they get one.
    """
    module, _, name = operator.partition(".")
    op = getattr(getattr(bpy.ops, module, None), name, None)
    if op is None:
        raise APIError("Unknown operator {!r}".format(operator))
    with bpy.context.temp_override(**_override()):
        result = op('EXEC_DEFAULT', **(properties or {}))
    _undo_push("Agent: " + operator)
    return {"result": sorted(result)}
