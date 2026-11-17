@echo off
rem Double-click to set up or update this PC as the server for latest avenue video.
rem See cloud\pc\README.md. Options: run install.ps1 from PowerShell instead.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
