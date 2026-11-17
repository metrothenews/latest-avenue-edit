# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Image for the editor running with no screen, behind the sign-in gateway. Used both on
# Cloud Run (cloud/deploy.sh) and on a PC acting as the server (cloud/pc/).
#
# Build from the repository root, with the Linux build file next to this repository's
# README (latest-avenue-video-linux-x64.tar.xz, from the release):
#
#   docker build -f cloud/Dockerfile -t latest-avenue-edit .
#
# cloud/Dockerfile.dockerignore keeps everything else in the repository out of the build.
# Ubuntu 24.04 matches the system the Linux build is compiled on.
FROM ubuntu:24.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      tigervnc-standalone-server xkb-data x11-xkb-utils \
      libgl1 libegl1 libglx-mesa0 libgl1-mesa-dri \
      libx11-6 libxi6 libxfixes3 libxext6 libxxf86vm1 libxcursor1 libxrandr2 libxinerama1 \
      libxrender1 libxkbcommon0 libsm6 libdbus-1-3 libpulse0 libsndfile1 \
      python3 python3-aiohttp python3-jwt python3-cryptography ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The Linux build (a tar of the build's "bin" folder, named "app"). ADD unpacks it.
ADD latest-avenue-video-linux-x64.tar.xz /opt/
RUN mv /opt/app /opt/latest-avenue-video

# This checkout's Python over the build, so changes to the game view, tools and control
# server go out without recompiling (C/C++ changes need a new Linux build).
COPY scripts/modules/vse_standalone /tmp/overlay/modules/vse_standalone
COPY scripts/startup/vse_standalone_startup.py /tmp/overlay/startup/vse_standalone_startup.py
COPY scripts/startup/bl_app_templates_system/Video_Editing /tmp/overlay/startup/bl_app_templates_system/Video_Editing
RUN VERSION_DIR="$(ls -d /opt/latest-avenue-video/[0-9]*.[0-9]* | head -1)" \
    && cp -R /tmp/overlay/. "$VERSION_DIR/scripts/" && rm -rf /tmp/overlay

COPY cloud/start.sh cloud/editor.sh cloud/gateway.py /opt/cloud/

# Not root: the editor and gateway run as an ordinary user. The sed strips Windows line
# endings in case the repository was checked out with them.
RUN useradd --create-home --uid 1001 editor && mkdir -p /data && chown editor:editor /data \
    && sed -i 's/\r$//' /opt/cloud/*.sh /opt/cloud/*.py \
    && chmod +x /opt/cloud/start.sh /opt/cloud/editor.sh
USER editor

ENV VSE_APP_DIR=/opt/latest-avenue-video \
    VSE_DATA=/data \
    VSE_GEOMETRY=1600x900 \
    PORT=8080

EXPOSE 8080
CMD ["/opt/cloud/start.sh"]
