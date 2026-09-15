/****************************************************************************
 * tools/phywear/pw_math_hosttest.c
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * 主机侧单测入口：把 pw_ahrs / pw_calib 两个纯算法库编到 PC 上跑自检。
 * 这两个文件不依赖 NuttX/LVGL（只 include <nuttx/config.h>，主机用桩头文件），
 * 所以算法改动可以在 PC 上秒级迭代，不必每次烧板子。
 *
 * 用法：bash tools/phywear/hosttest_math.sh
 ****************************************************************************/

#include <stdio.h>

#include "pw_ahrs.h"
#include "pw_calib.h"

int main(void)
{
  float err;
  int   rc;
  int   bad = 0;

  err = -1.0f;
  rc = pw_ahrs_selftest(&err);
  printf("pw_ahrs_selftest  rc=%d  max_attitude_err=%.3f deg   %s\n",
         rc, (double)err, rc == 0 ? "OK" : "FAIL");
  if (rc != 0)
    {
      bad++;
    }

  err = -1.0f;
  rc = pw_calib_selftest(&err);
  printf("pw_calib_selftest rc=%d  worst_rel_err=%.4f            %s\n",
         rc, (double)err, rc == 0 ? "OK" : "FAIL");
  if (rc != 0)
    {
      bad++;
    }

  printf("%s\n", bad == 0 ? "ALL OK" : "SOME FAILED");
  return bad == 0 ? 0 : 1;
}
