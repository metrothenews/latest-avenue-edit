# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Image for the cloud editor. Built by cloud/deploy.sh from a staging folder that holds:
#   app/    the Linux build of the editor (the "bin" folder of a build, or a release tarball)
#   cloud/  this folder
# Ubuntu 24.04 matches the system the Linux build is compiled on.
FROM ubuntu:24.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      tigervnc-standalone-server xkb-data x11-xkb-utils \
      libgl1 libegl1 libglx-mesa0 libgl1-mesa-dri \
      libx11-6 libxi6 libxfixes3 libxext6 libxxf86vm1 libxcursor1 libxrandr2 libxinerama1 \
      libxrender1 libxkbcommon0 libsm6 libdbus-1-3 libpulse0 libsndfile1 \
      python3 python3-aiohttp python3-jwt python3-cryptography ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY app /opt/latest-avenue-video
COPY cloud /opt/cloud

# Not root: the editor and gateway run as an ordinary user.
RUN useradd --create-home --uid 1001 editor && mkdir -p /data && chown editor:editor /data \
    && chmod +x /opt/cloud/start.sh /opt/cloud/editor.sh
USER editor

ENV VSE_APP_DIR=/opt/latest-avenue-video \
    VSE_DATA=/data \
    VSE_GEOMETRY=1600x900 \
    VSE_NO_GAME_VIEW=0 \
    PORT=8080

EXPOSE 8080
CMD ["/opt/cloud/start.sh"]
