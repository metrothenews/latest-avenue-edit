# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The "latest avenue" interface theme.

Derived from the latestavenue.com design system (``src/index.css``, ``tailwind.config.js``):
a near-black base, grey frosted-glass panels with faint borders, white text with reduced
opacity for secondary copy, a light-grey accent, pill-shaped buttons and only a hint of
blue-violet. Blender cannot blur, so "glass" is approximated by compositing the site's
translucent colors over the page base.

Every color is computed from :data:`TOKENS`, change a token and re-run :func:`apply`.
"""

__all__ = (
    "TOKENS",
    "apply",
    "apply_if_first_run",
    "write_preset",
)

# -----------------------------------------------------------------------------
# Design tokens (sRGB hex, alpha as 0..1), named after their source in the site.

TOKENS = {
    "ink": "#0a0a0a",              # html background / Tailwind `ink`.
    "gray_900": "#111827",         # .glass background base (bg-gray-900/30).
    "gray_700": "#374151",         # .glass border base (border-gray-700/50).
    "brand": "#e5e7eb",            # Accent: light grey, no saturated accent color.
    "white": "#ffffff",
    "black": "#000000",
    "glow": "#8273ff",             # rgba(130,115,255) top glow, use sparingly.
    "glow_cyan": "#3caaff",        # rgba(60,170,255) bottom-right glow.
}


def _hex(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _over(fg, alpha, bg):
    """Composite ``fg`` with ``alpha`` over ``bg`` (both hex), returns an RGB tuple."""
    f = _hex(fg) if isinstance(fg, str) else fg
    b = _hex(bg) if isinstance(bg, str) else bg
    return tuple(f[i] * alpha + b[i] * (1.0 - alpha) for i in range(3))


def _rgba(rgb, alpha=1.0):
    return (*rgb, alpha)


def _palette():
    t = TOKENS
    ink = _hex(t["ink"])
    # `.glass`: bg-gray-900/30 over the page.
    glass = _over(t["gray_900"], 0.30, ink)
    # Slightly raised glass for regions that sit on top of other regions (sidebars, menus).
    glass_raised = _over(t["white"], 0.04, glass)
    # `.glass-bar`: bg-black/30 over the page, tinted by the top glow (header / footer bars).
    bar = _over(t["glow"], 0.035, _over(t["black"], 0.30, ink))
    # Borders: border-gray-700/50 and border-white/10.
    border = _over(t["gray_700"], 0.50, ink)
    border_soft = _over(t["white"], 0.10, ink)
    # Buttons (components/ui/Button.jsx).
    btn_primary = _over(t["white"], 0.20, glass)      # bg-white/20
    btn_primary_border = _over(t["white"], 0.25, glass)  # border-white/25
    btn_secondary = _over(t["white"], 0.05, glass)    # bg-white/5
    btn_secondary_border = _over(t["white"], 0.15, glass)  # border-white/15
    btn_emphasis = _over(t["white"], 0.10, glass)     # bg-white/10 "emphasized card"
    # Text.
    text = _hex(t["white"])
    text_body = _over(t["white"], 0.70, glass)        # text-white/70
    text_dim = _over(t["white"], 0.50, glass)         # text-white/40..60
    text_faint = _over(t["white"], 0.30, glass)
    brand = _hex(t["brand"])
    glow = _hex(t["glow"])
    glow_soft = _over(t["glow"], 0.55, glass)
    return {
        "ink": ink, "glass": glass, "glass_raised": glass_raised, "bar": bar,
        "border": border, "border_soft": border_soft,
        "btn_primary": btn_primary, "btn_primary_border": btn_primary_border,
        "btn_secondary": btn_secondary, "btn_secondary_border": btn_secondary_border,
        "btn_emphasis": btn_emphasis,
        "text": text, "text_body": text_body, "text_dim": text_dim, "text_faint": text_faint,
        "brand": brand, "glow": glow, "glow_soft": glow_soft,
        "black": _hex(t["black"]), "white": _hex(t["white"]),
    }


# -----------------------------------------------------------------------------
# Applying.

class _Setter:
    """Sets theme properties, tolerating properties that do not exist in this build."""

    def __init__(self):
        self.missing = []

    def set(self, owner, attr, value, path=""):
        if owner is None or not hasattr(owner, attr):
            self.missing.append(path + attr)
            return
        current = getattr(owner, attr)
        try:
            if hasattr(current, "__len__") and not isinstance(current, str):
                value = tuple(value)[:len(current)]
                if len(value) < len(current):
                    value = (*value, 1.0)
            setattr(owner, attr, value)
        except Exception as ex:
            self.missing.append("{:s}{:s} ({:s})".format(path, attr, str(ex)))

    def many(self, owner, values, path=""):
        for attr, value in values.items():
            self.set(owner, attr, value, path)


def _widget(p, *, inner, inner_sel, outline, text=None, text_sel=None, item=None,
            roundness=1.0, outline_sel=None):
    return {
        "inner": _rgba(inner),
        "inner_sel": _rgba(inner_sel),
        "outline": _rgba(outline),
        "outline_sel": _rgba(outline_sel or outline),
        "item": _rgba(item or p["brand"]),
        "text": text or p["text_body"],
        "text_sel": text_sel or p["text"],
        "roundness": roundness,
        "show_shaded": False,  # Flat: no gradients.
        "shadetop": 0,
        "shadedown": 0,
    }


def _apply_user_interface(s, ui, p):
    pill = 1.0
    field = 0.5
    card = 0.35  # rounded-xl
    widgets = {
        # Pill buttons: `secondary` style at rest, `primary` style when active.
        "wcol_regular": _widget(p, inner=p["btn_secondary"], inner_sel=p["btn_primary"],
                                outline=p["btn_secondary_border"],
                                outline_sel=p["btn_primary_border"], roundness=pill),
        "wcol_tool": _widget(p, inner=p["btn_secondary"], inner_sel=p["btn_primary"],
                             outline=p["btn_secondary_border"],
                             outline_sel=p["btn_primary_border"], roundness=pill),
        "wcol_toolbar_item": _widget(p, inner=p["glass"], inner_sel=p["btn_primary"],
                                     outline=p["glass"], roundness=field),
        "wcol_toggle": _widget(p, inner=p["btn_secondary"], inner_sel=p["btn_primary"],
                               outline=p["btn_secondary_border"],
                               outline_sel=p["btn_primary_border"], roundness=pill),
        "wcol_radio": _widget(p, inner=p["btn_secondary"], inner_sel=p["btn_primary"],
                              outline=p["btn_secondary_border"],
                              outline_sel=p["btn_primary_border"], roundness=pill),
        "wcol_option": _widget(p, inner=p["btn_secondary"], inner_sel=p["btn_primary"],
                               outline=p["btn_secondary_border"], item=p["text"],
                               roundness=0.3),
        "wcol_text": _widget(p, inner=p["ink"], inner_sel=p["btn_emphasis"],
                             outline=p["border"], roundness=field),
        "wcol_num": _widget(p, inner=p["btn_secondary"], inner_sel=p["btn_emphasis"],
                            outline=p["btn_secondary_border"], item=p["text_dim"],
                            roundness=field),
        "wcol_numslider": _widget(p, inner=p["btn_secondary"], inner_sel=p["btn_emphasis"],
                                  outline=p["btn_secondary_border"],
                                  item=_over(p["brand"], 0.22, p["glass"]), roundness=field),
        "wcol_box": _widget(p, inner=p["glass_raised"], inner_sel=p["btn_emphasis"],
                            outline=p["border"], roundness=card),
        "wcol_menu": _widget(p, inner=p["btn_secondary"], inner_sel=p["btn_emphasis"],
                             outline=p["btn_secondary_border"], item=p["text_dim"],
                             roundness=pill),
        "wcol_pulldown": _widget(p, inner=p["bar"], inner_sel=p["btn_emphasis"],
                                 outline=p["bar"], roundness=pill),
        "wcol_menu_back": _widget(p, inner=p["glass_raised"], inner_sel=p["btn_emphasis"],
                                  outline=p["border"], roundness=card),
        "wcol_pie_menu": _widget(p, inner=p["glass_raised"], inner_sel=p["btn_primary"],
                                 outline=p["border"], roundness=pill),
        "wcol_tooltip": _widget(p, inner=p["glass_raised"], inner_sel=p["glass_raised"],
                                outline=p["border"], roundness=card),
        "wcol_menu_item": _widget(p, inner=p["glass_raised"], inner_sel=p["btn_emphasis"],
                                  outline=p["glass_raised"], roundness=field),
        "wcol_scroll": _widget(p, inner=p["glass"], inner_sel=p["btn_emphasis"],
                               outline=p["glass"], item=p["btn_secondary_border"],
                               roundness=pill),
        "wcol_progress": _widget(p, inner=p["btn_secondary"], inner_sel=p["btn_primary"],
                                 outline=p["border"], item=p["brand"], roundness=pill),
        "wcol_list_item": _widget(p, inner=p["glass"], inner_sel=p["btn_emphasis"],
                                  outline=p["glass"], roundness=field),
        "wcol_tab": _widget(p, inner=p["glass"], inner_sel=p["btn_emphasis"],
                            outline=p["border"], roundness=card),
    }
    for name, values in widgets.items():
        s.many(getattr(ui, name, None), values, "user_interface." + name + ".")

    state = getattr(ui, "wcol_state", None)
    # Animation state colors: keep Blender's semantics, desaturated to sit in the palette.
    s.many(state, {
        "inner_anim": _over((0.55, 0.75, 0.40), 0.55, p["glass"]),
        "inner_anim_sel": _over((0.55, 0.75, 0.40), 0.75, p["glass"]),
        "inner_key": _over((0.90, 0.80, 0.35), 0.55, p["glass"]),
        "inner_key_sel": _over((0.90, 0.80, 0.35), 0.75, p["glass"]),
        "inner_driven": _over(p["glow"], 0.55, p["glass"]),
        "inner_driven_sel": _over(p["glow"], 0.75, p["glass"]),
        "inner_overridden": _over((0.20, 0.65, 0.65), 0.55, p["glass"]),
        "inner_overridden_sel": _over((0.20, 0.65, 0.65), 0.75, p["glass"]),
        "inner_changed": _over((0.85, 0.50, 0.25), 0.55, p["glass"]),
        "inner_changed_sel": _over((0.85, 0.50, 0.25), 0.75, p["glass"]),
    }, "user_interface.wcol_state.")

    s.many(ui, {
        "menu_shadow_fac": 0.35,
        "menu_shadow_width": 12,
        "icon_alpha": 0.9,
        "icon_saturation": 0.35,
        "widget_text_cursor": p["brand"],
        "panel_roundness": card,
        "widget_emboss": _rgba(p["black"], 0.0),
        "editor_border": p["ink"],
        "editor_outline": _rgba(p["border_soft"]),
        "editor_outline_active": _rgba(p["btn_secondary_border"]),
        "transparent_checker_primary": p["glass"],
        "transparent_checker_secondary": p["btn_secondary"],
        "gizmo_primary": p["brand"],
        "gizmo_secondary": p["glow_soft"],
        "gizmo_view_align": p["text"],
        "gizmo_a": p["brand"],
        "gizmo_b": p["glow"],
    }, "user_interface.")


def _apply_space_generic(s, space, p, path, *, back=None, header=None):
    s.many(space, {
        "back": back or p["glass"],
        "header": _rgba(header or p["bar"]),
        "text": p["text_body"],
        "text_hi": p["text"],
    }, path)


def _apply_shared(s, theme, p):
    """Panels, sidebars, channel lists and animation colors shared by every editor."""
    s.many(theme.user_interface, {
        "panel_header": _rgba(p["glass_raised"]),
        "panel_title": p["text"],
        "panel_text": p["text_body"],
        "panel_back": _rgba(p["glass"]),
        "panel_sub_back": _rgba(p["black"], 0.18),
        "panel_outline": _rgba(p["border"]),
        "panel_active": _rgba(p["brand"], 0.6),
    }, "user_interface.")
    regions = getattr(theme, "regions", None)
    if regions is not None:
        s.many(getattr(regions, "sidebars", None),
               {"back": _rgba(p["glass"]), "tab_back": _rgba(p["ink"])}, "regions.sidebars.")
        s.many(getattr(regions, "channels", None),
               {"back": _rgba(p["glass"]), "text": p["text_body"], "text_selected": p["text"]},
               "regions.channels.")
        s.many(getattr(regions, "scrubbing", None),
               {"back": _rgba(p["bar"]), "text": p["text_body"],
                "time_marker": _rgba(p["text_dim"]), "time_marker_selected": _rgba(p["text"])},
               "regions.scrubbing.")
        s.many(getattr(regions, "asset_shelf", None),
               {"back": _rgba(p["glass"]), "header_back": _rgba(p["bar"])},
               "regions.asset_shelf.")
    anim = getattr(getattr(theme, "common", None), "anim", None)
    s.many(anim, {
        "playhead": _rgba(p["glow"]),
        "channels": _rgba(p["glass"]),
        "channels_sub": _rgba(p["glass_raised"]),
        "channel_group": _rgba(p["btn_secondary"]),
        "channel_group_active": _rgba(p["btn_emphasis"]),
        "channel": _rgba(p["glass"]),
        "channel_selected": _rgba(p["btn_emphasis"]),
    }, "common.anim.")


def _apply_sequencer(s, theme, p):
    seq = getattr(theme, "sequence_editor", None)
    if seq is None:
        s.missing.append("sequence_editor")
        return
    _apply_space_generic(s, getattr(seq, "space", None), p, "sequence_editor.space.",
                         back=p["ink"])

    # Strip colors: the site has no saturated accents, so strips are muted but still
    # distinguishable by type (editing requires telling clip types apart at a glance).
    def strip(rgb, amount=0.55):
        return _over(rgb, amount, p["glass"])

    s.many(seq, {
        "grid": _over(p["white"], 0.04, p["ink"]),
        "movie_strip": strip((0.30, 0.45, 0.70)),
        "movieclip_strip": strip((0.70, 0.35, 0.30)),
        "image_strip": strip((0.60, 0.40, 0.60)),
        "scene_strip": strip((0.35, 0.60, 0.55)),
        "audio_strip": strip((0.30, 0.60, 0.40)),
        "effect_strip": strip((0.55, 0.50, 0.75)),
        "transition_strip": strip((0.60, 0.45, 0.35)),
        "color_strip": strip((0.55, 0.55, 0.55)),
        "meta_strip": strip((0.45, 0.45, 0.50)),
        "mask_strip": strip((0.60, 0.35, 0.40)),
        "text_strip": strip((0.70, 0.60, 0.40)),
        "active_strip": p["text"],
        "selected_strip": p["brand"],
        "keyframe_border": _rgba(p["black"]),
        "keyframe_border_selected": _rgba(p["brand"]),
        "preview_back": p["ink"],
        "metadatabg": p["glass"],
        "metadatatext": p["text_body"],
        "row_alternate": _rgba(p["white"], 0.015),
        "selected_text": _rgba(p["brand"], 0.3),
        "text_strip_cursor": _rgba(p["text"]),
    }, "sequence_editor.")


_GENERIC_SPACES = (
    "properties", "file_browser", "outliner", "info", "preferences", "text_editor",
    "console", "dopesheet_editor", "graph_editor", "node_editor", "image_editor",
    "statusbar", "topbar", "spreadsheet", "clip_editor", "nla_editor",
)


def apply(context=None, *, report=False):
    """
    Apply the latest avenue theme to the current preferences.
    Returns a list of theme properties that do not exist in this build (empty when complete).
    """
    import bpy
    context = context or bpy.context
    theme = context.preferences.themes[0]
    p = _palette()
    s = _Setter()

    _apply_user_interface(s, theme.user_interface, p)
    _apply_shared(s, theme, p)
    _apply_sequencer(s, theme, p)
    for name in _GENERIC_SPACES:
        space_theme = getattr(theme, name, None)
        if space_theme is None:
            continue
        bar = name in {"topbar", "statusbar"}
        _apply_space_generic(s, getattr(space_theme, "space", None), p, name + ".space.",
                             back=p["bar"] if bar else None)

    if report and s.missing:
        print("latest avenue theme: {:d} properties not available in this build:".format(
            len(s.missing)))
        for path in s.missing:
            print("  " + path)
    return s.missing


def _marker_path():
    import bpy
    import os
    return os.path.join(bpy.utils.user_resource('CONFIG'), "vse_standalone_theme_applied")


def apply_if_first_run():
    """Apply the theme once per user configuration, so later user changes are kept."""
    import os
    marker = _marker_path()
    if os.path.exists(marker):
        return False
    apply()
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("1\n")
    except OSError:
        pass
    return True


def write_preset(filepath):
    """Write the current theme as an XML preset (shown under Preferences > Themes)."""
    import bpy
    import _rna_xml as rna_xml
    rna_xml.xml_file_write(bpy.context, filepath, bpy.types.USERPREF_MT_interface_theme_presets.preset_xml_map)
