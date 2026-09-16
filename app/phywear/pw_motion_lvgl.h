/****************************************************************************
 * apps/examples/phywear/pw_motion_lvgl.h
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * 把 pw_motion 的两条曲线接到 LVGL 的动画 path 上（P1-2）。
 *
 * 用法（与 LVGL 原生 path 完全一致，替换即可）：
 *     lv_anim_set_path_cb(&a, pw_motion_path_spring);
 * 想整体退回原生线性动效，把 path_cb 换回 lv_anim_path_linear 即可（不改别处）。
 ****************************************************************************/

#ifndef __APPS_EXAMPLES_PHYWEAR_MOTION_LVGL_H
#define __APPS_EXAMPLES_PHYWEAR_MOTION_LVGL_H

#include <lvgl/lvgl.h>

/* 物理弹簧手感（欠阻尼，约 25% 过冲，末帧严格落在 end_value） */
lv_anim_value_t pw_motion_path_spring(const lv_anim_t *a);

/* 衰减振荡（用于"抖一下"的反馈；末值回到 end_value） */
lv_anim_value_t pw_motion_path_decay(const lv_anim_t *a);

/* 便捷封装：对 obj 做一次"从 from_dy 像素外滑入到位"的弹簧动画。
 * 只改 y 坐标（几何变化 → 局部重绘），不动样式，不触发 SW 变换路径。 */
void pw_motion_slide_in_y(lv_obj_t *obj, int from_dy, uint32_t dur_ms);

#endif /* __APPS_EXAMPLES_PHYWEAR_MOTION_LVGL_H */
