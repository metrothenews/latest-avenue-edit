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
# The language model runtime (llama.cpp's server), built for the processor. It runs the
# editing agent's model: an open base model plus LoRA adapters (cloud/agent.py).
FROM ubuntu:24.04 AS llama
ARG LLAMA_CPP_TAG=b12492
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      git cmake ninja-build g++ ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "$LLAMA_CPP_TAG" https://github.com/ggml-org/llama.cpp /src \
    && cmake -S /src -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF \
         -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DGGML_NATIVE=OFF -DBUILD_SHARED_LIBS=OFF \
    && ninja -C /build llama-server

FROM ubuntu:24.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      tigervnc-standalone-server xkb-data x11-xkb-utils \
      libgl1 libegl1 libglx-mesa0 libgl1-mesa-dri \
      libx11-6 libxi6 libxfixes3 libxext6 libxxf86vm1 libxcursor1 libxrandr2 libxinerama1 \
      libxrender1 libxkbcommon0 libsm6 libdbus-1-3 libpulse0 libsndfile1 \
      python3 python3-aiohttp python3-jwt python3-cryptography ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# The Linux build (a tar of the build's "bin" folder, named "app"). ADD unpacks it.
ADD latest-avenue-video-linux-x64.tar.xz /opt/
# A link, not a move: moving would store the whole build a second time in the image.
RUN ln -s /opt/app /opt/latest-avenue-video

# This checkout's Python over the build, so changes to the game view, tools and control
# server go out without recompiling (C/C++ changes need a new Linux build).
COPY scripts/modules/vse_standalone /tmp/overlay/modules/vse_standalone
COPY scripts/startup/vse_standalone_startup.py /tmp/overlay/startup/vse_standalone_startup.py
COPY scripts/startup/bl_app_templates_system/Video_Editing /tmp/overlay/startup/bl_app_templates_system/Video_Editing
RUN VERSION_DIR="$(ls -d /opt/latest-avenue-video/[0-9]*.[0-9]* | head -1)" \
    && cp -R /tmp/overlay/. "$VERSION_DIR/scripts/" && rm -rf /tmp/overlay \
    && ln -s "$VERSION_DIR/scripts/modules" /opt/vse-modules

# The assembly cut's own Python (transcription, matching, picture grouping, sequence export;
# doc/vse/assembly_workflow.md), next to the editor's. ASSEMBLY_CUDA=1 adds the CUDA libraries
# faster-whisper needs on an NVIDIA card (the PC install sets it when it finds one); without
# a card everything runs on the CPU. torch is the CPU build: the picture model is small.
ARG ASSEMBLY_CUDA=0
COPY tools/vse_assembly/requirements.txt tools/vse_assembly/requirements-image.txt /tmp/assembly/
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ffmpeg poppler-utils python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/assembly-env \
    && /opt/assembly-env/bin/pip install --no-cache-dir -r /tmp/assembly/requirements.txt \
    && if [ "$ASSEMBLY_CUDA" = "1" ]; then \
         /opt/assembly-env/bin/pip install --no-cache-dir nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"; fi \
    && ( /opt/assembly-env/bin/pip install --no-cache-dir \
           --index-url https://download.pytorch.org/whl/cpu torch torchvision \
         && /opt/assembly-env/bin/pip install --no-cache-dir -r /tmp/assembly/requirements-image.txt \
         || echo "picture model not installed: the colour signature groups pictures" ) \
    && rm -rf /tmp/assembly
ENV VSE_ASSEMBLY_PYTHON=/opt/assembly-env/bin/python \
    LD_LIBRARY_PATH=/opt/assembly-env/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/assembly-env/lib/python3.12/site-packages/nvidia/cudnn/lib \
    HF_HOME=/data/models/hf \
    HF_HUB_DISABLE_XET=1 \
    VSE_ASSEMBLY_LLM_URL=http://127.0.0.1:8793

# The Docker command line and its compose plugin, for the updater service only (it drives the
# PC's Docker through its socket; no Docker daemon is in the image).
ARG DOCKER_CLI_VERSION=29.9.3
ARG COMPOSE_VERSION=v5.7.1
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl \
    && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
       | tar -xz -C /usr/local/bin --strip-components=1 docker/docker \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
       "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY cloud/start.sh cloud/editor.sh cloud/gateway.py cloud/agent.py cloud/worker.py cloud/heartbeat.py \
     cloud/updater.py tools/vse_mcp/vse_mcp.py /opt/cloud/
# The PC's stack files travel inside the image: the updater takes them out (cloud/updater.py).
COPY cloud/pc/compose.yaml cloud/pc/compose.gpu.yaml cloud/pc/env.example /opt/cloud/pc/
COPY --from=llama /build/bin/llama-server /usr/local/bin/llama-server

# The job worker (cloud/pc/compose.yaml) runs from this same image:
#   /opt/assembly-env/bin/python /opt/cloud/worker.py   (PYTHONPATH=/opt/vse-modules)
# VSE_VERSION (e.g. the git commit) is what the public /status and the heartbeat report.
ARG VSE_VERSION=dev
LABEL org.opencontainers.image.version=$VSE_VERSION \
      org.opencontainers.image.source=https://github.com/metrothenews/latest-avenue-edit

# Not root: the editor and gateway run as an ordinary user. The sed strips Windows line
# endings in case the repository was checked out with them.
RUN useradd --create-home --uid 1001 editor && mkdir -p /data && chown editor:editor /data \
    && sed -i 's/\r$//' /opt/cloud/*.sh /opt/cloud/*.py \
    && echo "$VSE_VERSION" > /opt/cloud/VERSION \
    && chmod +x /opt/cloud/start.sh /opt/cloud/editor.sh
USER editor

ENV VSE_APP_DIR=/opt/latest-avenue-video \
    VSE_DATA=/data \
    VSE_GEOMETRY=1600x900 \
    PORT=8080

EXPOSE 8080
CMD ["/opt/cloud/start.sh"]
