# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Starts the application layer: the local control server (so the game UI, MCP clients and
scripts can drive the editor) and, on first run, the latest avenue theme.

Environment variables:

``VSE_NO_SERVER=1``  do not start the control server.
``VSE_PORT=<n>``     port for the control server (default 8792).
"""

import os

import bpy
from bpy.app.handlers import persistent


@persistent
def _on_factory_preferences(*_args):
    from vse_standalone import theme
    theme.apply()


def _deferred_start():
    if os.environ.get("VSE_NO_SERVER") != "1":
        from vse_standalone import server
        try:
            server.start()
        except OSError as ex:
            print("VSE control server not started: {:s}".format(str(ex)))
    try:
        from vse_standalone import theme
        theme.apply_if_first_run()
    except Exception as ex:
        print("latest avenue theme not applied: {:s}".format(str(ex)))
    try:
        from vse_standalone import layout
        layout.apply_saved()
    except Exception as ex:
        print("Pane arrangement not applied: {:s}".format(str(ex)))
    if os.environ.get("VSE_NO_GAME_VIEW") != "1":
        try:
            from vse_standalone import game_view
            game_view.start()
        except Exception as ex:
            print("Game view not started: {:s}".format(str(ex)))
    return None


class VSE_OT_open_game_ui(bpy.types.Operator):
    """Open the web version of the Color and Crop passes in a separate window"""
    bl_idname = "vse.open_game_ui"
    bl_label = "Open Web UI"

    def execute(self, context):
        from vse_standalone import server, shell
        url = server.start()
        method = shell.open_ui(url)
        self.report({'INFO'}, "Opened {:s} ({:s})".format(url, method))
        return {'FINISHED'}


class VSE_OT_control_server(bpy.types.Operator):
    """Start or stop the local control server used by the Color Pass window and AI agents"""
    bl_idname = "vse.control_server"
    bl_label = "Control Server"

    action: bpy.props.EnumProperty(items=(('START', "Start", ""), ('STOP', "Stop", "")))

    def execute(self, context):
        from vse_standalone import server
        if self.action == 'START':
            server.start()
        else:
            server.stop()
        return {'FINISHED'}


class SEQUENCER_PT_vse_automation(bpy.types.Panel):
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Automation"
    bl_label = "Automation"

    def draw(self, context):
        import time
        from vse_standalone import server
        layout = self.layout
        if server.is_running():
            layout.label(text=server.url(), icon='LINKED')
            layout.operator("vse.control_server", text="Stop Server").action = 'STOP'
        else:
            layout.label(text="Server stopped", icon='UNLINKED')
            layout.operator("vse.control_server", text="Start Server").action = 'START'
        layout.operator("vse.open_game_ui", icon='WINDOW')
        layout.separator()
        row = layout.row(align=True)
        row.prop(context.window_manager, "vse_zoom", text="Zoom")
        row.operator("vse.zoom_all_clips", text="Zoom All Clips")
        log = server.recent_log()[-12:]
        if log:
            box = layout.box()
            box.label(text="Recent agent calls")
            col = box.column(align=True)
            for entry in reversed(log):
                col.label(text="{:s}  {:s}".format(time.strftime("%H:%M:%S", time.localtime(entry["time"])),
                                                   entry["method"]),
                          icon='CHECKMARK' if entry["ok"] else 'ERROR')
            layout.label(text="Undo with Ctrl+Z (Edit > Undo History)", icon='INFO')


class VSE_OT_zoom_all_clips(bpy.types.Operator):
    """Set the zoom of every video clip in the timeline (the Crop pass)"""
    bl_idname = "vse.zoom_all_clips"
    bl_label = "Zoom All Clips"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from vse_standalone import api, color_pass
        try:
            r = color_pass.zoom_all_clips(context.window_manager.vse_zoom)
        except api.APIError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, "{:d} changed, {:d} already at {:.2f}".format(
            r["changed"], r["unchanged"], r["zoom"]))
        return {'FINISHED'}


def _draw_header_button(self, context):
    from vse_standalone import game_view
    if context.space_data.view_type == 'PREVIEW':
        self.layout.operator("vse.game_view", text="Game View", icon='COLOR',
                             depress=game_view.G["active"])


def _game_view_class():
    from vse_standalone import game_view
    return game_view.VSE_OT_game_view


_classes = (VSE_OT_open_game_ui, VSE_OT_control_server, VSE_OT_zoom_all_clips,
            SEQUENCER_PT_vse_automation)


def register():
    bpy.types.WindowManager.vse_zoom = bpy.props.FloatProperty(
        name="Zoom", default=1.34, min=0.1, max=10.0, precision=2)
    for cls in _classes:
        bpy.utils.register_class(cls)
    if not bpy.app.background:
        bpy.utils.register_class(_game_view_class())
    from vse_standalone import layout
    for cls in layout.classes:
        bpy.utils.register_class(cls)
    bpy.types.SEQUENCER_MT_view.append(layout.draw_view_menu)
    bpy.types.SEQUENCER_HT_header.append(_draw_header_button)
    if bpy.app.background:
        # Background sessions start the server explicitly with `server.serve_forever()`.
        return
    bpy.app.handlers.load_factory_preferences_post.append(_on_factory_preferences)
    bpy.app.timers.register(_deferred_start, first_interval=0.5, persistent=True)


def unregister():
    bpy.types.SEQUENCER_HT_header.remove(_draw_header_button)
    from vse_standalone import layout
    bpy.types.SEQUENCER_MT_view.remove(layout.draw_view_menu)
    for cls in reversed(layout.classes):
        bpy.utils.unregister_class(cls)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    if not bpy.app.background:
        from vse_standalone import game_view
        game_view.stop()
        bpy.utils.unregister_class(_game_view_class())
    del bpy.types.WindowManager.vse_zoom
    if _on_factory_preferences in bpy.app.handlers.load_factory_preferences_post:
        bpy.app.handlers.load_factory_preferences_post.remove(_on_factory_preferences)
    from vse_standalone import server
    server.stop()
