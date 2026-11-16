/* SPDX-FileCopyrightText: 2026 VSE Standalone contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup sequencer
 *
 * LUT color grading modifier: applies a `.cube` Look-Up Table to the strip image.
 */

#include "BLI_math_vector.hh"
#include "BLI_path_utils.hh"
#include "BLI_string.hh"

#include "BLT_translation.hh"

#include "BKE_main.hh"

#include "DNA_scene_types.h"
#include "DNA_sequence_types.h"

#include "PRF_profile.hh"

#include "SEQ_lut.hh"
#include "SEQ_modifier.hh"
#include "SEQ_render.hh"

#include "UI_interface.hh"
#include "UI_interface_layout.hh"
#include "UI_resources.hh"

#include "RNA_access.hh"

#include "modifier.hh"
#include "render.hh"

namespace blender::seq {

static void lut_resolve_path(const Main *bmain,
                             const Scene *scene,
                             const LUTModifierData *lmd,
                             char r_path[FILE_MAX])
{
  BLI_strncpy(r_path, lmd->filepath, FILE_MAX);
  if (bmain && scene) {
    BLI_path_abs(r_path, ID_BLEND_PATH(bmain, &scene->id));
  }
}

struct LUTApplyOp {
  const CubeLUT *lut;
  float intensity;
  bool clamp_output;

  template<typename ImageT, typename MaskSampler>
  void apply(ImageT *image, MaskSampler &mask, int image_x, IndexRange y_range)
  {
    image += y_range.first() * image_x * 4;
    for (int64_t y : y_range) {
      mask.begin_row(y);
      for ([[maybe_unused]] int64_t x : IndexRange(image_x)) {
        /* LUTs are defined on straight (un-premultiplied) color. Byte images are stored
         * straight, float images are premultiplied. */
        float4 input = load_pixel_raw(image);
        float4 straight = input;
        if constexpr (std::is_same_v<ImageT, float>) {
          if (input.w > 0.0f && input.w != 1.0f) {
            straight = float4(input.xyz() / input.w, input.w);
          }
        }

        float3 graded = this->lut->evaluate(straight.xyz());
        if (this->clamp_output) {
          graded = math::clamp(graded, 0.0f, 1.0f);
        }
        graded = math::interpolate(straight.xyz(), graded, this->intensity);

        float4 result(graded, input.w);
        if constexpr (std::is_same_v<ImageT, float>) {
          if (input.w != 1.0f) {
            result = float4(graded * input.w, input.w);
          }
        }

        mask.apply_mask(input, result);
        store_pixel_raw(result, image);
        image += 4;
      }
    }
  }
};

static void lut_init_data(StripModifierData *smd)
{
  LUTModifierData *lmd = reinterpret_cast<LUTModifierData *>(smd);
  lmd->filepath[0] = '\0';
  lmd->intensity = 1.0f;
  lmd->flag = 0;
}

static void lut_apply(ModifierApplyContext &context, StripModifierData *smd)
{
  PRF_scope_with_name("SeqModLUT", ProfileCategory::Draw);
  const LUTModifierData *lmd = reinterpret_cast<const LUTModifierData *>(smd);
  if (lmd->filepath[0] == '\0' || lmd->intensity <= 0.0f) {
    return;
  }

  char path[FILE_MAX];
  lut_resolve_path(context.render_data.bmain, context.render_data.scene, lmd, path);
  std::string error;
  std::shared_ptr<const CubeLUT> lut = lut_load_cached(path, error);
  if (!lut) {
    /* Error is displayed in the modifier panel; render the strip unchanged. */
    return;
  }

  ensure_ibuf_is_sequencer_space(context.render_data.scene, context.result.image, false);
  ImBuf *mask = modifier_render_mask_input(context, *smd);

  LUTApplyOp op;
  op.lut = lut.get();
  op.intensity = math::clamp(lmd->intensity, 0.0f, 1.0f);
  op.clamp_output = (lmd->flag & SEQ_LUT_CLAMP_OUTPUT) != 0;

  apply_modifier_op(op, context.result.image, mask, context.transform);
  if (mask != nullptr) {
    IMB_freeImBuf(mask);
  }
}

static void lut_panel_draw(const bContext *C, Panel *panel)
{
  ui::Layout &layout = *panel->layout;
  PointerRNA *ptr = ui::panel_custom_data_get(panel);

  layout.use_property_split_set(true);

  layout.prop(ptr, "filepath", UI_ITEM_NONE, std::nullopt, ICON_NONE);
  layout.prop(ptr, "intensity", UI_ITEM_R_SLIDER, std::nullopt, ICON_NONE);
  layout.prop(ptr, "use_clamp", UI_ITEM_NONE, std::nullopt, ICON_NONE);

  /* Show LUT status so problems are visible without consulting the console. */
  const LUTModifierData *lmd = static_cast<const LUTModifierData *>(ptr->data);
  if (lmd->filepath[0] != '\0') {
    const Scene *scene = reinterpret_cast<const Scene *>(ptr->owner_id);
    char path[FILE_MAX];
    lut_resolve_path(CTX_data_main(C), scene, lmd, path);
    std::string error;
    std::shared_ptr<const CubeLUT> lut = lut_load_cached(path, error);
    if (lut) {
      std::string info = lut->is_3d ? "3D LUT, " : "1D LUT, ";
      info += std::to_string(lut->size);
      if (!lut->title.empty()) {
        info += ": " + lut->title;
      }
      layout.label(info, ICON_INFO);
    }
    else {
      layout.label(error, ICON_ERROR);
    }
  }

  if (ui::Layout *mask_input_layout = layout.panel_prop(
          C, ptr, "open_mask_input_panel", IFACE_("Mask Input")))
  {
    draw_mask_input_type_settings(C, *mask_input_layout, ptr);
  }
}

static void lut_register(ARegionType *region_type)
{
  modifier_panel_register(region_type, eSeqModifierType_LUT, lut_panel_draw);
}

StripModifierTypeInfo seqModifierType_LUT = {
    /*idname*/ "LUT",
    /*name*/ CTX_N_(BLT_I18NCONTEXT_ID_SEQUENCE, "LUT"),
    /*struct_name*/ "LUTModifierData",
    /*struct_size*/ sizeof(LUTModifierData),
    /*init_data*/ lut_init_data,
    /*free_data*/ nullptr,
    /*copy_data*/ nullptr,
    /*apply*/ lut_apply,
    /*panel_register*/ lut_register,
    /*blend_write*/ nullptr,
    /*blend_read*/ nullptr,
};

};  // namespace blender::seq
