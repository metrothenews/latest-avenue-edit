# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Build configuration for the standalone Video Sequence Editor.
#
# Everything that only serves 3D / rendering / simulation is switched off.
# Everything the Video Sequencer needs (video decode/encode, audio playback,
# image formats, color management, timeline interchange) is kept.
#
# Example usage:
#   cmake -C ../blender/build_files/cmake/config/vse_standalone.cmake ../blender

# ---------------------------------------------------------------------------
# KEEP: required for video editing.

set(WITH_CODEC_FFMPEG        ON  CACHE BOOL "" FORCE) # Video/audio decode + encode.
set(WITH_AUDASPACE           ON  CACHE BOOL "" FORCE) # Sound strips, mixing, scrubbing.
set(WITH_CODEC_SNDFILE       ON  CACHE BOOL "" FORCE) # WAV/FLAC/OGG audio import.
set(WITH_FFTW3               ON  CACHE BOOL "" FORCE) # Audio equalizer modifier.
set(WITH_RUBBERBAND          ON  CACHE BOOL "" FORCE) # Pitch-preserving time stretch.
set(WITH_OPENTIMELINEIO      ON  CACHE BOOL "" FORCE) # Timeline interchange (OTIO/EDL).
set(WITH_PUGIXML             ON  CACHE BOOL "" FORCE) # Used by OpenImageIO.
set(WITH_IMAGE_WEBP          ON  CACHE BOOL "" FORCE) # Image sequences / stills.
set(WITH_PYTHON              ON  CACHE BOOL "" FORCE) # UI layout is defined in Python.
set(WITH_PYTHON_INSTALL      ON  CACHE BOOL "" FORCE)
set(WITH_PYTHON_NUMPY        ON  CACHE BOOL "" FORCE) # Required by audaspace Python API.
set(WITH_INTERNATIONAL       OFF CACHE BOOL "" FORCE) # Can be re-enabled later.

# Audio output backends (per platform below).
set(WITH_OPENAL              ON  CACHE BOOL "" FORCE)
set(WITH_JACK                OFF CACHE BOOL "" FORCE)
set(WITH_SDL_AUDIO           OFF CACHE BOOL "" FORCE)

# ---------------------------------------------------------------------------
# REMOVE: 3D, rendering engines, simulation, 3D I/O.

set(WITH_CYCLES              OFF CACHE BOOL "" FORCE)
set(WITH_CYCLES_OSL          OFF CACHE BOOL "" FORCE)
set(WITH_CYCLES_PATH_GUIDING OFF CACHE BOOL "" FORCE)
set(WITH_CYCLES_DEVICE_OPTIX OFF CACHE BOOL "" FORCE)
set(WITH_CYCLES_DEVICE_CUDA  OFF CACHE BOOL "" FORCE)
set(WITH_CYCLES_DEVICE_HIP   OFF CACHE BOOL "" FORCE)
set(WITH_CYCLES_DEVICE_ONEAPI OFF CACHE BOOL "" FORCE)
set(WITH_EMBREE              OFF CACHE BOOL "" FORCE)
set(WITH_HYDRA               OFF CACHE BOOL "" FORCE)
set(WITH_USD                 OFF CACHE BOOL "" FORCE)
set(WITH_MATERIALX           OFF CACHE BOOL "" FORCE)
set(WITH_ALEMBIC             OFF CACHE BOOL "" FORCE)
set(WITH_FREESTYLE           OFF CACHE BOOL "" FORCE)
set(WITH_OPENIMAGEDENOISE    OFF CACHE BOOL "" FORCE)
set(WITH_OPENSUBDIV          OFF CACHE BOOL "" FORCE)
set(WITH_OPENVDB             OFF CACHE BOOL "" FORCE)
set(WITH_OPENVDB_BLOSC       OFF CACHE BOOL "" FORCE)
set(WITH_NANOVDB             OFF CACHE BOOL "" FORCE)
set(WITH_BULLET              OFF CACHE BOOL "" FORCE)
set(WITH_JOLT                OFF CACHE BOOL "" FORCE)
set(WITH_MOD_FLUID           OFF CACHE BOOL "" FORCE)
set(WITH_MOD_OCEANSIM        OFF CACHE BOOL "" FORCE)
set(WITH_MOD_REMESH          OFF CACHE BOOL "" FORCE)
set(WITH_QUADRIFLOW          OFF CACHE BOOL "" FORCE)
set(WITH_UV_SLIM             OFF CACHE BOOL "" FORCE)
set(WITH_GMP                 OFF CACHE BOOL "" FORCE)
set(WITH_MANIFOLD            OFF CACHE BOOL "" FORCE)
set(WITH_IK_ITASC            OFF CACHE BOOL "" FORCE)
set(WITH_IK_SOLVER           OFF CACHE BOOL "" FORCE)
set(WITH_LIBMV               OFF CACHE BOOL "" FORCE) # Motion tracking.
set(WITH_POTRACE             OFF CACHE BOOL "" FORCE)
set(WITH_HARU                OFF CACHE BOOL "" FORCE)
set(WITH_XR_OPENXR           OFF CACHE BOOL "" FORCE)
set(WITH_DRACO               OFF CACHE BOOL "" FORCE)
set(WITH_MESHOPTIMIZER       OFF CACHE BOOL "" FORCE)
set(WITH_KTX                 OFF CACHE BOOL "" FORCE)
set(WITH_IO_WAVEFRONT_OBJ    OFF CACHE BOOL "" FORCE)
set(WITH_IO_PLY              OFF CACHE BOOL "" FORCE)
set(WITH_IO_STL              OFF CACHE BOOL "" FORCE)
set(WITH_IO_FBX              OFF CACHE BOOL "" FORCE)
set(WITH_IO_GREASE_PENCIL    OFF CACHE BOOL "" FORCE)
set(WITH_IO_SPZ              OFF CACHE BOOL "" FORCE)
set(WITH_INPUT_NDOF          OFF CACHE BOOL "" FORCE) # 3D mice.
set(WITH_LLVM                OFF CACHE BOOL "" FORCE)
set(WITH_CLANG               OFF CACHE BOOL "" FORCE)
set(WITH_IMAGE_CINEON        ON  CACHE BOOL "" FORCE) # DPX is common in film work.
set(WITH_IMAGE_OPENJPEG      ON  CACHE BOOL "" FORCE) # Also required by FFmpeg.

# ---------------------------------------------------------------------------
# Build / platform.

set(WITH_BLENDER_THUMBNAILER OFF CACHE BOOL "" FORCE)
set(WITH_GTESTS              OFF CACHE BOOL "" FORCE)
set(WITH_TBB                 ON  CACHE BOOL "" FORCE)
set(WITH_TBB_MALLOC_PROXY    OFF CACHE BOOL "" FORCE)
set(WITH_INSTALL_PORTABLE    ON  CACHE BOOL "" FORCE)

# ---------------------------------------------------------------------------
# Platforms. Supported: Linux x64/arm64, macOS arm64, Windows x64/arm64, which are the
# platforms Blender ships precompiled libraries for (`lib/<platform>`, fetched with
# `build_files/vse/fetch_libs.py`). Intel macOS needs self-built libraries (see BUILDING_VSE.md).

if(APPLE)
  set(WITH_METAL_BACKEND     ON  CACHE BOOL "" FORCE) # OpenGL is deprecated on macOS.
  set(WITH_VULKAN_BACKEND    OFF CACHE BOOL "" FORCE)
  set(WITH_COREAUDIO         ON  CACHE BOOL "" FORCE)
elseif(WIN32)
  set(WITH_OPENGL_BACKEND    ON  CACHE BOOL "" FORCE)
  set(WITH_VULKAN_BACKEND    ON  CACHE BOOL "" FORCE) # Used on some GPUs/drivers.
  set(WITH_WASAPI            ON  CACHE BOOL "" FORCE)
  set(WITH_WINDOWS_BUNDLE_CRT ON CACHE BOOL "" FORCE)
else()
  set(WITH_OPENGL_BACKEND    ON  CACHE BOOL "" FORCE)
  set(WITH_VULKAN_BACKEND    OFF CACHE BOOL "" FORCE)
  set(WITH_GHOST_X11         ON  CACHE BOOL "" FORCE)
  set(WITH_GHOST_WAYLAND     ON  CACHE BOOL "" FORCE)
  set(WITH_PULSEAUDIO        ON  CACHE BOOL "" FORCE)
  set(WITH_PIPEWIRE          ON  CACHE BOOL "" FORCE)
endif()
