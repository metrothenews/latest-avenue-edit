# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Limits Blender's dependency builder (`build_files/build_environment`) to the libraries the
# video editor uses. Enabled with `-DVSE_DEPS_ONLY=ON`; used for platforms where Blender
# publishes no precompiled libraries (Intel macOS, see `build_files/vse/build_libs_macos_x64.sh`).
#
# Everything skipped here only serves features that `build_files/cmake/config/vse_standalone.cmake`
# switches off (3D, rendering, simulation, 3D I/O, GPU compute), plus libraries for other
# platforms. Keep the two files in step.

# `build_files/build_environment/cmake/<name>.cmake` files that are not included.
set(VSE_DEPS_SKIP
  alembic
  blosc
  ceres
  draco
  embree
  gmp
  haru
  ispc
  jolt
  ktx
  llvm
  manifold
  materialx
  meshoptimizer
  nanobind
  openimagedenoise
  openpgl
  opensubdiv
  openvdb
  osl
  potrace
  tracy
  usd
  xr_openxr_sdk
)

# Source archives (names from `versions.cmake`) that are not downloaded.
set(VSE_DEPS_SKIP_DOWNLOAD
  ALEMBIC BLOSC CERES DRACO EMBREE GMP HARU ISPC JOLT KTX LLVM MANIFOLD MATERIALX
  MESHOPTIMIZER NANOBIND OIDN OPENPGL OPENSUBDIV OPENVDB OSL POTRACE TRACY USD XR_OPENXR_SDK
  # Other platforms or GPU vendors.
  AMF DPCPP DPCPP_SPIRV_HEADERS EMHASH FFNVCODEC FLEX FLEXBISON GMMLIB HIPRT HWLOC ICDLOADER
  IGC IGC_LLVM IGC_OPENCL_CLANG IGC_SPIRV_HEADERS IGC_SPIRV_TOOLS IGC_SPIRV_TRANSLATOR
  IGC_VCINTRINSICS LEVEL_ZERO OCLOC OPENCLHEADERS PARALLEL_HASHMAP SPNAV SSE2NEON
  UNIFIED_MEMORY_FRAMEWORK VCINTRINSICS WAYLAND WAYLAND_WESTON WL_PROTOCOLS
)

# Include a dependency file unless it is in VSE_DEPS_SKIP.
macro(vse_dep_include file)
  get_filename_component(_vse_dep_name "${file}" NAME_WE)
  if(VSE_DEPS_ONLY AND _vse_dep_name IN_LIST VSE_DEPS_SKIP)
    message(STATUS "VSE_DEPS_ONLY: skipping ${_vse_dep_name}")
  else()
    include(${file})
  endif()
  unset(_vse_dep_name)
endmacro()
