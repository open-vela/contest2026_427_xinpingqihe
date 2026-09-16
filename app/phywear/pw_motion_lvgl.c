/****************************************************************************
 * apps/examples/phywear/pw_motion_lvgl.c
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * LVGL path 适配层。为什么单独一个文件：pw_motion.c 必须是纯算法（能在 PC 上单测），
 * 所以 LVGL 依赖全部隔离在这里。
 *
 * 数值约定（与 lv_anim_path_linear 一致，见 LVGL 9 lv_anim.c）：
 *   step = lv_map(act_time, 0, duration, 0, LV_ANIM_RESOLUTION)   // 0..1024
 *   返回值 = start + (end - start) × 曲线(step/1024)
 * 位移用 int64 中间量：end-start 可能到 ±几万像素级 × Q14，int32 会溢出。
 ****************************************************************************/

#include <nuttx/config.h>

#include "pw_motion.h"
#include "pw_motion_lvgl.h"

static lv_anim_value_t motion_path(const lv_anim_t *a, int32_t q14)
{
  int32_t step;
  int32_t span;
  int64_t v;

  step = lv_map(a->act_time, 0, a->duration, 0, PW_MOTION_RES);

  if (step >= PW_MOTION_RES)
    {
      return a->end_value;              /* 末帧严格到位 */
    }

  span = a->end_value - a->start_value;
  v = (int64_t)span * (int64_t)q14;     /* Q14 */
  v >>= PW_MOTION_Q;

  return (lv_anim_value_t)(a->start_value + (int32_t)v);
}

lv_anim_value_t pw_motion_path_spring(const lv_anim_t *a)
{
  return motion_path(a, pw_motion_spring_q14(
                       (uint32_t)lv_map(a->act_time, 0, a->duration,
                                        0, PW_MOTION_RES)));
}

lv_anim_value_t pw_motion_path_decay(const lv_anim_t *a)
{
  return motion_path(a, pw_motion_decay_q14(
                       (uint32_t)lv_map(a->act_time, 0, a->duration,
                                        0, PW_MOTION_RES)));
}

void pw_motion_slide_in_y(lv_obj_t *obj, int from_dy, uint32_t dur_ms)
{
  lv_anim_t a;
  int32_t   y0;

  if (obj == NULL)
    {
      return;
    }

  y0 = lv_obj_get_y(obj);

  lv_anim_init(&a);
  lv_anim_set_var(&a, obj);
  lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_y);
  lv_anim_set_values(&a, y0 + from_dy, y0);
  lv_anim_set_duration(&a, dur_ms);
  lv_anim_set_path_cb(&a, pw_motion_path_spring);
  lv_anim_start(&a);
}
