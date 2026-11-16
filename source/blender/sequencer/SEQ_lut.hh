/* SPDX-FileCopyrightText: 2026 VSE Standalone contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup sequencer
 *
 * Look-Up Table (LUT) loading and evaluation for color grading.
 *
 * Supports the Adobe/Resolve `.cube` format (1D and 3D tables, `DOMAIN_MIN`/`DOMAIN_MAX`,
 * `LUT_1D_INPUT_RANGE`/`LUT_3D_INPUT_RANGE`). Parsed tables are cached by absolute path and
 * file modification time, so editing a LUT on disk is picked up automatically.
 */

#include <memory>
#include <string>

#include "BLI_math_vector_types.hh"
#include "BLI_string_ref.hh"
#include "BLI_vector.hh"

namespace blender::seq {

struct CubeLUT {
  std::string title;
  /** Number of entries per axis for 3D tables, or entries for 1D tables. */
  int size = 0;
  bool is_3d = false;
  float3 domain_min = float3(0.0f);
  float3 domain_max = float3(1.0f);
  /** For 3D: `size^3` entries, red varies fastest. For 1D: `size` entries. */
  Vector<float3> table;

  /** Evaluate the LUT for an RGB value (tetrahedral interpolation for 3D tables). */
  float3 evaluate(const float3 &rgb) const;
};

/**
 * Parse `.cube` text. Returns null and sets `r_error` on failure.
 */
std::unique_ptr<CubeLUT> lut_cube_parse(StringRef text, std::string &r_error);

/**
 * Load a LUT from an absolute file path, using a process-wide cache that is invalidated
 * when the file's modification time or size changes. Thread-safe.
 * Returns null and sets `r_error` on failure.
 */
std::shared_ptr<const CubeLUT> lut_load_cached(StringRefNull abs_filepath, std::string &r_error);

/** Free all cached LUTs (called on exit, and useful for tests). */
void lut_cache_clear();

}  // namespace blender::seq
