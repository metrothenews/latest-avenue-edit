# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The game view: the keyboard-driven pass interface, drawn natively inside the editor.

It lives in the sequencer *preview* pane (left), next to the full timeline (right), in one
window. Keys only act while the mouse is over the game view, so the editor beside it keeps
working normally and a person can step in at any moment.

Keys (same as latest avenue resolve):

``1 2 3 4 / 7 8 9 0``  preview LUT 1-8 of the current pass (live, in the editor's viewer)
``backspace``          preview with no LUT for this pass
``space``              apply the previewed LUT and go to the next clip
``left / right``       previous / next clip without applying
``tab``                switch between the look pass and the color pass
``F``                  game view full-screen on/off
``esc``                leave the game view (the header button brings it back)
"""

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader
from math import cos, sin, pi

from . import api, color_pass

KEYMAP = {'ONE': 1, 'TWO': 2, 'THREE': 3, 'FOUR': 4, 'SEVEN': 5, 'EIGHT': 6, 'NINE': 7, 'ZERO': 8,
          'NUMPAD_1': 1, 'NUMPAD_2': 2, 'NUMPAD_3': 3, 'NUMPAD_4': 4,
          'NUMPAD_7': 5, 'NUMPAD_8': 6, 'NUMPAD_9': 7, 'NUMPAD_0': 8}
KEY_LABELS = ("1", "2", "3", "4", "7", "8", "9", "0")

G = {
    "active": False,
    "pass": "look",
    "index": 0,
    "slot": 0,          # Slot being previewed for the current pass.
    "clips": [],        # Strip names.
    "committed": None,  # LUT paths applied to the current clip (before previewing).
    "done": False,
    "handle": None,
    "message": "",
}


# -----------------------------------------------------------------------------
# State.

def _strip():
    if not G["clips"]:
        return None
    return api._strips().get(G["clips"][G["index"]])


def _lut_name(kind, slot):
    if not slot:
        return ""
    mode = color_pass._mode()
    return color_pass._stem(mode[kind][slot - 1]).replace("_33Pt", "").replace("_", " ")


def _committed_slot(kind):
    strip = _strip()
    if strip is None or G["committed"] is None:
        return 0
    path = G["committed"].get(kind)
    if not path:
        return 0
    mode = color_pass._mode()
    import os
    norm = os.path.normcase(os.path.abspath(bpy.path.abspath(path)))
    for n, lut in enumerate(mode[kind], 1):
        if os.path.normcase(os.path.abspath(lut)) == norm:
            return n
    return 0


def _restore():
    """Drop the preview: put the clip's applied LUTs back."""
    strip = _strip()
    if strip is not None and G["committed"] is not None:
        color_pass._set_pass_luts(strip, G["committed"])


def _show_preview():
    strip = _strip()
    if strip is None:
        return
    mode = color_pass._mode()
    luts = dict(G["committed"] or {})
    luts[G["pass"]] = mode[G["pass"]][G["slot"] - 1] if G["slot"] else None
    color_pass._set_pass_luts(strip, luts)


def _arrive(index):
    """Go to a clip: playhead to its frame at 1 s, select it in the timeline."""
    if not G["clips"]:
        return
    _restore()
    G["index"] = max(0, min(len(G["clips"]) - 1, index))
    G["done"] = False
    strip = _strip()
    if strip is None:
        load()
        return
    G["committed"] = color_pass._current_luts(strip)
    G["slot"] = _committed_slot(G["pass"])
    scene = api._scene()
    scene.frame_current = color_pass._still_frame(strip)
    for other in api._strips():
        other.select = False
    strip.select = True
    scene.sequence_editor.active_strip = strip


def load():
    state = color_pass.color_pass_load(color_pass._mode()["name"])
    G["clips"] = [c["name"] for c in state["clips"]]
    G["index"] = 0
    G["committed"] = None
    G["done"] = False
    G["message"] = "" if G["clips"] else "Add video clips to the timeline."
    if G["clips"]:
        _arrive(0)


def _apply_and_next():
    strip = _strip()
    if strip is None:
        return
    if G["slot"] != _committed_slot(G["pass"]):
        _restore()
        color_pass.color_pass_apply(G["index"], G["pass"], G["slot"])
        G["committed"] = color_pass._current_luts(strip)
        api._undo_push("Color pass: {:s} {:s}".format(
            strip.name, _lut_name(G["pass"], G["slot"]) or "no LUT"))
    if G["index"] == len(G["clips"]) - 1:
        G["done"] = True
    else:
        _arrive(G["index"] + 1)


def _set_pass(kind):
    if kind == G["pass"] or not color_pass._mode()[kind]:
        return
    _restore()
    G["pass"] = kind
    G["done"] = False
    G["slot"] = _committed_slot(kind)


# -----------------------------------------------------------------------------
# Drawing (latest avenue look: glass panels, pill labels, white text, no accent color).

def _rounded(x, y, w, h, r, color):
    r = min(r, w / 2, h / 2)
    verts = []
    for cx, cy, a0 in ((x + w - r, y + h - r, 0.0), (x + r, y + h - r, pi / 2),
                       (x + r, y + r, pi), (x + w - r, y + r, 3 * pi / 2)):
        for i in range(7):
            a = a0 + (pi / 2) * i / 6
            verts.append((cx + cos(a) * r, cy + sin(a) * r))
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _panel(x, y, w, h, r, fill, border):
    _rounded(x, y, w, h, r, border)
    _rounded(x + 1, y + 1, w - 2, h - 2, max(r - 1, 0), fill)


def _text(text, x, y, size, color, align="left"):
    blf.size(0, size)
    blf.color(0, *color)
    tw, _th = blf.dimensions(0, text)
    if align == "center":
        x -= tw / 2
    elif align == "right":
        x -= tw
    blf.position(0, round(x), round(y), 0)
    blf.draw(0, text)
    return tw


def _pill(text, x, y, s, *, bright=False, align="left"):
    size = 12 * s
    blf.size(0, size)
    tw, _ = blf.dimensions(0, text)
    w, h = tw + 24 * s, 26 * s
    if align == "right":
        x -= w
    elif align == "center":
        x -= w / 2
    fill = (0.22, 0.22, 0.23, 0.92) if bright else (0.0, 0.0, 0.0, 0.6)
    border = (1, 1, 1, 0.25) if bright else (1, 1, 1, 0.15)
    _panel(x, y, w, h, h / 2, fill, border)
    _text(text, x + 12 * s, y + 8 * s, size, (1, 1, 1, 1))
    return w


WHITE = (1, 1, 1, 1)
DIM = (1, 1, 1, 0.6)
FAINT = (1, 1, 1, 0.4)
GLASS = (0.035, 0.04, 0.055, 0.82)
GLASS_BORDER = (0.22, 0.25, 0.32, 0.7)
ON = (0.24, 0.24, 0.26, 0.95)
ON_BORDER = (1, 1, 1, 0.4)


def _draw():
    if not G["active"]:
        return
    context = bpy.context
    region = context.region
    space = context.space_data
    if region is None or space is None or getattr(space, "view_type", "") != 'PREVIEW':
        return
    s = context.preferences.system.ui_scale
    W, H = region.width, region.height
    gpu.state.blend_set('ALPHA')
    try:
        _draw_hud(W, H, s)
    finally:
        gpu.state.blend_set('NONE')


def _draw_hud(W, H, s):
    mode = color_pass._mode()
    pad = 16 * s

    # Top bar (glass-bar).
    bar_h = 44 * s
    _rounded(0, H - bar_h, W, bar_h, 0, (0, 0, 0, 0.55))
    _rounded(0, H - bar_h, W, 1, 0, (1, 1, 1, 0.1))
    x = pad
    x += _text("latest avenue", x, H - bar_h + 15 * s, 16 * s, WHITE) + 14 * s
    for kind, label in (("look", "1 Look"), ("color", "2 Color")):
        active = kind == G["pass"]
        blf.size(0, 12 * s)
        tw, _ = blf.dimensions(0, label)
        w = tw + 22 * s
        if active:
            _rounded(x, H - bar_h + 9 * s, w, 26 * s, 13 * s, (1, 1, 1, 0.15))
        _text(label, x + 11 * s, H - bar_h + 17 * s, 12 * s, WHITE if active else DIM)
        x += w + 4 * s
    strip = _strip()
    if strip is not None:
        x += 12 * s
        x += _text("{:d} / {:d}".format(G["index"] + 1, len(G["clips"])), x,
                   H - bar_h + 16 * s, 13 * s, WHITE) + 10 * s
        _text(strip.name, x, H - bar_h + 16 * s, 13 * s, DIM)
    _text("F  full screen", W - pad, H - bar_h + 17 * s, 11 * s, FAINT, "right")

    if strip is None:
        _text(G["message"] or "Add video clips to the timeline.", W / 2, H / 2, 14 * s, DIM,
              "center")
        return

    # Labels over the picture: previewed (top-left), applied (top-right).
    look = G["slot"] if G["pass"] == "look" else _committed_slot("look")
    color = G["slot"] if G["pass"] == "color" else _committed_slot("color")
    shown = " + ".join(n for n in (_lut_name("look", look), _lut_name("color", color)) if n)
    _pill(shown or "no LUT", pad, H - bar_h - 38 * s, s)
    a_look, a_color = _committed_slot("look"), _committed_slot("color")
    if a_look or a_color:
        applied = " + ".join(n for n in (_lut_name("look", a_look),
                                         _lut_name("color", a_color)) if n)
        _pill("applied: " + applied, W - pad, H - bar_h - 38 * s, s, bright=True, align="right")

    # LUT keys: two groups of four, like the keyboard.
    key = min(72 * s, (W - 2 * pad - 40 * s) / 8.6)
    gap = key * 0.15
    group_gap = key * 0.5
    total = key * 8 + gap * 6 + group_gap
    x0 = (W - total) / 2
    y0 = 44 * s
    applied_slot = _committed_slot(G["pass"])
    small = "color" if G["pass"] == "color" else mode["name"]
    for n in range(8):
        kx = x0 + n * (key + gap) + (group_gap - gap if n >= 4 else 0)
        on = (n + 1) == G["slot"]
        _panel(kx, y0, key, key, 12 * s, ON if on else GLASS, ON_BORDER if on else GLASS_BORDER)
        _text(KEY_LABELS[n], kx + key / 2, y0 + key * 0.46, key * 0.32,
              WHITE if on else (1, 1, 1, 0.7), "center")
        _text("{:s} {:d}".format(small, n + 1).upper(), kx + key / 2, y0 + key * 0.2,
              max(key * 0.13, 8), FAINT, "center")
        if (n + 1) == applied_slot:
            _rounded(kx + key - 14 * s, y0 + key - 14 * s, 6 * s, 6 * s, 3 * s, WHITE)

    hints = "1-4  7-0 preview     tab switch pass     backspace no LUT     " \
            "space apply & next     ← → move"
    _text(hints, W / 2, 18 * s, 11 * s, (1, 1, 1, 0.5), "center")

    if G["done"]:
        cw, ch = min(440 * s, W - 2 * pad), 150 * s
        cx, cy = (W - cw) / 2, (H - ch) / 2
        _rounded(0, 0, W, H, 0, (0, 0, 0, 0.6))
        _panel(cx, cy, cw, ch, 12 * s, (0.05, 0.055, 0.07, 0.96), GLASS_BORDER)
        title = "First pass done" if G["pass"] == "look" else "Color pass done"
        count = sum(1 for name in G["clips"]
                    if color_pass._slot_of(api._strips()[name], G["pass"]))
        _text(title, W / 2, cy + ch - 50 * s, 22 * s, WHITE, "center")
        _text("{:d} of {:d} clips have a {:s} LUT.".format(count, len(G["clips"]), G["pass"]),
              W / 2, cy + ch - 82 * s, 13 * s, DIM, "center")
        nxt = "tab  start color pass     " if (G["pass"] == "look" and mode["color"]) else ""
        _text(nxt + "←  back to clips", W / 2, cy + 26 * s, 12 * s, (1, 1, 1, 0.7), "center")


# -----------------------------------------------------------------------------
# Operator.

def _preview_area_under_mouse(context, event):
    for area in context.window.screen.areas:
        if area.type != 'SEQUENCE_EDITOR':
            continue
        if not (area.x <= event.mouse_x < area.x + area.width and
                area.y <= event.mouse_y < area.y + area.height):
            continue
        if area.spaces.active.view_type == 'PREVIEW':
            return area
    return None


def find_preview_area(window):
    for area in window.screen.areas:
        if area.type == 'SEQUENCE_EDITOR' and area.spaces.active.view_type == 'PREVIEW':
            return area
    return None


def _redraw(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'SEQUENCE_EDITOR':
                area.tag_redraw()


class VSE_OT_game_view(bpy.types.Operator):
    """Step through clips and pick LUTs with single key presses, next to the full editor"""
    bl_idname = "vse.game_view"
    bl_label = "Game View"

    def invoke(self, context, event):
        if G["active"]:
            stop()
            _redraw(context)
            return {'CANCELLED'}
        G["active"] = True
        G["handle"] = bpy.types.SpaceSequenceEditor.draw_handler_add(
            _draw, (), 'PREVIEW', 'POST_PIXEL')
        try:
            load()
        except api.APIError as ex:
            G["message"] = str(ex)
        context.window_manager.modal_handler_add(self)
        _redraw(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if not G["active"]:
            return {'CANCELLED'}
        if event.value != 'PRESS' or event.ctrl or event.alt or event.oskey:
            return {'PASS_THROUGH'}
        area = _preview_area_under_mouse(context, event)
        if area is None:
            return {'PASS_THROUGH'}

        kind = event.type
        handled = True
        try:
            if kind == 'ESC':
                stop()
                _redraw(context)
                return {'CANCELLED'}
            elif kind == 'F':
                region = next(r for r in area.regions if r.type == 'PREVIEW')
                with context.temp_override(area=area, region=region):
                    bpy.ops.screen.screen_full_area(use_hide_panels=True)
            elif kind == 'TAB':
                _set_pass("color" if G["pass"] == "look" else "look")
                if G["done"]:
                    _arrive(0)
            elif not G["clips"]:
                load()
                handled = kind in KEYMAP or kind == 'SPACE'
            elif kind == 'LEFT_ARROW':
                _arrive(G["index"] if G["done"] else G["index"] - 1)
            elif G["done"]:
                handled = kind in KEYMAP or kind in {'SPACE', 'BACK_SPACE', 'RIGHT_ARROW'}
            elif kind in KEYMAP:
                G["slot"] = KEYMAP[kind]
                _show_preview()
            elif kind == 'BACK_SPACE':
                G["slot"] = 0
                _show_preview()
            elif kind == 'SPACE':
                _apply_and_next()
            elif kind == 'RIGHT_ARROW':
                _arrive(G["index"] + 1)
            else:
                handled = False
        except (api.APIError, KeyError, ReferenceError):
            # The timeline changed under us (manual edit or agent): re-read it.
            G["committed"] = None
            load()
        if handled:
            _redraw(context)
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


def stop():
    if not G["active"]:
        return
    try:
        _restore()
    except Exception:
        pass
    if G["handle"] is not None:
        bpy.types.SpaceSequenceEditor.draw_handler_remove(G["handle"], 'PREVIEW')
        G["handle"] = None
    G["active"] = False
    G["committed"] = None


def start(window=None):
    """Start the game view in a window's preview pane (used at startup)."""
    if G["active"]:
        return True
    wm = bpy.context.window_manager
    for win in ([window] if window else wm.windows):
        area = find_preview_area(win)
        if area is None:
            continue
        region = next((r for r in area.regions if r.type == 'PREVIEW'), None)
        if region is None:
            continue
        with bpy.context.temp_override(window=win, area=area, region=region):
            bpy.ops.vse.game_view('INVOKE_DEFAULT')
        return True
    return False
