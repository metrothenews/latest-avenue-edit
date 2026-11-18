#!/bin/bash
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# The compose stack (cloud/pc/compose.yaml) on a Linux machine with Docker, with stand-ins for
# Google (sign-in certificates, Firestore, Drive): every service healthy, the public /status,
# the website's /edit flow with heavy steps on the worker and outputs in storage, the import
# queue, a worker killed mid-job, a backup, the heartbeat. Build both images first
# (cloud/Dockerfile.test's header), then from the repository root:
#
#   cloud/test_stack.sh latest-avenue-edit:candidate latest-avenue-edit:test
#
# MODELS_VOLUME=<docker volume> keeps the speech model between runs (mounted at /data/models).
# Leaves nothing behind (project "latest-avenue-stacktest", volumes removed at the end).
set -u
IMAGE=${1:-latest-avenue-edit:candidate}
TEST_IMAGE=${2:-latest-avenue-edit:test}
REPO=$(cd "$(dirname "$0")/.." && pwd)
W=$(mktemp -d); chmod 755 "$W"
P=latest-avenue-stacktest
failed=()
check() { if eval "$2"; then echo "ok    $1"; else echo "FAIL  $1"; failed+=("$1"); fi; }

# Test keys: a sign-in certificate the gateway trusts, a service-account key the fake checks.
mkdir -p "$W/certs" "$W/secrets" "$W/fake"
docker run --rm -i -u "$(id -u):$(id -g)" -v "$W:/w" "$IMAGE" python3 - <<'PY'
import datetime, json
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
pem = lambda k: k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
        .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
open("/w/key.pem", "wb").write(pem(key))
json.dump({"kid1": cert.public_bytes(serialization.Encoding.PEM).decode()}, open("/w/certs/certs.json", "w"))
sa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
json.dump({"type": "service_account", "client_email": "heartbeat@test.iam.gserviceaccount.com", "private_key": pem(sa).decode(),
           "private_key_id": "k1", "token_uri": "http://fakefs:9000/token"}, open("/w/secrets/heartbeat-sa.json", "w"))
open("/w/fake/sa.pub", "wb").write(sa.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
PY
chmod -R a+rX "$W"
cp "$REPO/tests/vse/stack/fake_firestore.py" "$W/fake/"
KEY=wk-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')
cat > "$W/test.env" <<ENV
FIREBASE_PROJECT_ID=test-project
ALLOWED_UIDS=owner123
ALLOWED_ORIGINS=http://localhost:5173
WORKER_KEY=$KEY
S3_ACCESS_KEY=stacktestkey
S3_SECRET_KEY=stacktest-$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')
EDIT_IMAGE=$IMAGE
TUNNEL_TOKEN=unused
GATEWAY_PORT=18099
SECRETS_DIR=$W/secrets
HEARTBEAT_SECONDS=3
FIRESTORE_URL=http://fakefs:9000
${REDIS_IMAGE:+REDIS_IMAGE=$REDIS_IMAGE}
${S3_IMAGE:+S3_IMAGE=$S3_IMAGE}
ENV
cat > "$W/compose.test.yaml" <<YAML
services:
  gateway:
    environment:
      GATEWAY_CERTS_FILE: /certs/certs.json
    volumes:
      - $W/certs:/certs:ro
  worker:
    environment:
      VSE_DRIVE_API: http://127.0.0.1:9100
${MODELS_VOLUME:+    volumes:
      - $MODELS_VOLUME:/data/models}
  fakefs:
    image: $IMAGE
    command: ["python3", "/fake/fake_firestore.py"]
    volumes:
      - $W/fake:/fake:ro
${MODELS_VOLUME:+volumes:
  $MODELS_VOLUME:
    external: true}
YAML
DC="docker compose -p $P --project-directory $REPO/cloud/pc --env-file $W/test.env -f $REPO/cloud/pc/compose.yaml -f $W/compose.test.yaml"
G=$P-gateway-1; WK=$P-worker-1

echo "=== Starting the stack"
$DC up -d --wait 2>&1 | grep -iv "^ *\(container\|network\|volume\).*\(created\|started\|healthy\|waiting\|running\|creating\|starting\)" | tail -20
check "every service healthy" '[ -z "$($DC ps --format "{{.Health}}" | grep -v -e healthy -e "^$")" ]'
STATUS=$(curl -s -i http://127.0.0.1:18099/status)
check "public /status, any origin" 'echo "$STATUS" | grep -qi "access-control-allow-origin: \*" && echo "$STATUS" | grep -q "\"online\": true"'

echo "=== The website's /edit flow, heavy steps on the worker"
docker run --rm -v ${P}_edit-data:/data "$TEST_IMAGE" python3 /opt/tests/vse/assembly/make_samples.py /data/media/shoot --tiny | tail -1
docker cp "$REPO/tests/vse/test_site_flow.py" $G:/tmp/ && docker cp "$W/key.pem" $G:/tmp/key.pem
check "site flow over the stack" 'docker exec $G python3 /tmp/test_site_flow.py http://127.0.0.1:8080 /tmp/key.pem shoot | tail -3 | grep -q "all passed"'
STORED=$(docker exec $WK python3 -c "
import sys; sys.path.insert(0, '/opt/vse-modules')
from vse_standalone.assembly import s3; print('\n'.join(o['key'] for o in s3.list_keys('users/owner123/')))")
check "outputs in storage (render, exports, editing script)" 'echo "$STORED" | grep -q "renders/site-test.mp4" && echo "$STORED" | grep -q "renders/site-test.fcpxml" && echo "$STORED" | grep -q "projects/site-test.edit.json"'
check "worker refuses calls without its key" '! docker exec $WK python3 -c "import urllib.request;urllib.request.urlopen(\"http://127.0.0.1:8795/status\")" 2>/dev/null'

echo "=== Import queue through gateway -> worker -> storage"
docker cp "$REPO/tests/vse/test_imports.py" $WK:/tmp/ && docker cp "$REPO/tests/vse/stack/fake_drive.py" $WK:/tmp/
docker exec -d $WK sh -c 'PYTHONPATH=/opt/vse-modules /opt/assembly-env/bin/python /tmp/fake_drive.py > /tmp/fd.log 2>&1'
sleep 2
docker cp "$REPO/tests/vse/stack/import_flow.py" $G:/tmp/
OUT=$(docker exec $G python3 /tmp/import_flow.py 2>&1); echo "$OUT" | sed 's/^/        /'
check "import done, 5 files" 'echo "$OUT" | grep "^import done" | grep -q "done.: 5"'
check "imported files and proxies in storage" 'docker exec $WK python3 -c "
import sys; sys.path.insert(0, \"/opt/vse-modules\")
from vse_standalone.assembly import s3; ks=[o[\"key\"] for o in s3.list_keys(\"users/owner123/media/drive/\")]
sys.exit(0 if any(k.endswith(\"A001_C001.mov\") for k in ks) and any(\".proxies/\" in k for k in ks) else 1)"'

echo "=== Worker killed mid-job"
docker cp "$REPO/tests/vse/stack/restart_check.py" $WK:/tmp/
IDS=$(docker exec $WK python3 /tmp/restart_check.py submit)
$DC kill worker >/dev/null 2>&1; $DC up -d --wait worker >/dev/null 2>&1
A=$(echo "$IDS" | python3 -c "import json,sys;print(json.load(sys.stdin)['a'])"); B=$(echo "$IDS" | python3 -c "import json,sys;print(json.load(sys.stdin)['b'])")
docker cp "$REPO/tests/vse/stack/restart_check.py" $WK:/tmp/
for i in $(seq 1 300); do docker exec $WK python3 /tmp/restart_check.py get $A | grep -q "done\|failed" && break; sleep 2; done
R=$(docker exec $WK python3 /tmp/restart_check.py get $A $B); echo "$R" | sed 's/^/        /'
check "the running job ran again after the restart" 'echo "$R" | grep -q "$A done attempts 2"'
check "the job that needed a token waits as needs_token" 'echo "$R" | grep -q "$B needs_token"'

echo "=== Backup and heartbeat"
BK=$(docker exec $WK python3 -c "import urllib.request,os;print(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8795/backup',method='POST',data=b'{}',headers={'X-Worker-Key':os.environ['WORKER_KEY']})).read().decode())")
check "backup stored" 'echo "$BK" | grep -q "backups/owner123/projects-"'
HB=$($DC exec -T fakefs python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:9000/').read().decode())")
check "heartbeat wrote editServer/status with a signed service-account token" 'echo "$HB" | grep -q "documents/editServer/status" && echo "$HB" | grep -q lastSeen'

$DC --profile tunnel down -v >/dev/null 2>&1; rm -rf "$W"
echo
if [ ${#failed[@]} -eq 0 ]; then echo "ALL PASSED"; else echo "FAILED: ${failed[*]}"; exit 1; fi
