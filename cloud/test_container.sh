#!/bin/bash
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# The whole assembly pipeline, end to end, inside the editor's container, on a generated
# 124x70 pixel shoot: screenplay PDF -> sound sync -> transcription -> take matching and
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

run "Generate the 124x70 test shoot" python3 $T/assembly/make_samples.py "$SHOOT" --tiny
run "Assembly: script, sync, transcription, matching, grouping, edits, render, exports, music" \
    $PY $T/test_assembly.py --full --samples "$SHOOT"
run "Editor tools in Blender: sequence, takes, uploads, sync fix, thumbnails, song job" \
    $APP/blender -b --factory-startup --python $T/test_assembly_editor.py -- "$SHOOT"
run "Google Drive client (stand-in server)" python3 $T/test_drive.py
run "Drive import queue: pause/resume byte-exact, cancel, edit, dedupe, needs_token, restart" \
    python3 $T/test_imports.py
run "Editor basics (LUTs, ripple edits, export)" \
    $APP/blender -b --factory-startup --python $T/test_vse.py

# The website's flow over /api/rpc, against the real gateway and editor in this container,
# signed in with a test certificate the gateway is told to trust (never the real setup).
site_flow() {
    local W; W=$(mktemp -d)
    python3 - "$W" <<'PY'
import datetime, json, sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
w = sys.argv[1]
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
open(w + "/key.pem", "wb").write(key.private_bytes(serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
json.dump({"kid1": cert.public_bytes(serialization.Encoding.PEM).decode()}, open(w + "/certs.json", "w"))
PY
    mkdir -p /data/media && rm -rf /data/media/shoot && cp -r "$SHOOT" /data/media/shoot
    FIREBASE_PROJECT_ID=test-project ALLOWED_UIDS=owner123 ALLOWED_ORIGINS=http://localhost:5173 \
        GATEWAY_CERTS_FILE="$W/certs.json" PORT=8091 /opt/cloud/start.sh > "$W/gateway.log" 2>&1 &
    local gw=$!
    for i in $(seq 1 30); do python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8091/healthz')" 2>/dev/null && break; sleep 1; done
    python3 $T/test_site_flow.py http://127.0.0.1:8091 "$W/key.pem" shoot
    local code=$?
    kill $gw 2>/dev/null; pkill -f Xvnc 2>/dev/null; pkill -f latest-avenue-video 2>/dev/null
    [ $code -ne 0 ] && tail -30 "$W/gateway.log"
    return $code
}
run "The website's /edit flow over /api/rpc (gateway + editor in this container)" site_flow

echo
if [ ${#failed[@]} -eq 0 ]; then
    echo "ALL PASSED"
else
    echo "FAILED: ${failed[*]}"
    exit 1
fi
