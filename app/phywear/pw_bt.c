/****************************************************************************
 * apps/examples/phywear/pw_bt.c
 *
 * 蓝牙阶段 B-1：起 zblue host 栈。判据：打印 rc=0 ⇒ host 栈已初始化。
 ****************************************************************************/

#include <nuttx/config.h>

#include <stdio.h>
#include <time.h>
#include <unistd.h>

#include <zephyr/bluetooth/bluetooth.h>

#include "pw_bt.h"

int pw_btgatt_start(void);

/* 栈只起一次。
 *
 * 为什么必须幂等（2026-09-18 加）：自从「蓝牙」页会自己确保栈已启动之后，
 * pw_bt_init() 可能被两条路各调一次 —— NSH 的 `phywear cap bt` 一次、
 * 用户点开蓝牙页再一次。第二次 bt_enable() 会回 -EALREADY，而后面那次
 * pw_btgatt_start() 会把同一个 service 再注册一遍（属性表被重新赋 handle，
 * 广播也会重复开），属于"看着能跑、状态已经乱了"的那类问题。
 * 所以这里显式记住"是不是我起的"，第二次直接返回成功。 */

static int g_bt_up;

int pw_bt_init(void)
{
  struct timespec t0;
  struct timespec t1;
  long ms;
  int rc;

  if (g_bt_up)
    {
      printf("[bt] bt_enable skipped (host stack already up)\n");
      return 0;
    }

  /* 单调时钟量 bt_enable 的**耗时**：这是区分「命令超时（HCI_CMD_TIMEOUT = 10 s）」
   * 与「控制器立刻回了非 0 status / 发送路径当场失败」的第一手判据 ——
   * 两者都会让 rc 变成负数，但含义完全不同（B1 根因就是靠它锁定"4 ms 快失败"）。 */

  clock_gettime(CLOCK_MONOTONIC, &t0);
  printf("[bt] calling bt_enable(NULL) ...\n");
  rc = bt_enable(NULL);
  clock_gettime(CLOCK_MONOTONIC, &t1);

  ms = (long)(t1.tv_sec - t0.tv_sec) * 1000L +
       (long)(t1.tv_nsec - t0.tv_nsec) / 1000000L;

  printf("[bt] bt_enable rc=%d %s elapsed=%ldms\n", rc,
         rc == 0 ? "(host stack up)" : "(FAILED)", ms);

  /* B1 过了才做 B2：注册 GATT 服务 + 开可被发现广播。
   * 失败只打印，不影响 B1 的判据。 */

  if (rc == 0)
    {
      g_bt_up = 1;
      pw_btgatt_start();
    }

  return rc;
}

int pw_bt_is_up(void)
{
  return g_bt_up;
}
