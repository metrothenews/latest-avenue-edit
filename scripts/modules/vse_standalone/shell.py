# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Opens the game UI (served by the editor's control server) in its own window.

The UI depends on CSS frosted-glass blur, so it is shown by a web engine. In order:

1. ``pywebview`` (native window: WKWebView on macOS, WebView2 on Windows, GTK/Qt on Linux),
   when installed in the bundled Python or the system Python.
2. A Chromium-based browser in app mode (Chrome, Edge, Chromium, Brave): own window,
   no tabs or address bar. Edge ships with Windows, so this always works there.
3. The default web browser.

Every option works on macOS, Windows and Linux; nothing here is platform specific beyond
where browsers are installed.
"""

import os
import shutil
import subprocess
import sys

_WEBVIEW_SCRIPT = r"""
import sys, webview
webview.create_window("latest avenue video", sys.argv[1], width=1280, height=860,
                      background_color="#0a0a0a")
webview.start()
"""

_BROWSERS = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ],
    "win32": [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ],
    "linux": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
              "microsoft-edge", "brave-browser"],
}


def _python_with_webview():
    for python in (sys.executable, shutil.which("python3"), shutil.which("python")):
        if not python:
            continue
        try:
            subprocess.run([python, "-c", "import webview"], check=True, timeout=20,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return python
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _chromium():
    platform = "linux" if sys.platform.startswith("linux") else sys.platform
    for candidate in _BROWSERS.get(platform, []):
        path = candidate if os.path.isabs(candidate) else shutil.which(candidate)
        if path and os.path.exists(path):
            return path
    return None


def open_ui(url, *, prefer=None):
    """Open ``url`` in a window. Returns which method was used."""
    detached = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)}
    if prefer in (None, "webview"):
        python = _python_with_webview()
        if python:
            subprocess.Popen([python, "-c", _WEBVIEW_SCRIPT, url], **detached)
            return "webview"
    if prefer in (None, "app"):
        browser = _chromium()
        if browser:
            import tempfile
            profile = os.path.join(tempfile.gettempdir(), "vse_standalone_ui_profile")
            subprocess.Popen([browser, "--app=" + url, "--user-data-dir=" + profile,
                              "--window-size=1280,860", "--no-first-run",
                              "--no-default-browser-check"], **detached)
            return "app"
    import webbrowser
    webbrowser.open(url)
    return "browser"
