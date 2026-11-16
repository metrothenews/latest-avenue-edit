/* SPDX-FileCopyrightText: 2026 VSE Standalone contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup sequencer
 */

#include <charconv>
#include <cstdlib>
#include <mutex>
#include <unordered_map>

#include "BLI_fileops.hh"
#include "BLI_math_base.hh"
#include "BLI_math_vector.hh"
#include "BLI_string_ref.hh"
#include "BLI_utildefines.h"

#include "MEM_guardedalloc.h"

#include "SEQ_lut.hh"

namespace blender::seq {

/* -------------------------------------------------------------------- */
/** \name Parsing
 * \{ */

static StringRef strip_ws(StringRef s)
{
  return s.trim(" \t\r\n");
}

static bool parse_floats(StringRef s, float *r_values, const int count)
{
  const char *p = s.data();
  const char *end = s.data() + s.size();
  for (int i = 0; i < count; i++) {
    while (p < end && (*p == ' ' || *p == '\t')) {
      p++;
    }
    if (p >= end) {
      return false;
    }
    /* `strtof` is locale dependent for the decimal separator; LUT files always use '.',
     * `std::from_chars` is locale independent. */
    const std::from_chars_result res = std::from_chars(p, end, r_values[i]);
    if (res.ec != std::errc()) {
      return false;
    }
    p = res.ptr;
  }
  while (p < end && (*p == ' ' || *p == '\t' || *p == '\r')) {
    p++;
  }
  return p == end;
}

std::unique_ptr<CubeLUT> lut_cube_parse(StringRef text, std::string &r_error)
{
  auto lut = std::make_unique<CubeLUT>();
  int line_number = 0;
  int64_t expected = -1;

  int64_t pos = 0;
  while (pos < text.size()) {
    int64_t eol = text.find('\n', pos);
    if (eol == StringRef::not_found) {
      eol = text.size();
    }
    StringRef line = strip_ws(text.substr(pos, eol - pos));
    pos = eol + 1;
    line_number++;

    if (line.is_empty() || line[0] == '#') {
      continue;
    }

    auto keyword_value = [&](StringRef keyword, StringRef &r_value) -> bool {
      if (line.startswith(keyword) && line.size() > keyword.size() &&
          ELEM(line[keyword.size()], ' ', '\t'))
      {
        r_value = strip_ws(line.substr(keyword.size()));
        return true;
      }
      return false;
    };

    StringRef value;
    if (keyword_value("TITLE", value)) {
      if (value.size() >= 2 && value[0] == '"' && value[value.size() - 1] == '"') {
        value = value.substr(1, value.size() - 2);
      }
      lut->title = value;
      continue;
    }
    if (keyword_value("LUT_3D_SIZE", value) || keyword_value("LUT_1D_SIZE", value)) {
      const bool is_3d = line.startswith("LUT_3D");
      float size_f;
      if (!parse_floats(value, &size_f, 1) || size_f < 2.0f || size_f > 4096.0f ||
          (is_3d && size_f > 256.0f))
      {
        r_error = "Invalid LUT size on line " + std::to_string(line_number);
        return nullptr;
      }
      if (lut->size != 0) {
        r_error = "Multiple LUT size declarations";
        return nullptr;
      }
      lut->size = int(size_f);
      lut->is_3d = is_3d;
      expected = is_3d ? int64_t(lut->size) * lut->size * lut->size : int64_t(lut->size);
      lut->table.reserve(expected);
      continue;
    }
    if (keyword_value("DOMAIN_MIN", value)) {
      if (!parse_floats(value, &lut->domain_min.x, 3)) {
        r_error = "Invalid DOMAIN_MIN on line " + std::to_string(line_number);
        return nullptr;
      }
      continue;
    }
    if (keyword_value("DOMAIN_MAX", value)) {
      if (!parse_floats(value, &lut->domain_max.x, 3)) {
        r_error = "Invalid DOMAIN_MAX on line " + std::to_string(line_number);
        return nullptr;
      }
      continue;
    }
    if (keyword_value("LUT_1D_INPUT_RANGE", value) || keyword_value("LUT_3D_INPUT_RANGE", value))
    {
      float range[2];
      if (!parse_floats(value, range, 2)) {
        r_error = "Invalid input range on line " + std::to_string(line_number);
        return nullptr;
      }
      lut->domain_min = float3(range[0]);
      lut->domain_max = float3(range[1]);
      continue;
    }

    /* Unknown keywords (vendor extensions) are skipped, data lines start with a number. */
    const char c = line[0];
    if (!((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.')) {
      continue;
    }
    if (lut->size == 0) {
      r_error = "LUT data before LUT_1D_SIZE / LUT_3D_SIZE";
      return nullptr;
    }
    float3 rgb;
    if (!parse_floats(line, &rgb.x, 3)) {
      r_error = "Invalid data on line " + std::to_string(line_number);
      return nullptr;
    }
    if (lut->table.size() >= expected) {
      r_error = "Too many entries in LUT";
      return nullptr;
    }
    lut->table.append(rgb);
  }

  if (lut->size == 0) {
    r_error = "Missing LUT_1D_SIZE / LUT_3D_SIZE";
    return nullptr;
  }
  if (lut->table.size() != expected) {
    r_error = "Expected " + std::to_string(expected) + " entries, found " +
              std::to_string(lut->table.size());
    return nullptr;
  }
  for (int i = 0; i < 3; i++) {
    if (!(lut->domain_max[i] > lut->domain_min[i])) {
      r_error = "DOMAIN_MAX must be greater than DOMAIN_MIN";
      return nullptr;
    }
  }
  return lut;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Evaluation
 * \{ */

float3 CubeLUT::evaluate(const float3 &rgb) const
{
  const float n = float(this->size - 1);
  /* Normalized lookup coordinate in `[0, size-1]`. */
  float3 coord;
  for (int i = 0; i < 3; i++) {
    const float t = (rgb[i] - domain_min[i]) / (domain_max[i] - domain_min[i]);
    coord[i] = math::clamp(t, 0.0f, 1.0f) * n;
  }

  if (!this->is_3d) {
    float3 out;
    for (int i = 0; i < 3; i++) {
      const int i0 = std::min(int(coord[i]), this->size - 2);
      const float f = coord[i] - float(i0);
      out[i] = math::interpolate(this->table[i0][i], this->table[i0 + 1][i], f);
    }
    return out;
  }

  const int s = this->size;
  const int r0 = std::min(int(coord.x), s - 2);
  const int g0 = std::min(int(coord.y), s - 2);
  const int b0 = std::min(int(coord.z), s - 2);
  const float fr = coord.x - float(r0);
  const float fg = coord.y - float(g0);
  const float fb = coord.z - float(b0);

  auto at = [&](int dr, int dg, int db) -> const float3 & {
    return this->table[(int64_t(b0 + db) * s + (g0 + dg)) * s + (r0 + dr)];
  };

  /* Tetrahedral interpolation: smoother and more accurate than trilinear for color LUTs,
   * and what most grading applications use. */
  const float3 &c000 = at(0, 0, 0);
  const float3 &c111 = at(1, 1, 1);
  if (fr > fg) {
    if (fg > fb) {
      return c000 + fr * (at(1, 0, 0) - c000) + fg * (at(1, 1, 0) - at(1, 0, 0)) +
             fb * (c111 - at(1, 1, 0));
    }
    if (fr > fb) {
      return c000 + fr * (at(1, 0, 0) - c000) + fb * (at(1, 0, 1) - at(1, 0, 0)) +
             fg * (c111 - at(1, 0, 1));
    }
    return c000 + fb * (at(0, 0, 1) - c000) + fr * (at(1, 0, 1) - at(0, 0, 1)) +
           fg * (c111 - at(1, 0, 1));
  }
  if (fb > fg) {
    return c000 + fb * (at(0, 0, 1) - c000) + fg * (at(0, 1, 1) - at(0, 0, 1)) +
           fr * (c111 - at(0, 1, 1));
  }
  if (fb > fr) {
    return c000 + fg * (at(0, 1, 0) - c000) + fb * (at(0, 1, 1) - at(0, 1, 0)) +
           fr * (c111 - at(0, 1, 1));
  }
  return c000 + fg * (at(0, 1, 0) - c000) + fr * (at(1, 1, 0) - at(0, 1, 0)) +
         fb * (c111 - at(1, 1, 0));
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Cache
 * \{ */

namespace {

struct CacheEntry {
  int64_t mtime = 0;
  int64_t size = 0;
  std::shared_ptr<const CubeLUT> lut;
  std::string error;
};

struct LUTCache {
  std::mutex mutex;
  std::unordered_map<std::string, CacheEntry> entries;
};

LUTCache &get_cache()
{
  static LUTCache cache;
  return cache;
}

}  // namespace

std::shared_ptr<const CubeLUT> lut_load_cached(StringRefNull abs_filepath, std::string &r_error)
{
  BLI_stat_t st;
  if (abs_filepath.is_empty() || BLI_stat(abs_filepath.c_str(), &st) != 0) {
    r_error = "LUT file not found";
    return nullptr;
  }
  const int64_t mtime = int64_t(st.st_mtime);
  const int64_t size = int64_t(st.st_size);

  LUTCache &cache = get_cache();
  std::lock_guard lock(cache.mutex);
  CacheEntry &entry = cache.entries[abs_filepath];
  if (entry.mtime == mtime && entry.size == size && (entry.lut || !entry.error.empty())) {
    r_error = entry.error;
    return entry.lut;
  }

  entry.mtime = mtime;
  entry.size = size;
  entry.lut.reset();
  entry.error.clear();

  if (size > 256 * 1024 * 1024) {
    entry.error = "LUT file is too large";
    r_error = entry.error;
    return nullptr;
  }

  /* Use BLI file reading: handles UTF-8 paths on Windows (unlike `std::ifstream`). */
  size_t text_size = 0;
  char *text_mem = static_cast<char *>(
      BLI_file_read_text_as_mem(abs_filepath.c_str(), 0, &text_size));
  if (text_mem == nullptr) {
    entry.error = "Unable to open LUT file";
    r_error = entry.error;
    return nullptr;
  }
  const std::string text(text_mem, text_size);
  MEM_delete(text_mem);

  std::string error;
  std::unique_ptr<CubeLUT> lut = lut_cube_parse(text, error);
  if (!lut) {
    entry.error = error.empty() ? "Unsupported LUT file" : error;
    r_error = entry.error;
    return nullptr;
  }
  entry.lut = std::move(lut);
  r_error.clear();
  return entry.lut;
}

void lut_cache_clear()
{
  LUTCache &cache = get_cache();
  std::lock_guard lock(cache.mutex);
  cache.entries.clear();
}

/** \} */

}  // namespace blender::seq
