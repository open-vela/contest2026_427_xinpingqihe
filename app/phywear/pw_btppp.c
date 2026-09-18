/****************************************************************************
 * apps/examples/phywear/pw_btppp.c
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * 任务 3 的第三条路线：**PPP over BLE**（NuttX 侧实现）
 *
 * 为什么走这条：本板没有无线网卡（`ifconfig: open failed: 2` 已证），
 * USB CDC-ACM + SLIP 又缺一根插不上的数据线 ⇒ **BLE 是唯一现成的物理通道**。
 * 而 NuttX 自带完整的 pppd（apps/netutils/pppd），主机侧 pppd 也早已安装 ——
 * 中间只差一条"BLE ↔ 伪终端(pty)"的**字节管道**，本文件就是这条管道。
 *
 * 结构（三段，各司其职）：
 *
 *   ┌─ BT 侧 ────────────────┐   ┌─ 本文件 ─────────────┐   ┌─ NuttX 网络 ─┐
 *   │ GATT 写 (a101)  ──────►│──►│ 环形缓冲 → pty master │──►│              │
 *   │ GATT 通知 (a102) ◄─────│◄──│ pty master → 通知      │◄──│ pppd (slave) │
 *   └────────────────────────┘   └──────────────────────┘   └──────┬───────┘
 *                                                                  ▼
 *                                                          ppp0 + IP（N1/N2）
 *
 * 为什么用 **pty** 而不是直接把 fd 交给 pppd：
 *   NuttX 的 pppd 只通过 `ppp_arch_getchar/putchar` 读写它自己 `open()` 的
 *   那个**字符设备**（见 apps/netutils/pppd/pppd.c）。pty 正好提供一对
 *   双向字节流：我们拿 master，pppd 拿 slave，双方都不用改一行 pppd 代码。
 *
 * 为什么用 **GATT 写+通知** 而不是 L2CAP CoC：
 *   CoC 吞吐更好，但 Linux 用户态走 LE CoC 的 socket API 很别扭；
 *   而 GATT 这条路我们已经有现成、验证过的宿主实现（pw_ble_central.py）。
 *   协商后 ATT MTU 是 517 ⇒ 单次可带 ~514 B，跑 LCP/IPCP/ping 绰绰有余。
 *   （CoC 留作后续优化，见 docs/16 §13.6e。）
 *
 * 跨线程约定（与 pw_btgatt.c 同一套，务必遵守）：
 *   * GATT 回调跑在 BT 接收线程 ⇒ 只往环形缓冲写字节、只改标志位，
 *     **不碰任何 fd、不碰 LVGL**；
 *   * pty 的 open/read/write 全部在**本文件自己的任务**里做 ——
 *     NuttX 的 fd 表挂在 task_group 上（B1 根因就是踩了这个），
 *     所以"谁 open 谁用"，绝不把 fd 跨组传；
 *   * 索引都是单字宽读写，单生产者单消费者 ⇒ 不加锁。满了丢字节并计数
 *     （PPP 有自己的 FCS，丢包会重传比在这里阻塞 BT 线程划算）。
 ****************************************************************************/

#include <nuttx/config.h>

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>

#include "netutils/pppd.h"
#include "pw_btppp.h"

/* ── 总开关：默认 **关**（2026-09-18 实测结论，见 docs/16 §13.6e）────────
 *
 * 功能本身已经跑通到"pty 建好 + pppd 起来"（串口原文见证据目录），
 * 但**装不进这块板子**：
 *   · NuttX 的 pppd 需要 ~16 KB 栈（它自己的参考例子
 *     apps/examples/pppd 用 CONFIG_EXAMPLES_PPPD_STACKSIZE=16096）；
 *   · 加上桥任务的 2 KB，本功能要 ~18 KB SRAM；
 *   · 实测本板的悬崖在 **95.98%（能开机）与 96.76%（开机挂死在 SFBL/ABCD 之前）之间**
 *     —— 也就是只有约 4~5 KB 余量。把 pppd 栈给到 16 KB 时 SRAM 到 98.71%，
 *     板子连 ABCD 都打不出来（只有 SFBL）。
 * 所以默认关掉，保住出货固件可开机；等 SRAM 腾出 ~18 KB 再打开这个宏即可
 * （代码、GATT 服务、宿主脚本都已就位）。
 */
#ifndef PW_BT_PPP
#define PW_BT_PPP 0
#endif

#if PW_BT_PPP

/* ── 环形缓冲：BT 线程写，管道任务读 ─────────────────────────────────── */

#define PPP_RX_RING   2048      /* 主机 → 设备 */
#define PPP_TX_CHUNK  512       /* 设备 → 主机：单次通知上限（MTU 517 够用） */
#define PPP_PTY_RD    256

static uint8_t           g_rx[PPP_RX_RING];
static volatile unsigned g_rx_head;
static volatile unsigned g_rx_tail;
static volatile uint32_t g_rx_drop;      /* 丢弃字节数（缓冲满） */
static volatile uint32_t g_rx_total;     /* 累计收字节 */
static volatile uint32_t g_tx_total;     /* 累计发字节 */
static volatile uint8_t  g_ppp_sub;      /* 主机是否订阅了 TX 通知 */
static volatile uint8_t  g_ppp_up;       /* 管道是否已起来 */

static int  g_pty_master = -1;
static char g_pty_slave[32];
static volatile int g_pppd_rc = -1;      /* pppd 退出码（正常时永远不返回） */

/* pppd 参数。两点注意：
 * ① `ttyname` 在 struct 里是**字符数组**（`char ttyname[TTYNAMSIZ]`），
 *    不是指针 —— 所以只能等 pty 建好、拿到 slave 名字之后再 strlcpy 进去，
 *    在这里没法"指过去"（第一版就是这么写的，编译器报
 *    "initializer element is not computable at load time"）。
 * ② connect/disconnect_script 都给 NULL：那两个脚本是给"AT 指令拨号猫"用的
 *    （走 chat）；我们是 pty 直连、对面就是宿主 pppd，没有猫可拨，给了反而
 *    会去等 AT 响应。pppd 里是 `if (settings->connect_script)` 才跑 chat。 */
static struct pppd_settings_s g_pppd_settings;

/* ── GATT：第二个服务，专门做字节管道 ─────────────────────────────────
 * 单独一个服务而不是塞进原来的服务里：原来的属性索引（sensor=2/ccc=3/
 * cmd=5/text=7）被自检与验收脚本断言着，加在后面容易牵一发动全身。 */

static const struct bt_uuid_128 pw_ppp_svc_uuid = BT_UUID_INIT_128(
  0xf6, 0xe5, 0xd4, 0xc3, 0xb2, 0xa1, 0x90, 0x8f,
  0x5e, 0x4d, 0x2c, 0x1b, 0x00, 0xa1, 0xf1, 0xe0);

static const struct bt_uuid_128 pw_ppp_rx_uuid = BT_UUID_INIT_128(
  0xf6, 0xe5, 0xd4, 0xc3, 0xb2, 0xa1, 0x90, 0x8f,
  0x5e, 0x4d, 0x2c, 0x1b, 0x01, 0xa1, 0xf1, 0xe0);

static const struct bt_uuid_128 pw_ppp_tx_uuid = BT_UUID_INIT_128(
  0xf6, 0xe5, 0xd4, 0xc3, 0xb2, 0xa1, 0x90, 0x8f,
  0x5e, 0x4d, 0x2c, 0x1b, 0x02, 0xa1, 0xf1, 0xe0);

/* 主机 → 设备：整段塞进环形缓冲。
 * 注意只接受 offset==0 —— 长写（Prepare/Execute）在这里没有意义：
 * 宿主按 MTU 分片写就好，PPP 的 HDLC 帧本来就能拼回来。 */
static ssize_t pw_ppp_write(struct bt_conn *conn,
                            const struct bt_gatt_attr *attr, const void *buf,
                            uint16_t len, uint16_t offset, uint8_t flags)
{
  const uint8_t *p = buf;
  unsigned head;
  unsigned i;

  (void)conn;
  (void)attr;
  (void)flags;

  if (offset != 0)
    {
      return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
    }

  head = g_rx_head;

  for (i = 0; i < len; i++)
    {
      unsigned next = (head + 1u) % PPP_RX_RING;

      if (next == g_rx_tail)
        {
          /* 缓冲满：丢剩下的并记账。绝不在这里等 —— 这是 BT 接收线程。 */
          g_rx_drop += (len - i);
          break;
        }

      g_rx[head] = p[i];
      head = next;
    }

  g_rx_head = head;
  g_rx_total += i;
  return len;
}

static void pw_ppp_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
  (void)attr;
  g_ppp_sub = (value & BT_GATT_CCC_NOTIFY) ? 1 : 0;
  printf("[ppp] host notify %s\n", g_ppp_sub ? "ON" : "OFF");
}

/* 状态读：一行 ASCII，宿主读一次就知道管道/pppd 的现状 */
static ssize_t pw_ppp_status_read(struct bt_conn *conn,
                                  const struct bt_gatt_attr *attr, void *buf,
                                  uint16_t len, uint16_t offset)
{
  char st[96];
  int n;

  n = snprintf(st, sizeof(st),
               "PhyWear ppp up=%u sub=%u slave=%s rx=%u tx=%u drop=%u pppd=%d",
               (unsigned)g_ppp_up, (unsigned)g_ppp_sub, g_pty_slave,
               (unsigned)g_rx_total, (unsigned)g_tx_total,
               (unsigned)g_rx_drop, g_pppd_rc);

  return bt_gatt_attr_read(conn, attr, buf, len, offset, st, (uint16_t)n);
}

static struct bt_gatt_attr pw_ppp_attrs[] =
{
  BT_GATT_PRIMARY_SERVICE(&pw_ppp_svc_uuid.uuid),

  /* 主机写进来的 PPP 字节 */
  BT_GATT_CHARACTERISTIC(&pw_ppp_rx_uuid.uuid,
                         BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP,
                         BT_GATT_PERM_WRITE,
                         NULL, pw_ppp_write, NULL),

  /* 设备发出去的 PPP 字节（通知）+ 状态读 */
  BT_GATT_CHARACTERISTIC(&pw_ppp_tx_uuid.uuid,
                         BT_GATT_CHRC_NOTIFY | BT_GATT_CHRC_READ,
                         BT_GATT_PERM_READ,
                         pw_ppp_status_read, NULL, NULL),
  BT_GATT_CCC(pw_ppp_ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
};

#define PW_PPP_ATTR_TX 2

static struct bt_gatt_service pw_ppp_svc = BT_GATT_SERVICE(pw_ppp_attrs);

/* ── pppd 任务：它自己 open(slave)，然后永远循环 ─────────────────────── */

static void pw_ppp_pppd_task(void *a, void *b, void *c)
{
  (void)a;
  (void)b;
  (void)c;

  strlcpy(g_pppd_settings.ttyname, g_pty_slave,
          sizeof(g_pppd_settings.ttyname));
  printf("[ppp] starting pppd on %s\n", g_pppd_settings.ttyname);
  g_pppd_rc = pppd(&g_pppd_settings);
  printf("[ppp] pppd returned %d (0 才是正常退出)\n", g_pppd_rc);
}

K_THREAD_STACK_DEFINE(g_pppd_stack, 16384);   /* 与 apps/examples/pppd 的 CONFIG_EXAMPLES_PPPD_STACKSIZE 一致 */
static struct k_thread g_pppd_thread;

/* ── 管道任务：pty master 与环形缓冲/通知之间的搬运工 ───────────────── */

K_THREAD_STACK_DEFINE(g_bridge_stack, 2048);
static struct k_thread g_bridge_thread;

static void pw_ppp_bridge_task(void *a, void *b, void *c)
{
  uint8_t buf[PPP_PTY_RD];
  struct pollfd pfd;

  (void)a;
  (void)b;
  (void)c;

  /* 第一行就打"我起来了"：上一版桥任务栈只有 1 KB，而下面 buf[] 就 512 B，
   * 直接栈溢出**整机静默**（没有 panic、没有 assert，串口就停了）——
   * 有这么一行"活着"的标志，下次一眼能分辨"任务没起来"还是"起来后崩了"。 */
  printf("[ppp] bridge task up (stack %u B)\n",
         (unsigned)K_THREAD_STACK_SIZEOF(g_bridge_stack));

  /* 1) 建 pty。master 归我们，slave 名字交给 pppd 去 open。
   *    NuttX 没实现 grantpt()（include/stdlib.h 里明确写了），跳过它。 */
  g_pty_master = posix_openpt(O_RDWR | O_NOCTTY);
  if (g_pty_master < 0)
    {
      printf("[ppp] posix_openpt FAILED errno=%d\n", errno);
      return;
    }

  if (unlockpt(g_pty_master) < 0)
    {
      printf("[ppp] unlockpt FAILED errno=%d\n", errno);
      return;
    }

  {
    FAR char *name = ptsname(g_pty_master);

    if (name == NULL)
      {
        printf("[ppp] ptsname FAILED errno=%d\n", errno);
        return;
      }

    strncpy(g_pty_slave, name, sizeof(g_pty_slave) - 1);
    g_pty_slave[sizeof(g_pty_slave) - 1] = '\0';
  }

  printf("[ppp] pty master fd=%d slave=%s\n", g_pty_master, g_pty_slave);

  /* 2) pppd 自己的任务（阻塞式无限循环）。名字先填好再建任务。 */
  k_thread_create(&g_pppd_thread, g_pppd_stack,
                  K_THREAD_STACK_SIZEOF(g_pppd_stack),
                  pw_ppp_pppd_task, NULL, NULL, NULL,
                  K_PRIO_PREEMPT(8), 0, K_NO_WAIT);
  k_thread_name_set(&g_pppd_thread, "pw_pppd");

  g_ppp_up = 1;
  pfd.fd = g_pty_master;
  pfd.events = POLLIN;

  /* 3) 双向搬运 */
  for (; ; )
    {
      int progress = 0;

      /* 3a) 主机 → 设备：把环形缓冲抽干写进 pty */
      while (g_rx_tail != g_rx_head)
        {
          unsigned tail = g_rx_tail;
          unsigned n = 0;
          ssize_t w;

          while (n < sizeof(buf) && tail != g_rx_head)
            {
              buf[n++] = g_rx[tail];
              tail = (tail + 1u) % PPP_RX_RING;
            }

          w = write(g_pty_master, buf, n);
          if (w <= 0)
            {
              break;                    /* 写不进去就下一轮再试，别丢 */
            }

          g_rx_tail = (g_rx_tail + (unsigned)w) % PPP_RX_RING;
          progress = 1;
        }

      /* 3b) 设备 → 主机：pty 有数据就通知出去 */
      pfd.revents = 0;
      if (poll(&pfd, 1, progress ? 0 : 20) > 0 && (pfd.revents & POLLIN))
        {
          ssize_t r = read(g_pty_master, buf, sizeof(buf));

          if (r > 0)
            {
              if (g_ppp_sub)
                {
                  ssize_t sent = 0;

                  while (sent < r)
                    {
                      size_t chunk = (size_t)(r - sent);

                      if (chunk > PPP_TX_CHUNK)
                        {
                          chunk = PPP_TX_CHUNK;
                        }

                      if (bt_gatt_notify(NULL, &pw_ppp_attrs[PW_PPP_ATTR_TX],
                                         buf + sent, chunk) < 0)
                        {
                          break;
                        }

                      sent += (ssize_t)chunk;
                    }

                  g_tx_total += (uint32_t)sent;
                }
              else
                {
                  /* 没人订阅就先丢：PPP 会重传，比在这儿堆积更划算 */
                  g_rx_drop += 0;
                }
            }
        }
    }
}

/* ── 对外入口 ───────────────────────────────────────────────────────── */

int pw_btppp_start(void)
{
  int rc;

  if (g_ppp_up)
    {
      printf("[ppp] already running (slave=%s)\n", g_pty_slave);
      return 0;
    }

  rc = bt_gatt_service_register(&pw_ppp_svc);
  printf("[ppp] gatt service register rc=%d %s\n", rc,
         rc == 0 ? "(ok)" : "(FAILED)");
  if (rc != 0)
    {
      return rc;
    }

  k_thread_create(&g_bridge_thread, g_bridge_stack,
                  K_THREAD_STACK_SIZEOF(g_bridge_stack),
                  pw_ppp_bridge_task, NULL, NULL, NULL,
                  K_PRIO_PREEMPT(9), 0, K_NO_WAIT);
  k_thread_name_set(&g_bridge_thread, "pw_btppp");

  return 0;
}

#else  /* !PW_BT_PPP */

/* 没编进来时也留一个入口：`phywear btppp` 能给出"为什么没有"，而不是静默无事发生 */
int pw_btppp_start(void)
{
  printf("[ppp] 本固件未编入 PPP over BLE（PW_BT_PPP=0）\n");
  printf("[ppp] 原因：NuttX pppd 需要 ~16 KB 栈 + 桥 2 KB ≈ 18 KB SRAM，\n");
  printf("[ppp]       而本板在 SRAM 96.76%% 时就会开机挂死（实测悬崖）。\n");
  printf("[ppp] 详见 docs/16 §13.6e 与 docs/evidence/bt-ppp-20260918/\n");
  return -ENOSYS;
}

#endif /* PW_BT_PPP */
