#!/bin/bash
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Deploy (or update) the cloud editor on Google Cloud Run. Safe to run again: every step
# checks what exists. Needs the gcloud CLI signed in to an account that owns the project,
# and the gh CLI signed in to an account that can see this repo (to fetch the Linux build).
#
#   cloud/deploy.sh                 deploy with the defaults below
#   OWNER_UIDS=a,b cloud/deploy.sh  allow more than one Firebase user
#   APP_DIR=/path/to/build/bin cloud/deploy.sh   use a local Linux build instead of the release
#
# What it creates, all in one project and region:
#   - a Cloud Run service (scales to zero; at most one instance)
#   - a Cloud Storage bucket for projects, media and renders, mounted at /data
#   - an Artifact Registry repository holding the image
#
# What it costs: nothing while nobody is connected (apart from pennies for stored files and
# the image). While connected, the instance size below at Cloud Run's per-second rates.

set -euo pipefail

PROJECT="${PROJECT:-latest-avenue-app}"            # the site's Firebase / Google Cloud project
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-latest-avenue-edit}"
BUCKET="${BUCKET:-${PROJECT}-edit-data}"
REPOSITORY="${REPOSITORY:-edit}"
# Firebase user IDs allowed to use the editor: the site's `ownerUid` (src/data/firebase.js in
# the latest-avenue repo). Not a secret; it only names who is allowed.
OWNER_UIDS="${OWNER_UIDS:-cUUEuJ7NRCZVuMNhSxOr7yzA8DX2}"
ORIGINS="${ORIGINS:-https://latestavenue.com,https://www.latestavenue.com,https://latest-avenue-app.web.app}"
CPU="${CPU:-4}"
MEMORY="${MEMORY:-4Gi}"
GEOMETRY="${GEOMETRY:-1600x900}"
RELEASE_FILE="latest-avenue-video-linux-x64.tar.xz"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

command -v gcloud >/dev/null || { echo "Install the gcloud CLI first: https://cloud.google.com/sdk/docs/install"; exit 1; }
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . || { echo "Sign in first: gcloud auth login"; exit 1; }

step "Staging the editor"
if [ -n "${APP_DIR:-}" ]; then
  cp -R "$APP_DIR" "$STAGE/app"
else
  command -v gh >/dev/null || { echo "Install the gh CLI, or pass APP_DIR=<a Linux build's bin folder>"; exit 1; }
  gh release download --repo metrothenews/latest-avenue-edit --pattern "$RELEASE_FILE" --dir "$STAGE"
  tar -xJf "$STAGE/$RELEASE_FILE" -C "$STAGE"
  rm "$STAGE/$RELEASE_FILE"
fi
[ -x "$STAGE/app/blender" ] || { echo "No Linux build found in the staging folder"; exit 1; }
# Lay this checkout's Python over the build, so changes to the game view, tools and control
# server deploy without recompiling (C/C++ changes need a new Linux build).
VERSION_DIR="$(find "$STAGE/app" -maxdepth 1 -type d -name '[0-9]*.[0-9]*' | head -1)"
cp -R "$REPO/scripts/modules/vse_standalone/." "$VERSION_DIR/scripts/modules/vse_standalone/"
cp "$REPO/scripts/startup/vse_standalone_startup.py" "$VERSION_DIR/scripts/startup/"
cp -R "$REPO/scripts/startup/bl_app_templates_system/Video_Editing/." \
  "$VERSION_DIR/scripts/startup/bl_app_templates_system/Video_Editing/"
mkdir -p "$STAGE/cloud"
cp "$REPO/cloud/Dockerfile" "$REPO/cloud/start.sh" "$REPO/cloud/editor.sh" "$REPO/cloud/gateway.py" "$STAGE/cloud/"
git -C "$REPO" rev-parse --short HEAD > "$STAGE/cloud/VERSION"

step "Google Cloud project: $PROJECT"
gcloud config set project "$PROJECT" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com

step "Image"
gcloud artifacts repositories describe "$REPOSITORY" --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPOSITORY" --repository-format docker --location "$REGION" \
    --description "latest avenue video, cloud editor"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPOSITORY/editor:$(cat "$STAGE/cloud/VERSION")"
cat > "$STAGE/cloudbuild.yaml" <<EOF
steps:
- name: gcr.io/cloud-builders/docker
  args: [build, -f, cloud/Dockerfile, -t, $IMAGE, .]
images: [$IMAGE]
EOF
gcloud builds submit "$STAGE" --config "$STAGE/cloudbuild.yaml" --region "$REGION"

step "Storage bucket: $BUCKET"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access \
    --public-access-prevention

step "Cloud Run service: $SERVICE"
# --allow-unauthenticated: browsers can't present Google Cloud credentials on a WebSocket, so
# the service is reachable and the gateway inside checks the Firebase sign-in on every
# request (cloud/gateway.py). --max-instances 1 caps what can ever be running.
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" \
  --allow-unauthenticated \
  --execution-environment gen2 \
  --cpu "$CPU" --memory "$MEMORY" --cpu-boost \
  --min-instances 0 --max-instances 1 --concurrency 50 \
  --timeout 3600 --session-affinity \
  --set-env-vars "^|^FIREBASE_PROJECT_ID=$PROJECT|ALLOWED_UIDS=$OWNER_UIDS|ALLOWED_ORIGINS=$ORIGINS|VSE_GEOMETRY=$GEOMETRY" \
  --add-volume "name=data,type=cloud-storage,bucket=$BUCKET,mount-options=uid=1001;gid=1001" \
  --add-volume-mount "volume=data,mount-path=/data"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
printf '\n\033[1mDeployed.\033[0m Gateway address:\n\n  %s\n\n' "$URL"
echo "Put that address in editServiceUrl in src/data/edit.js of the latest-avenue repo, then"
echo "build and deploy the site. The editor is then at https://latestavenue.com/edit (owner sign-in)."
echo "Footage goes in gs://$BUCKET/media (it appears in the editor at /data/media)."
