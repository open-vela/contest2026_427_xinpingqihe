/****************************************************************************
 * apps/examples/phywear/pw_bt.c
 *
 * 蓝牙阶段 B-1：起 zblue host 栈。判据：打印 rc=0 ⇒ host 栈已初始化。
 ****************************************************************************/

#include <nuttx/config.h>

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <zephyr/bluetooth/bluetooth.h>

int pw_bt_init(void)
{
  struct timespec t0;
  struct timespec t1;
  long ms;
  int rc;
  int fd;

  /* 诊断：先确认 /dev/ttyHCI0 能被打开、再用单调时钟量 bt_enable 的**耗时**。
   * 这是区分「命令超时（HCI_CMD_TIMEOUT = 10 s）」与「控制器立刻回了非 0
   * status」的第一手判据 —— 两者都会让 rc 变成负数，但含义完全不同。 */
  fd = open("/dev/ttyHCI0", O_RDWR | O_NONBLOCK);
  printf("[bt] precheck open /dev/ttyHCI0 -> %d %s\n", fd,
         fd < 0 ? strerror(errno) : "(ok)");
  if (fd >= 0)
    {
      close(fd);
    }

  clock_gettime(CLOCK_MONOTONIC, &t0);
  printf("[bt] calling bt_enable(NULL) ...\n");
  rc = bt_enable(NULL);
  clock_gettime(CLOCK_MONOTONIC, &t1);

  ms = (long)(t1.tv_sec - t0.tv_sec) * 1000L +
       (long)(t1.tv_nsec - t0.tv_nsec) / 1000000L;

  printf("[bt] bt_enable rc=%d %s elapsed=%ldms\n", rc,
         rc == 0 ? "(host stack up)" : "(FAILED)", ms);
  return rc;
}
