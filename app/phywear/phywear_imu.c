/****************************************************************************
 * apps/examples/phywear/phywear_imu.c
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * ③ 惯性标尺 UI：水平仪/姿态 + 标定向导（4 页横滑）。
 *
 * 设计取舍（都为了不撞 SF32LB52 的两个硬约束）：
 *   - **不用任何旋转控件**：水平仪的小球靠"改坐标"实现（lv_obj_set_pos），
 *     不走对象 rotate（那会退回软件光栅，EPIC 不加速）；
 *   - **不新增采样缓冲**：AHRS 状态 76 B、陀螺零偏累加 28 B、六面法累加 ~100 B、
 *     磁椭球正规方程 360 B —— 全是流式充分统计，与采样点数无关；
 *   - 采样沿用 GUI 自己的缓存 fd（`pw_sensors_read_imu/mag`），不走 oneshot
 *     （oneshot 是给别的任务用的，见 docs/03 §6.6）。
 *
 * 三个标定页的"真值"都不依赖外部仪器：
 *   零偏 ← 静止时角速度为零；重力 ← 重力本身（理想读数 ±1 g）；磁 ← 场强恒定（椭球）。
 ****************************************************************************/

#include <nuttx/config.h>

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "phywear_imu.h"
#include "phywear_i18n.h"
#include "phywear_sensors.h"
#include "phywear_ui.h"
#include "pw_ahrs.h"
#include "pw_calib.h"

/****************************************************************************
 * Private Definitions
 ****************************************************************************/

#define IMU_PAGES        4
#define IMU_PAGE_W       390
#define IMU_PAGE_H       342
#define IMU_TICK_MS      20        /* 50 Hz，与单摆页一致 */

#define BIAS_MIN_N       100       /* 静止 2s 才允许求解 */
#define BIAS_STILL_STD   0.02f     /* rad/s ≈1.1 dps，超过就判"没静止" */
#define SIX_FACE_N       50        /* 每个面取 1s */
#define MAG_MIN_N        200       /* 至少 200 个样本才拟合椭球 */

#define LVL_CARD_W       190
#define LVL_CARD_H       150
#define LVL_DOT          20
#define LVL_RANGE_X      72.0f
#define LVL_RANGE_Y      54.0f

struct imu_ui_s
{
  lv_obj_t *scr;
  lv_obj_t *scroller;
  lv_obj_t *dots[IMU_PAGES];
  lv_obj_t *arrow_btn[2];
  int       idx;

  /* page 0 实时数值 */

  lv_obj_t *lvl_card;
  lv_obj_t *lvl_h;
  lv_obj_t *lvl_v;
  lv_obj_t *lvl_dot;
  lv_obj_t *ax_name[3];
  lv_obj_t *ax_val[3];
  lv_obj_t *bias_lab;
  lv_obj_t *live_stat;
  lv_obj_t *live_hint;

  /* page 1 零偏标定 */

  lv_obj_t *bias_btn_lab;
  lv_obj_t *bias_prog;
  lv_obj_t *bias_res;
  lv_obj_t *bias_disp;
  lv_obj_t *bias_stat;

  /* page 2 重力标定（六面） */

  lv_obj_t *grav_face;
  lv_obj_t *grav_btn_lab;
  lv_obj_t *grav_prog;
  lv_obj_t *grav_res[3];
  lv_obj_t *grav_stat;

  /* page 3 磁标定 */

  lv_obj_t *mag_btn_lab;
  lv_obj_t *mag_prog;
  lv_obj_t *mag_res[3];
  lv_obj_t *mag_stat;

  /* 状态 */

  struct pw_ahrs_s       ahrs;
  struct pw_calib_bias_s bias_acc;
  struct pw_calib_six_s  six_acc;
  struct pw_calib_mag_s  mag_acc;

  int       bias_rec;
  int       six_face;          /* 下一个要取的面 0..5 */
  int       mag_rec;
  int       ahrs_inited;
  int       mag_seen;
  uint32_t  last_ms;
};

static struct imu_ui_s g_i;

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static int imu_dps1000(float rps)
{
  return (int)(rps * 57.29578f * 1000.0f);
}

static void imu_sync_dots(void)
{
  int i;

  for (i = 0; i < IMU_PAGES; i++)
    {
      lv_obj_set_style_bg_color(g_i.dots[i],
                                (i == g_i.idx) ? PW_ACC_RAW : PW_COL_CARD_LT,
                                0);
    }
}

static void imu_set_page(int idx)
{
  if (idx < 0)
    {
      idx = 0;
    }
  else if (idx >= IMU_PAGES)
    {
      idx = IMU_PAGES - 1;
    }

  g_i.idx = idx;
  lv_obj_scroll_to_x(g_i.scroller, idx * IMU_PAGE_W, LV_ANIM_OFF);
  imu_sync_dots();
}

static void imu_arrow_cb(lv_event_t *e)
{
  int d = *(int *)lv_event_get_user_data(e);

  imu_set_page(g_i.idx + d);
}

static void imu_scroll_end_cb(lv_event_t *e)
{
  int32_t x = lv_obj_get_scroll_x(g_i.scroller);

  (void)e;

  g_i.idx = (int)((x + IMU_PAGE_W / 2) / IMU_PAGE_W);

  if (g_i.idx < 0)
    {
      g_i.idx = 0;
    }
  else if (g_i.idx >= IMU_PAGES)
    {
      g_i.idx = IMU_PAGES - 1;
    }

  imu_sync_dots();
}

/* 水平仪小球：跟着倾角走（正 roll 向下、正 pitch 向右），不旋转对象 */

static void imu_level_dot(float roll, float pitch)
{
  int   cx = LVL_CARD_W / 2 - LVL_DOT / 2;
  int   cy = LVL_CARD_H / 2 - LVL_DOT / 2;
  float dx = pitch * (LVL_RANGE_X / 45.0f);
  float dy = roll * (LVL_RANGE_Y / 45.0f);

  if (dx > LVL_RANGE_X)  dx = LVL_RANGE_X;
  if (dx < -LVL_RANGE_X) dx = -LVL_RANGE_X;
  if (dy > LVL_RANGE_Y)  dy = LVL_RANGE_Y;
  if (dy < -LVL_RANGE_Y) dy = -LVL_RANGE_Y;

  lv_obj_set_pos(g_i.lvl_dot, cx + (int)dx, cy + (int)dy);
}

static void imu_live_update(void)
{
  float r = 0.0f;
  float p = 0.0f;
  float y = 0.0f;
  float b[3];
  char  buf[72];

  pw_ahrs_euler(&g_i.ahrs, &r, &p, &y);
  pw_ahrs_bias(&g_i.ahrs, b);

  lv_label_set_text_fmt(g_i.ax_val[0], "%+.1f", (double)r);
  lv_label_set_text_fmt(g_i.ax_val[1], "%+.1f", (double)p);
  lv_label_set_text_fmt(g_i.ax_val[2], "%+.1f", (double)y);

  imu_level_dot(r, p);

  snprintf(buf, sizeof(buf), PW_STR(IMU_FMT_BIAS),
           imu_dps1000(b[0]), imu_dps1000(b[1]), imu_dps1000(b[2]));
  lv_label_set_text(g_i.bias_lab, buf);

  snprintf(buf, sizeof(buf), PW_STR(IMU_FMT_MAG),
           g_i.mag_seen ? PW_STR(IMU_V_YES) : PW_STR(IMU_V_NO));
  lv_label_set_text(g_i.live_stat, buf);
}

/* page 1：零偏标定 */

static void imu_bias_cb(lv_event_t *e)
{
  (void)e;

  if (!g_i.bias_rec)
    {
      pw_calib_bias_reset(&g_i.bias_acc);
      g_i.bias_rec = 1;
      lv_label_set_text(g_i.bias_btn_lab, PW_STR(IMU_BTN_STOP));
      lv_label_set_text(g_i.bias_prog, "");
      lv_label_set_text(g_i.bias_res, "");
      lv_label_set_text(g_i.bias_disp, "");
      lv_label_set_text(g_i.bias_stat, "");
      return;
    }

  g_i.bias_rec = 0;
  lv_label_set_text(g_i.bias_btn_lab, PW_STR(IMU_BTN_START));

  if (pw_calib_bias_ready(&g_i.bias_acc, BIAS_MIN_N))
    {
      float bias[3];
      float sd[3];

      /* 第三个参数是"够不够静"的判据：判不过就如实拒绝，不给数字 */

      if (pw_calib_bias_solve(&g_i.bias_acc, bias, sd,
                              BIAS_STILL_STD) == 0)
        {
          lv_label_set_text_fmt(g_i.bias_res, PW_STR(IMU_FMT_BIAS),
                                imu_dps1000(bias[0]), imu_dps1000(bias[1]),
                                imu_dps1000(bias[2]));
          lv_label_set_text_fmt(g_i.bias_disp, PW_STR(IMU_FMT_DISP),
                                imu_dps1000(sd[0]), imu_dps1000(sd[1]),
                                imu_dps1000(sd[2]));
          lv_label_set_text(g_i.bias_stat, PW_STR(IMU_ST_DONE));
          return;
        }
    }

  lv_label_set_text(g_i.bias_stat, PW_STR(IMU_ST_NONE));
}

/* page 2：重力标定（六面法） */

static void imu_grav_face_label(void)
{
  static const char axc[6] = {'X', 'X', 'Y', 'Y', 'Z', 'Z'};
  char tag[4];

  snprintf(tag, sizeof(tag), "%c%c",
           ((g_i.six_face % 2) == 0) ? '+' : '-', axc[g_i.six_face]);
  lv_label_set_text_fmt(g_i.grav_face, PW_STR(IMU_FACE_DOWN), tag);
}

static void imu_grav_solve(void)
{
  float bias[3];
  float scale[3];
  float resid = 0.0f;
  float cross = 0.0f;

  if (pw_calib_six_solve(&g_i.six_acc, bias, scale, &resid, &cross) == 0)
    {
      lv_label_set_text_fmt(g_i.grav_res[0], PW_STR(IMU_FMT_GBIAS),
                            (double)bias[0], (double)bias[1],
                            (double)bias[2]);
      lv_label_set_text_fmt(g_i.grav_res[1], PW_STR(IMU_FMT_SCALE),
                            (double)scale[0], (double)scale[1],
                            (double)scale[2]);
      lv_label_set_text_fmt(g_i.grav_res[2], PW_STR(IMU_FMT_DEV),
                            (double)resid);
      lv_label_set_text_fmt(g_i.grav_prog, PW_STR(IMU_FMT_AXIS),
                            (double)cross);
      lv_label_set_text(g_i.grav_stat, PW_STR(IMU_ST_DONE));
    }
  else
    {
      lv_label_set_text(g_i.grav_stat, PW_STR(IMU_ST_NONE));
    }
}

static void imu_grav_cb(lv_event_t *e)
{
  (void)e;

  if (g_i.six_face >= 6)
    {
      /* 已经全部取完：再点一次重新开始 */

      pw_calib_six_reset(&g_i.six_acc);
      g_i.six_face = 0;
      lv_label_set_text(g_i.grav_res[0], "");
      lv_label_set_text(g_i.grav_res[1], "");
      lv_label_set_text(g_i.grav_res[2], "");
      lv_label_set_text(g_i.grav_stat, "");
      lv_label_set_text(g_i.grav_prog, "");
      imu_grav_face_label();
      return;
    }

  g_i.six_face++;

  if (g_i.six_face >= 6)
    {
      lv_label_set_text(g_i.grav_prog, "");
      imu_grav_solve();
      lv_label_set_text_fmt(g_i.grav_face, PW_STR(IMU_FMT_FACE), 6,
                            SIX_FACE_N);
      return;
    }

  imu_grav_face_label();
}

/* page 3：磁标定 —— 只定形状（椭球→球），物理量纲另行按 WMM 缩放 */

static void imu_mag_cb(lv_event_t *e)
{
  (void)e;

  if (!g_i.mag_rec)
    {
      pw_calib_mag_reset(&g_i.mag_acc);
      g_i.mag_rec = 1;
      lv_label_set_text(g_i.mag_btn_lab, PW_STR(IMU_BTN_STOP));
      lv_label_set_text(g_i.mag_prog, "");
      lv_label_set_text(g_i.mag_res[0], "");
      lv_label_set_text(g_i.mag_res[1], "");
      lv_label_set_text(g_i.mag_res[2], "");
      lv_label_set_text(g_i.mag_stat, "");
      return;
    }

  g_i.mag_rec = 0;
  lv_label_set_text(g_i.mag_btn_lab, PW_STR(IMU_BTN_START));

  if (pw_calib_mag_ready(&g_i.mag_acc, MAG_MIN_N))
    {
      float center[3];
      float w[9];
      float mean = 0.0f;
      float eig[3];

      if (pw_calib_mag_solve(&g_i.mag_acc, center, w, &mean, eig) == 0)
        {
          float lo = eig[0];
          float hi = eig[0];
          int   i;

          for (i = 1; i < 3; i++)
            {
              if (eig[i] < lo) lo = eig[i];
              if (eig[i] > hi) hi = eig[i];
            }

          lv_label_set_text_fmt(g_i.mag_res[0], PW_STR(IMU_FMT_MAGC),
                                (int)center[0], (int)center[1],
                                (int)center[2]);
          lv_label_set_text_fmt(g_i.mag_res[1], PW_STR(IMU_FMT_MAGMEAN),
                                (int)mean);
          lv_label_set_text_fmt(g_i.mag_res[2], PW_STR(IMU_FMT_MAGDISP),
                                (double)((lo > 0.0f) ? (hi / lo - 1.0f) : 0.0f));
          lv_label_set_text(g_i.mag_stat, PW_STR(IMU_ST_DONE));
          return;
        }
    }

  lv_label_set_text(g_i.mag_stat, PW_STR(IMU_ST_NONE));
}

/****************************************************************************
 * Tick：一路采样，按当前页分派（50 Hz）
 ****************************************************************************/

static void imu_tick_cb(lv_timer_t *timer)
{
  struct pw_imu_s imu;
  struct pw_mag_s mag;
  float a[3];
  float g[3];
  float m[3];
  float dt;
  uint32_t now;
  int   have_mag;

  (void)timer;

  now = lv_tick_get();
  dt = (float)(now - g_i.last_ms) / 1000.0f;
  g_i.last_ms = now;

  if (dt <= 0.0f || dt > 0.5f)
    {
      dt = (float)IMU_TICK_MS / 1000.0f;
    }

  if (pw_sensors_read_imu(&imu) < 0)
    {
      return;
    }

  a[0] = imu.ax / 1000.0f;
  a[1] = imu.ay / 1000.0f;
  a[2] = imu.az / 1000.0f;
  g[0] = imu.gx * 1.745329e-5f;
  g[1] = imu.gy * 1.745329e-5f;
  g[2] = imu.gz * 1.745329e-5f;

  have_mag = (pw_sensors_read_mag(&mag) == 0);
  if (have_mag)
    {
      m[0] = (float)mag.x;
      m[1] = (float)mag.y;
      m[2] = (float)mag.z;
      g_i.mag_seen = 1;
    }

  switch (g_i.idx)
    {
      case 0:
        if (!g_i.ahrs_inited)
          {
            pw_ahrs_init(&g_i.ahrs);
            g_i.ahrs_inited = 1;
          }

        pw_ahrs_update(&g_i.ahrs, a, g, have_mag ? m : NULL, dt);
        imu_live_update();
        break;

      case 1:
        if (g_i.bias_rec)
          {
            pw_calib_bias_add(&g_i.bias_acc, g);
            lv_label_set_text_fmt(g_i.bias_prog, PW_STR(IMU_FMT_SAMPLES),
                                  g_i.bias_acc.n);
          }
        break;

      case 2:
        if (g_i.six_face < 6 &&
            g_i.six_acc.n[g_i.six_face] < SIX_FACE_N)
          {
            pw_calib_six_add(&g_i.six_acc, g_i.six_face, a);
            lv_label_set_text_fmt(g_i.grav_prog, PW_STR(IMU_FMT_FACE),
                                  g_i.six_face + 1,
                                  g_i.six_acc.n[g_i.six_face]);
          }
        break;

      case 3:
        if (g_i.mag_rec && have_mag)
          {
            float mean;

            pw_calib_mag_add(&g_i.mag_acc, m);
            mean = sqrtf(g_i.mag_acc.sum_r2 / (float)g_i.mag_acc.n) / 0.001f;

            lv_label_set_text_fmt(g_i.mag_prog, PW_STR(IMU_FMT_SAMPLES),
                                  g_i.mag_acc.n);
            lv_label_set_text_fmt(g_i.mag_res[1], PW_STR(IMU_FMT_MAGMEAN),
                                  (int)mean);
          }
        break;

      default:
        break;
    }
}

/****************************************************************************
 * Builders
 ****************************************************************************/

static lv_obj_t *imu_page_new(int pi)
{
  lv_obj_t *pg = lv_obj_create(g_i.scroller);

  lv_obj_set_size(pg, IMU_PAGE_W, IMU_PAGE_H);
  lv_obj_set_pos(pg, pi * IMU_PAGE_W, 0);
  lv_obj_set_style_bg_opa(pg, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(pg, 0, 0);
  lv_obj_set_style_pad_all(pg, 0, 0);
  lv_obj_remove_flag(pg, LV_OBJ_FLAG_SCROLLABLE);

  return pg;
}

static void imu_button(lv_obj_t *parent, int x, int y, int w, int h,
                       lv_event_cb_t cb, const char *text,
                       lv_obj_t **lab_out)
{
  lv_obj_t *btn = pw_card_new(parent, w, h, PW_COL_CARD);
  lv_obj_t *lab;

  lv_obj_set_pos(btn, x, y);
  lv_obj_add_event_cb(btn, cb, LV_EVENT_CLICKED, NULL);

  lab = pw_label_new(btn, text, PW_FNT_MED, PW_COL_TEXT);
  lv_obj_center(lab);

  if (lab_out != NULL)
    {
      *lab_out = lab;
    }
}

static void imu_build_live(void)
{
  lv_obj_t *pg = imu_page_new(0);
  lv_obj_t *lab;
  static const char *nm[3] = {"roll", "pitch", "yaw"};
  int i;

  lab = pw_label_new(pg, PW_STR(IMU_LIVE), PW_FNT_LARGE, PW_ACC_RAW);
  lv_obj_set_pos(lab, 20, 2);

  g_i.lvl_card = pw_card_new(pg, LVL_CARD_W, LVL_CARD_H, PW_COL_CARD);
  lv_obj_set_pos(g_i.lvl_card, 14, 36);
  lv_obj_remove_flag(g_i.lvl_card, LV_OBJ_FLAG_SCROLLABLE);

  g_i.lvl_h = lv_obj_create(g_i.lvl_card);
  lv_obj_set_size(g_i.lvl_h, LVL_CARD_W - 20, 1);
  lv_obj_set_style_radius(g_i.lvl_h, 0, 0);
  lv_obj_set_style_bg_color(g_i.lvl_h, PW_COL_GRID, 0);
  lv_obj_set_style_border_width(g_i.lvl_h, 0, 0);
  lv_obj_remove_flag(g_i.lvl_h, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_center(g_i.lvl_h);

  g_i.lvl_v = lv_obj_create(g_i.lvl_card);
  lv_obj_set_size(g_i.lvl_v, 1, LVL_CARD_H - 20);
  lv_obj_set_style_radius(g_i.lvl_v, 0, 0);
  lv_obj_set_style_bg_color(g_i.lvl_v, PW_COL_GRID, 0);
  lv_obj_set_style_border_width(g_i.lvl_v, 0, 0);
  lv_obj_remove_flag(g_i.lvl_v, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_center(g_i.lvl_v);

  g_i.lvl_dot = lv_obj_create(g_i.lvl_card);
  lv_obj_set_size(g_i.lvl_dot, LVL_DOT, LVL_DOT);
  lv_obj_set_style_radius(g_i.lvl_dot, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_color(g_i.lvl_dot, PW_ACC_RAW, 0);
  lv_obj_set_style_border_width(g_i.lvl_dot, 0, 0);
  lv_obj_remove_flag(g_i.lvl_dot, LV_OBJ_FLAG_SCROLLABLE);
  imu_level_dot(0.0f, 0.0f);

  for (i = 0; i < 3; i++)
    {
      g_i.ax_name[i] = pw_label_new(pg, nm[i], PW_FNT_BODY, PW_COL_DIM);
      lv_obj_set_pos(g_i.ax_name[i], 220, 40 + i * 66);

      g_i.ax_val[i] = pw_label_new(pg, "--", PW_FNT_XL, PW_COL_TEXT);
      lv_obj_set_pos(g_i.ax_val[i], 220, 58 + i * 66);
    }

  g_i.bias_lab = pw_label_new(pg, "", PW_FNT_BODY, PW_COL_DIM);
  lv_obj_set_pos(g_i.bias_lab, 14, 200);

  g_i.live_stat = pw_label_new(pg, "", PW_FNT_BODY, PW_COL_DIM);
  lv_obj_set_pos(g_i.live_stat, 14, 222);

  g_i.live_hint = pw_label_new(pg, PW_STR(IMU_HINT_LIVE), PW_FNT_BODY,
                               PW_COL_FAINT);
  lv_obj_set_pos(g_i.live_hint, 14, 244);
}

static void imu_build_bias(void)
{
  lv_obj_t *pg = imu_page_new(1);
  lv_obj_t *lab;

  lab = pw_label_new(pg, PW_STR(IMU_BIAS_CAL), PW_FNT_LARGE, PW_ACC_RAW);
  lv_obj_set_pos(lab, 20, 2);

  lab = pw_label_new(pg, PW_STR(IMU_HINT_FLAT), PW_FNT_BODY, PW_COL_DIM);
  lv_obj_set_pos(lab, 20, 40);
  lv_obj_set_width(lab, 350);
  lv_label_set_long_mode(lab, LV_LABEL_LONG_WRAP);

  imu_button(pg, 20, 86, 170, 48, imu_bias_cb, PW_STR(IMU_BTN_START),
             &g_i.bias_btn_lab);

  g_i.bias_prog = pw_label_new(pg, "", PW_FNT_MED, PW_COL_TEXT);
  lv_obj_set_pos(g_i.bias_prog, 20, 152);

  g_i.bias_res = pw_label_new(pg, "", PW_FNT_SMALL, PW_COL_TEXT);
  lv_obj_set_pos(g_i.bias_res, 20, 190);

  g_i.bias_disp = pw_label_new(pg, "", PW_FNT_SMALL, PW_COL_DIM);
  lv_obj_set_pos(g_i.bias_disp, 20, 214);

  g_i.bias_stat = pw_label_new(pg, "", PW_FNT_SMALL, PW_ACC_MECH);
  lv_obj_set_pos(g_i.bias_stat, 20, 244);
}

static void imu_build_grav(void)
{
  lv_obj_t *pg = imu_page_new(2);
  lv_obj_t *lab;

  lab = pw_label_new(pg, PW_STR(IMU_GRAV_CAL), PW_FNT_LARGE, PW_ACC_RAW);
  lv_obj_set_pos(lab, 20, 2);

  lab = pw_label_new(pg, PW_STR(IMU_HINT_GRAV), PW_FNT_BODY, PW_COL_DIM);
  lv_obj_set_pos(lab, 20, 34);
  lv_obj_set_width(lab, 350);
  lv_label_set_long_mode(lab, LV_LABEL_LONG_WRAP);

  g_i.grav_face = pw_label_new(pg, "", PW_FNT_XL, PW_COL_TEXT);
  lv_obj_set_pos(g_i.grav_face, 20, 84);

  imu_button(pg, 20, 130, 190, 48, imu_grav_cb, PW_STR(IMU_BTN_TAKE),
             &g_i.grav_btn_lab);

  g_i.grav_prog = pw_label_new(pg, "", PW_FNT_BODY, PW_COL_DIM);
  lv_obj_set_pos(g_i.grav_prog, 20, 190);

  g_i.grav_res[0] = pw_label_new(pg, "", PW_FNT_SMALL, PW_COL_TEXT);
  lv_obj_set_pos(g_i.grav_res[0], 20, 218);
  g_i.grav_res[1] = pw_label_new(pg, "", PW_FNT_SMALL, PW_COL_TEXT);
  lv_obj_set_pos(g_i.grav_res[1], 20, 240);
  g_i.grav_res[2] = pw_label_new(pg, "", PW_FNT_SMALL, PW_COL_DIM);
  lv_obj_set_pos(g_i.grav_res[2], 20, 262);

  g_i.grav_stat = pw_label_new(pg, "", PW_FNT_SMALL, PW_ACC_MECH);
  lv_obj_set_pos(g_i.grav_stat, 20, 292);

  imu_grav_face_label();
}

static void imu_build_mag(void)
{
  lv_obj_t *pg = imu_page_new(3);
  lv_obj_t *lab;

  lab = pw_label_new(pg, PW_STR(IMU_MAG_CAL), PW_FNT_LARGE, PW_ACC_RAW);
  lv_obj_set_pos(lab, 20, 2);

  lab = pw_label_new(pg, PW_STR(IMU_HINT_MAG), PW_FNT_BODY, PW_COL_DIM);
  lv_obj_set_pos(lab, 20, 40);
  lv_obj_set_width(lab, 350);
  lv_label_set_long_mode(lab, LV_LABEL_LONG_WRAP);

  imu_button(pg, 20, 86, 170, 48, imu_mag_cb, PW_STR(IMU_BTN_START),
             &g_i.mag_btn_lab);

  g_i.mag_prog = pw_label_new(pg, "", PW_FNT_BODY, PW_COL_TEXT);
  lv_obj_set_pos(g_i.mag_prog, 20, 152);

  g_i.mag_res[0] = pw_label_new(pg, "", PW_FNT_SMALL, PW_COL_TEXT);
  lv_obj_set_pos(g_i.mag_res[0], 20, 190);
  g_i.mag_res[1] = pw_label_new(pg, "", PW_FNT_SMALL, PW_COL_TEXT);
  lv_obj_set_pos(g_i.mag_res[1], 20, 214);
  g_i.mag_res[2] = pw_label_new(pg, "", PW_FNT_SMALL, PW_COL_DIM);
  lv_obj_set_pos(g_i.mag_res[2], 20, 238);

  g_i.mag_stat = pw_label_new(pg, "", PW_FNT_SMALL, PW_ACC_MECH);
  lv_obj_set_pos(g_i.mag_stat, 20, 268);
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

lv_obj_t *pw_imu_screen(void)
{
  lv_obj_t *btn;
  lv_obj_t *lab;
  static int dm = -1;
  static int dp = 1;
  int i;

  memset(&g_i, 0, sizeof(g_i));
  g_i.six_face = 0;

  g_i.scr = pw_scr_new();

  g_i.scroller = pw_topbar(g_i.scr, PW_STR(IMU_TITLE));
  lv_obj_set_size(g_i.scroller, IMU_PAGE_W, IMU_PAGE_H);
  lv_obj_set_scroll_dir(g_i.scroller, LV_DIR_HOR);
  lv_obj_set_scroll_snap_x(g_i.scroller, LV_SCROLL_SNAP_CENTER);
  lv_obj_set_scrollbar_mode(g_i.scroller, LV_SCROLLBAR_MODE_OFF);
  lv_obj_add_flag(g_i.scroller, LV_OBJ_FLAG_SCROLL_ONE);
  lv_obj_add_event_cb(g_i.scroller, imu_scroll_end_cb, LV_EVENT_SCROLL_END,
                      NULL);

  imu_build_live();
  imu_build_bias();
  imu_build_grav();
  imu_build_mag();

  btn = pw_card_new(g_i.scr, 64, 44, PW_COL_CARD);
  lv_obj_set_pos(btn, 4, 396);
  lv_obj_add_event_cb(btn, imu_arrow_cb, LV_EVENT_CLICKED, &dm);
  lab = pw_label_new(btn, "<", PW_FNT_XL, PW_COL_DIM);
  lv_obj_center(lab);
  g_i.arrow_btn[0] = btn;

  for (i = 0; i < IMU_PAGES; i++)
    {
      lv_obj_t *d = lv_obj_create(g_i.scr);

      lv_obj_set_size(d, 12, 12);
      lv_obj_set_style_bg_color(d, PW_COL_CARD_LT, 0);
      lv_obj_set_style_radius(d, LV_RADIUS_CIRCLE, 0);
      lv_obj_set_style_border_width(d, 0, 0);
      lv_obj_remove_flag(d, LV_OBJ_FLAG_SCROLLABLE);
      g_i.dots[i] = d;
      lv_obj_align(d, LV_ALIGN_BOTTOM_MID, (i - (IMU_PAGES - 1) / 2) * 18, -24);
    }

  btn = pw_card_new(g_i.scr, 64, 44, PW_COL_CARD);
  lv_obj_set_pos(btn, IMU_PAGE_W - 4 - 64, 396);
  lv_obj_add_event_cb(btn, imu_arrow_cb, LV_EVENT_CLICKED, &dp);
  lab = pw_label_new(btn, ">", PW_FNT_XL, PW_COL_DIM);
  lv_obj_center(lab);
  g_i.arrow_btn[1] = btn;

  imu_set_page(0);

  g_i.last_ms = lv_tick_get();
  pw_scr_set_tick(g_i.scr, imu_tick_cb, IMU_TICK_MS);

  return g_i.scr;
}

void pw_imu_goto(int idx)
{
  if (g_i.scroller == NULL)
    {
      return;
    }

  imu_set_page(idx);
}
