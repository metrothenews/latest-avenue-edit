#!/bin/bash
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Runs the editor: a virtual display with a VNC server (127.0.0.1 only) and the editor on
# that display with software rendering. Started by the gateway after the first request that
# passes sign-in, so traffic from anyone else never starts it.

set -u

APP="${VSE_APP_DIR:-/opt/latest-avenue-video}"
export VSE_DATA="${VSE_DATA:-/data}"
GEOMETRY="${VSE_GEOMETRY:-1600x900}"
WIDTH="${GEOMETRY%x*}"; HEIGHT="${GEOMETRY#*x}"
export VNC_PORT="${VNC_PORT:-5901}"
export DISPLAY=":1"
# No GPU in the cloud: render on the CPU. OpenGL is asked for explicitly so the app does not
# try Vulkan first and show a notice about falling back.
export LIBGL_ALWAYS_SOFTWARE=1
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-/tmp/config}"
mkdir -p "$XDG_RUNTIME_DIR" "$XDG_CONFIG_HOME" "$VSE_DATA/projects" "$VSE_DATA/media" "$VSE_DATA/renders" 2>/dev/null
chmod 700 "$XDG_RUNTIME_DIR"

# The VNC server has no password of its own: it only listens on 127.0.0.1 and the gateway
# authenticates every connection before relaying to it.
Xvnc "$DISPLAY" -geometry "$GEOMETRY" -depth 24 -localhost -rfbport "$VNC_PORT" \
  -SecurityTypes None -AlwaysShared -AcceptSetDesktopSize=0 -ac >/tmp/xvnc.log 2>&1 &

for _ in $(seq 1 50); do
  [ -e "/tmp/.X11-unix/X1" ] && break
  sleep 0.1
done

run_editor() {
  # Restart the editor if it is quit from inside, reopening the last autosave.
  while true; do
    PROJECT=()
    [ -f "$VSE_DATA/projects/autosave.blend" ] && PROJECT=("$VSE_DATA/projects/autosave.blend")
    "$APP/blender" --gpu-backend opengl --window-geometry 0 0 "$WIDTH" "$HEIGHT" \
      --no-window-focus "${PROJECT[@]}" >>/tmp/editor.log 2>&1
    echo "editor exited ($?), restarting" >>/tmp/editor.log
    sleep 1
  done
}
run_editor

