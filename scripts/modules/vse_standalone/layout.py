# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Arrangement of the two editor panes (game view / preview and timeline).

``SIDE_BY_SIDE`` (the startup file's layout): preview left, timeline right.
``STACKED``: preview on top, timeline below.

Rearranging joins the two areas and splits the result the other way, so it only acts on a
screen made of exactly one preview and one timeline (the Video Editing layout). The choice
is remembered in the config folder (``vse_layout.json``) and applied on the next start.
"""

import json
import os

import bpy

ARRANGEMENTS = (
    ('SIDE_BY_SIDE', "Side by Side", "Preview on the left, timeline on the right"),
    ('STACKED', "Stacked", "Preview on top, timeline below"),
)

# Share of the window the preview gets.
_PREVIEW_SHARE = {'SIDE_BY_SIDE': 0.45, 'STACKED': 0.6}


def _config_path():
    return os.path.join(bpy.utils.user_resource('CONFIG'), "vse_layout.json")


def saved_arrangement():
    try:
        with open(_config_path(), encoding="utf-8") as fh:
            value = json.load(fh).get("arrangement")
    except (OSError, ValueError, AttributeError):
        return None
    return value if value in _PREVIEW_SHARE else None


def _save_arrangement(value):
    path = _config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"arrangement": value}, fh)
    except OSError as ex:
        print("Pane arrangement not saved: {:s}".format(str(ex)))


def _panes(screen):
    """(preview, timeline) when the screen is exactly those two, else None."""
    areas = list(screen.areas)
    if len(areas) != 2 or any(a.type != 'SEQUENCE_EDITOR' for a in areas):
        return None
    by_type = {a.spaces.active.view_type: a for a in areas}
    if set(by_type) != {'PREVIEW', 'SEQUENCER'}:
        return None
    return by_type['PREVIEW'], by_type['SEQUENCER']


def current(screen):
    """The screen's arrangement, or None when it is not a preview + timeline layout."""
    panes = _panes(screen)
    if panes is None:
        return None
    preview, timeline = panes
    return 'STACKED' if preview.y > timeline.y else 'SIDE_BY_SIDE'


def _region_flags(space):
    return {p.identifier: getattr(space, p.identifier) for p in space.bl_rna.properties
            if p.identifier.startswith("show_region_") and not p.is_readonly}


def arrange(window, arrangement, *, remember=True):
    """Rearrange ``window``'s panes. Returns False when its screen can't be rearranged."""
    if arrangement not in _PREVIEW_SHARE:
        raise ValueError("Unknown arrangement {!r}".format(arrangement))
    screen = window.screen
    panes = _panes(screen)
    if panes is None:
        return False
    if remember:
        _save_arrangement(arrangement)
    if current(screen) == arrangement:
        return True
    preview, timeline = panes
    flags = _region_flags(timeline.spaces.active)

    with bpy.context.temp_override(window=window, screen=screen, area=preview):
        bpy.ops.screen.area_join(
            source_xy=(preview.x + preview.width // 2, preview.y + preview.height // 2),
            target_xy=(timeline.x + timeline.width // 2, timeline.y + timeline.height // 2))
    area = screen.areas[0]
    # `factor` places the split line from the bottom or left edge. Which side the new area
    # lands on depends on the factor, so the panes are assigned by position afterwards:
    # preview on top (stacked) or on the left (side by side).
    share = _PREVIEW_SHARE[arrangement]
    if arrangement == 'STACKED':
        direction, factor = 'HORIZONTAL', 1.0 - share
    else:
        direction, factor = 'VERTICAL', share
    with bpy.context.temp_override(window=window, screen=screen, area=area):
        bpy.ops.screen.area_split(direction=direction, factor=factor)

    # Both halves hold the preview's space now. Area coordinates are only updated on the next
    # redraw, so go by where the split puts the new area: right/top for a factor over a
    # half, left/bottom otherwise (measured on Blender 5.3).
    added = next(a for a in screen.areas if a != area)
    added_is_top_or_right = factor > 0.5
    if arrangement == 'STACKED':
        timeline_area = area if added_is_top_or_right else added
    else:
        timeline_area = added if added_is_top_or_right else area
    space = timeline_area.spaces.active
    space.view_type = 'SEQUENCER'
    for name, value in flags.items():
        try:
            setattr(space, name, value)
        except (AttributeError, TypeError):
            pass
    # Show the whole edit once the new area has its size.
    bpy.app.timers.register(lambda: _view_all(window), first_interval=0.1)
    return True


def _view_all(window):
    try:
        panes = _panes(window.screen)
    except ReferenceError:
        return None
    if panes is None:
        return None
    timeline = panes[1]
    region = next((r for r in timeline.regions if r.type == 'WINDOW'), None)
    if region is not None:
        with bpy.context.temp_override(window=window, screen=window.screen, area=timeline,
                                       region=region):
            bpy.ops.sequencer.view_all()
    return None


def apply_saved():
    """Apply the remembered arrangement to every window (called once at start)."""
    arrangement = saved_arrangement()
    if arrangement is None:
        return
    for window in bpy.context.window_manager.windows:
        arrange(window, arrangement, remember=False)


class VSE_OT_arrange_panes(bpy.types.Operator):
    """Arrange the preview and timeline panes"""
    bl_idname = "vse.arrange_panes"
    bl_label = "Arrange Panes"

    arrangement: bpy.props.EnumProperty(items=ARRANGEMENTS, name="Arrangement")

    def execute(self, context):
        if not arrange(context.window, self.arrangement):
            self.report({'WARNING'}, "Only the two-pane layout (preview and timeline) can be rearranged")
            return {'CANCELLED'}
        return {'FINISHED'}


class SEQUENCER_MT_vse_arrangement(bpy.types.Menu):
    bl_label = "Arrangement"

    def draw(self, context):
        now = current(context.window.screen)
        for value, label, _desc in ARRANGEMENTS:
            self.layout.operator(VSE_OT_arrange_panes.bl_idname, text=label,
                                 icon='CHECKBOX_HLT' if now == value else 'CHECKBOX_DEHLT'
                                 ).arrangement = value


def draw_view_menu(self, context):
    self.layout.separator()
    self.layout.menu(SEQUENCER_MT_vse_arrangement.__name__)


classes = (VSE_OT_arrange_panes, SEQUENCER_MT_vse_arrangement)
