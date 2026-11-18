#!/bin/bash
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# The whole assembly pipeline, end to end, inside the editor's container, on a generated
# 12x12 pixel shoot: screenplay PDF -> sound sync -> transcription -> take matching and
# picture grouping -> editing script -> Blender sequence -> ffmpeg render + Resolve / Premiere /
# Final Cut exports, plus the music-video path and the website's tools. Uses the NVIDIA card
# when the container has one (docker run --gpus all), the processor otherwise.
#
# Needs the internet once, for the speech model (about 140 MB). Keep it between runs with
#   -v la-models:/data/models
set -u
APP=/opt/latest-avenue-video
VERSION_DIR="$(ls -d $APP/[0-9]*.[0-9]* | head -1)"
export PYTHONPATH="$VERSION_DIR/scripts/modules"
PY=/opt/assembly-env/bin/python
SHOOT=$(mktemp -d)/shoot
T=/opt/tests/vse
failed=()

run() {
    local name="$1"; shift
    echo
    echo "=== $name"
    if "$@"; then echo "=== $name: passed"; else echo "=== $name: FAILED"; failed+=("$name"); fi
}

echo "=== Machine"
$PY - <<'PY'
import ctranslate2, os
n = ctranslate2.get_cuda_device_count()
print("NVIDIA cards seen by the speech model:", n, "(using the GPU)" if n else "(using the processor)")
try:
    import torch
    print("picture model: MobileCLIP2-S0, torch", torch.__version__, "cuda" if torch.cuda.is_available() else "cpu")
except ImportError:
    print("picture model: colour signature (torch not installed)")
PY

run "Generate the 12x12 test shoot" python3 $T/assembly/make_samples.py "$SHOOT" --tiny
run "Assembly: script, sync, transcription, matching, grouping, edits, render, exports, music" \
    $PY $T/test_assembly.py --full --samples "$SHOOT"
run "Editor tools in Blender: sequence, takes, uploads, sync fix, thumbnails, song job" \
    $APP/blender -b --factory-startup --python $T/test_assembly_editor.py -- "$SHOOT"
run "Google Drive client (stand-in server)" python3 $T/test_drive.py
run "Editor basics (LUTs, ripple edits, export)" \
    $APP/blender -b --factory-startup --python $T/test_vse.py

echo
if [ ${#failed[@]} -eq 0 ]; then
    echo "ALL PASSED"
else
    echo "FAILED: ${failed[*]}"
    exit 1
fi
