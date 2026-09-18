/****************************************************************************
 * apps/examples/phywear/pw_btgatt.c
 *
 * 蓝牙阶段 B-2：注册 GATT 服务 + 开可被发现广播。
 *
 * 设计（尽量少变量、每一步都有 rc 可判）：
 *   Service  PhyWear Physics      e0f1a000-1b2c-4d5e-8f90-a1b2c3d4e5f6
 *     ├─ Sensor  READ | NOTIFY    e0f1a001-…  ← 16 B 小端传感器快照
 *     │    └─ CCC（订阅开关）
 *     └─ Command WRITE | WRITE_NR e0f1a002-…  ← 手机写命令，串口回显
 *
 * Sensor 包（16 B，全部小端）：
 *   int16 ax, ay, az   mg
 *   int16 gx, gy, gz   mdps/10（0.01 dps 单位，±327 dps 不溢出）
 *   uint16 lux         估算照度
 *   uint16 cmd_count   收到的命令条数（手机写一条就 +1，用来肉眼确认「写得进来」）
 *
 * 为什么不用「采样线程」而用 k_work_delayable：
 *   ① 采样走 pw_sensors_read_*_oneshot()，它**自己 open/close** /dev 设备，
 *      因此在哪个 task_group 里跑都合法（这一点刚在 H4 上踩过坑，见 docs/16）；
 *   ② bt_gatt_notify() 最终只走到 h4_send() → tx_fifo，不碰 fd；
 *   ③ 只在**有订阅者**时才采样，避免空转。
 ****************************************************************************/

#include <nuttx/config.h>

#include <stdio.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>

#include "phywear_sensors.h"

/* UUID 按 LSB-first 排布（BT_UUID_INIT_128 的字节序） */

static const struct bt_uuid_128 pw_svc_uuid = BT_UUID_INIT_128(
  0xf6, 0xe5, 0xd4, 0xc3, 0xb2, 0xa1, 0x90, 0x8f,
  0x5e, 0x4d, 0x2c, 0x1b, 0x00, 0xa0, 0xf1, 0xe0);

static const struct bt_uuid_128 pw_sensor_uuid = BT_UUID_INIT_128(
  0xf6, 0xe5, 0xd4, 0xc3, 0xb2, 0xa1, 0x90, 0x8f,
  0x5e, 0x4d, 0x2c, 0x1b, 0x01, 0xa0, 0xf1, 0xe0);

static const struct bt_uuid_128 pw_cmd_uuid = BT_UUID_INIT_128(
  0xf6, 0xe5, 0xd4, 0xc3, 0xb2, 0xa1, 0x90, 0x8f,
  0x5e, 0x4d, 0x2c, 0x1b, 0x02, 0xa0, 0xf1, 0xe0);

/* ── 状态 ───────────────────────────────────────────────────────────── */

struct pw_bt_pkt_s
{
  int16_t  ax, ay, az;      /* mg */
  int16_t  gx, gy, gz;      /* mdps/10 */
  uint16_t lux;
  uint16_t cmd_count;
} __attribute__((packed));

static struct pw_bt_pkt_s   g_pkt;
static uint8_t              g_nfy_on;       /* CCC 是否已订阅 */
static uint16_t             g_cmd_count;
static char                 g_last_cmd[32];
static struct k_work_delayable g_sample_work;

#define PW_SAMPLE_MS   500

/* ── GATT 回调 ──────────────────────────────────────────────────────── */

static void pw_sample_once(int strict);

/* 读的时候**现采一次**：手机点一下"读"就应拿到当下这一秒的值，
 * 而不是上一轮 notify 的旧值。（采样最坏 ~150 ms，读是用户触发的，可接受。） */
static ssize_t pw_read_sensor(struct bt_conn *conn,
                              const struct bt_gatt_attr *attr, void *buf,
                              uint16_t len, uint16_t offset)
{
  if (offset == 0)
    {
      pw_sample_once(1);
    }

  return bt_gatt_attr_read(conn, attr, buf, len, offset, &g_pkt,
                           sizeof(g_pkt));
}

static ssize_t pw_write_cmd(struct bt_conn *conn,
                            const struct bt_gatt_attr *attr, const void *buf,
                            uint16_t len, uint16_t offset, uint8_t flags)
{
  size_t n = len;

  (void)conn;
  (void)attr;
  (void)flags;

  if (offset != 0)
    {
      return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
    }

  if (n > sizeof(g_last_cmd) - 1)
    {
      n = sizeof(g_last_cmd) - 1;
    }

  memcpy(g_last_cmd, buf, n);
  g_last_cmd[n] = '\0';
  g_cmd_count++;
  g_pkt.cmd_count = g_cmd_count;

  printf("[bt] cmd #%u (%u B): '%s'\n", (unsigned)g_cmd_count, (unsigned)len,
         g_last_cmd);

  /* 约定的最小命令集（手机侧写 ASCII） */

  if (strcmp(g_last_cmd, "notify") == 0)
    {
      g_nfy_on = 1;
      printf("[bt] cmd: notify forced on\n");
    }
  else if (strcmp(g_last_cmd, "ping") == 0)
    {
      printf("[bt] cmd: pong\n");
    }
  else if (strcmp(g_last_cmd, "selftest") == 0)
    {
      printf("[bt] cmd: selftest ok (设备侧自检，非手机写入)\n");
    }

  return len;
}

static void pw_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
  (void)attr;
  g_nfy_on = (value & BT_GATT_CCC_NOTIFY) ? 1 : 0;
  printf("[bt] ccc changed -> notify %s\n", g_nfy_on ? "ON" : "OFF");
}

/* 属性表：0=service, 1=chrc decl, 2=chrc value(Sensor), 3=ccc, 4=chrc decl,
 *         5=chrc value(Command) */

static struct bt_gatt_attr pw_attrs[] =
{
  BT_GATT_PRIMARY_SERVICE(&pw_svc_uuid.uuid),

  BT_GATT_CHARACTERISTIC(&pw_sensor_uuid.uuid,
                         BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
                         BT_GATT_PERM_READ,
                         pw_read_sensor, NULL, &g_pkt),
  BT_GATT_CCC(pw_ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),

  BT_GATT_CHARACTERISTIC(&pw_cmd_uuid.uuid,
                         BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP,
                         BT_GATT_PERM_WRITE,
                         NULL, pw_write_cmd, NULL),
};

static struct bt_gatt_service pw_svc = BT_GATT_SERVICE(pw_attrs);

#define PW_ATTR_SENSOR_VALUE   2

/* ── 采样 + 通知 ────────────────────────────────────────────────────── */

/* 静止手表的三轴合矢量必然 ~1 g，用它当"这一读是不是垃圾"的判据：
 * pw_sensors_read_imu_oneshot() 只要 accel 非全 0 就接受，而它自己的注释就写了
 * "FIFO 刚重启时第一次常拿到全 0"，所以这里再加一道合矢量合理性检查。
 * （2026-09-18：服务端自检第一次跑出 |a|≈15.4 g，一度以为是传感器瞬态，
 *  实为本次重构把 IMU 读取整行删掉、读了未初始化的栈变量 —— 自检把这个自伤
 *  抓了出来。修好后实测 |a|≈1000 mg。） */
#define PW_ACC_MIN_MG   300
#define PW_ACC_MAX_MG   3000
#define PW_ACC_TRIES    4

/* strict 路径（GATT 读 / 自检）的总时间预算，单位 ms。
 * 为什么要有预算：这些回调跑在 Zephyr 系统工作队列线程上，而
 * pw_sensors_read_imu_oneshot() **一次**最坏就 ~160 ms（内部 3 次读 + START + 5 次读）。
 * 只按"次数"限（4 次）⇒ 最坏 ~640 ms 把 HCI 收包处理与命令 TX 全按住，
 * 手机连着时可能表现成卡顿甚至掉线。改成按**时间**限：
 *   - 最多 PW_ACC_TRIES 次；
 *   - 且只有"剩余预算还够再来一次"时才重试（PW_ACC_MIN_RETRY_MS）。
 * 拿不到就保留上一个好值（不更新 g_pkt），绝不为了一个读数把 BT 按住。
 * 注：单次 oneshot 自身的耗时无法从外面切断，所以下界就是一次调用的量级。 */
#define PW_SAMPLE_BUDGET_MS     200
#define PW_ACC_MIN_RETRY_MS     100

static int pw_imu_mag_ok(const struct pw_imu_s *imu)
{
  long ax = imu->ax;
  long ay = imu->ay;
  long az = imu->az;
  long mag2 = ax * ax + ay * ay + az * az;

  return mag2 >= (long)PW_ACC_MIN_MG * PW_ACC_MIN_MG &&
         mag2 <= (long)PW_ACC_MAX_MG * PW_ACC_MAX_MG;
}

/* 采样一次。
 *
 * `strict` 决定"要不要为了拿一个好样本反复重试" —— 这一点很关键，因为
 * **GATT 回调和周期通知都跑在 Zephyr 系统工作队列线程上**（bt_recv →
 * k_work_submit(&hdev->rx_work) → rx_work_handler，栈 4096 B，优先级 110）。
 * 而 pw_sensors_read_imu_oneshot() 内部最坏要走
 * 「3 次读 + START + 5 次读」≈160 ms。如果在这里再套一层重试（原来 6 次），
 * 最坏能把工作队列堵 ~1 秒 —— 会连带推迟 HCI 命令 TX 与所有收到的包的处理。
 *
 *   strict=1（GATT 读 / 自检）：用户主动触发，值得等 ⇒ 最多 PW_ACC_TRIES 次，
 *                               拿不到合理的就报 WARN；
 *   strict=0（500 ms 周期通知）：**只读一次**，样本不合理就**不更新** g_pkt
 *                               （保留上一个好值），绝不为了通知去堵队列。
 */
static void pw_sample_once(int strict)
{
  struct pw_imu_s imu;
  struct pw_light_s light;
  int tries = strict ? PW_ACC_TRIES : 1;
  int64_t deadline = k_uptime_get() + PW_SAMPLE_BUDGET_MS;
  int i;
  int ok = 0;

  memset(&imu, 0, sizeof(imu));

  for (i = 0; i < tries && !ok; i++)
    {
      if (pw_sensors_read_imu_oneshot(&imu) == 0 && pw_imu_mag_ok(&imu))
        {
          ok = 1;
          break;
        }

      /* 还有没有"再来一次"的预算？没有就立刻收手。 */
      if (!strict || k_uptime_get() + PW_ACC_MIN_RETRY_MS > deadline)
        {
          break;
        }

      usleep(20000);
    }

  if (ok)
    {
      g_pkt.ax = (int16_t)imu.ax;
      g_pkt.ay = (int16_t)imu.ay;
      g_pkt.az = (int16_t)imu.az;
      g_pkt.gx = (int16_t)(imu.gx / 10);
      g_pkt.gy = (int16_t)(imu.gy / 10);
      g_pkt.gz = (int16_t)(imu.gz / 10);
    }
  else if (strict)
    {
      printf("[bt] imu WARN %d 次都没有合矢量合理的样本\n", tries);
    }

  if (pw_sensors_read_light_oneshot(&light) == 0)
    {
      g_pkt.lux = (uint16_t)(light.lux < 0 ? 0 : light.lux);
    }
}

static void pw_sample_work_handler(struct k_work *work)
{
  (void)work;

  /* 没有订阅者就不采样 —— 别为了没人看的数据每 500 ms 去开一次 /dev 传感器。
   * 有人读时会由 pw_read_sensor() 现采。 */
  if (g_nfy_on)
    {
      pw_sample_once(0);
    }

  if (g_nfy_on)
    {
      /* 诊断开关：为 1 时**每次**通知都打印 rc（排查"订阅了但收不到包"）；
       * 平时置 0，只在出错时打一行。
       * 2026-09-18：这个开关正是抓到"设备侧 rc=0、手机侧收不到"的功臣 ——
       * 顺着它打开 h4.c 的 PW_H4_TRACE，才在空口原文里看到通知帧的
       * value handle 是 0x0000（zblue gatt_notify_mc 漏 data.handle）。
       * 根因已修（见 docs/evidence/bt-gatt-handle-20260918/），开关归 0。 */
#define PW_BT_NOTIFY_TRACE 0
      static unsigned int nfy_tick;
      int rc = bt_gatt_notify(NULL, &pw_attrs[PW_ATTR_SENSOR_VALUE], &g_pkt,
                              sizeof(g_pkt));

      nfy_tick++;
#if PW_BT_NOTIFY_TRACE
      printf("[bt] notify #%u rc=%d len=%u\n", nfy_tick, rc,
             (unsigned)sizeof(g_pkt));
#else
      if (rc && rc != -ENOTCONN)
        {
          printf("[bt] notify #%u rc=%d\n", nfy_tick, rc);
        }
#endif
    }

  k_work_reschedule(&g_sample_work, K_MSEC(PW_SAMPLE_MS));
}

/* ── 连接事件 ──────────────────────────────────────────────────────────
 * B3 是**人工**验证（手机连），所以设备侧必须把"连上了/断了"打到串口上，
 * 否则只能靠手机屏幕，串口证据链是空的。 */

static void pw_connected(struct bt_conn *conn, uint8_t err)
{
  char addr[BT_ADDR_LE_STR_LEN];

  bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
  printf("[bt] B3 connected: %s err=%u\n", addr, err);
  g_nfy_on = 0;
}

static void pw_disconnected(struct bt_conn *conn, uint8_t reason)
{
  char addr[BT_ADDR_LE_STR_LEN];

  bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
  printf("[bt] B3 disconnected: %s reason=0x%02x\n", addr, reason);
  g_nfy_on = 0;
}

static struct bt_conn_cb pw_conn_cb =
{
  .connected = pw_connected,
  .disconnected = pw_disconnected,
};

/* ── 广播数据 ───────────────────────────────────────────────────────── */

static const struct bt_data pw_ad[] =
{
  BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
  BT_DATA(BT_DATA_NAME_COMPLETE, "PhyWear", 7),
};

static const struct bt_data pw_sd[] =
{
  BT_DATA_BYTES(BT_DATA_UUID128_ALL,
                0xf6, 0xe5, 0xd4, 0xc3, 0xb2, 0xa1, 0x90, 0x8f,
                0x5e, 0x4d, 0x2c, 0x1b, 0x00, 0xa0, 0xf1, 0xe0),
};

/* ── 服务端自检（B3 的可自动化部分）──────────────────────────────────
 * B3 的"空口"那一段必须有真实 BLE 中心设备（手机/蓝牙棒）才能验，
 * 但**服务端数据库 + 读/写回调**这一段不需要空口就能证明。
 * 本函数把这一段全部走一遍（用的就是手机连上后会走的那几个函数）：
 *   ① 逐条打印属性表（UUID / 权限 / read / write 回调是否存在）
 *   ② 采样一次真传感器，再走 bt_gatt_attr_read() 把 16 B 读出来并解析
 *   ③ 走一遍命令写回调（等价于手机往 …a002 写 "ping"）
 * 输出前缀统一 [bt] SELFTEST，便于脚本判定。 */

static int pw_btgatt_selftest(void)
{
  uint8_t buf[32];
  ssize_t n;
  size_t i;
  int fails = 0;

  printf("[bt] SELFTEST start (服务端自检，不含空口)\n");

  for (i = 0; i < ARRAY_SIZE(pw_attrs); i++)
    {
      const struct bt_gatt_attr *a = &pw_attrs[i];
      char u[BT_UUID_STR_LEN];

      bt_uuid_to_str(a->uuid, u, sizeof(u));
      printf("[bt] SELFTEST attr[%u] handle=0x%04x %s perm=0x%02x read=%c write=%c\n",
             (unsigned)i, a->handle, u, a->perm, a->read ? 'Y' : '-',
             a->write ? 'Y' : '-');
    }

  /* ①b 句柄结构自检。
   * 为什么只看 UUID/权限不够：`handle` 是 `bt_gatt_service_register()` **注册时**
   * 才写进属性的。若注册没真正生效，handle 会是 0 —— 那样手机来的 ATT 请求
   * 会全部找不到东西，而日志上"UUID/权限都对"看不出任何异常。
   * 另外通知能发出去的前提是：**CCC 紧跟在特征值后面**（标准四段布局
   * service-decl / chrc-decl / chrc-value / CCC），zblue 的 notify 正是靠
   * `bt_gatt_attr_value_handle()` 拿到值句柄、再据它找到对应的 CCC。
   * 这里把这两条都断言下来。 */
  {
    int all_nonzero = 1;

    for (i = 0; i < ARRAY_SIZE(pw_attrs); i++)
      {
        if (pw_attrs[i].handle == 0)
          {
            all_nonzero = 0;
          }
      }

    printf("[bt] SELFTEST handles: value=0x%04x ccc=0x%04x (ccc 应为 value+1)\n",
           pw_attrs[PW_ATTR_SENSOR_VALUE].handle,
           pw_attrs[PW_ATTR_SENSOR_VALUE + 1].handle);

    if (!all_nonzero)
      {
        printf("[bt] SELFTEST FAIL 有属性 handle=0 ⇒ 服务没真正注册\n");
        fails++;
      }

    if (pw_attrs[PW_ATTR_SENSOR_VALUE + 1].handle !=
        pw_attrs[PW_ATTR_SENSOR_VALUE].handle + 1)
      {
        printf("[bt] SELFTEST FAIL CCC 不紧跟特征值 ⇒ 通知可能发不出去\n");
        fails++;
      }
  }

  /* ② 读路径：先采样，再走真实 read helper。
   * 这里顺便把采样耗时**量出来**打印 —— 「预算 200 ms」不能只是注释里的一句话，
   * 得在真机日志里有数（这个回调与周期通知都在 BT 工作队列线程上）。 */
  {
    int64_t t0 = k_uptime_get();

    pw_sample_once(1);
    printf("[bt] SELFTEST sample took %d ms (budget %d ms)\n",
           (int)(k_uptime_get() - t0), PW_SAMPLE_BUDGET_MS);
  }

  {
    /* lux 单独报一次 rc：否则 g_pkt.lux==0 分不清"真的 0"还是"读失败" */
    struct pw_light_s l;

    memset(&l, 0, sizeof(l));
    int lrc = pw_sensors_read_light_oneshot(&l);

    printf("[bt] SELFTEST light rc=%d lux=%d ch0=%d ch1=%d\n", lrc, l.lux,
           l.ch0, l.ch1);
  }

  memset(buf, 0, sizeof(buf));
  n = bt_gatt_attr_read(NULL, &pw_attrs[PW_ATTR_SENSOR_VALUE], buf,
                        sizeof(buf), 0, &g_pkt, sizeof(g_pkt));
  printf("[bt] SELFTEST read sensor -> %d B (want %u)\n", (int)n,
         (unsigned)sizeof(g_pkt));
  if (n == (ssize_t)sizeof(g_pkt))
    {
      int16_t ax = (int16_t)(buf[0] | (buf[1] << 8));
      int16_t ay = (int16_t)(buf[2] | (buf[3] << 8));
      int16_t az = (int16_t)(buf[4] | (buf[5] << 8));
      uint16_t lux = (uint16_t)(buf[12] | (buf[13] << 8));
      long mag = 0;
      long t = (long)ax * ax + (long)ay * ay + (long)az * az;

      while (mag * mag < t)
        {
          mag++;
        }

      printf("[bt] SELFTEST decode ax=%d ay=%d az=%d mg |a|=%ld mg lux=%u\n",
             ax, ay, az, mag, (unsigned)lux);
      if (mag < 300 || mag > 3000)
        {
          printf("[bt] SELFTEST FAIL 合矢量 %ld mg 不合理（静止应 ~1000 mg）\n",
                 mag);
          fails++;
        }
    }
  else
    {
      fails++;
    }

  /* ③ 写路径：等价于手机往命令特征写一条命令。
   * 用 "selftest" 这个**专用**命令名，避免与人工手机写入（比如 "ping"）混淆 ——
   * B3 判定器就是靠区分这两者才不会误判"手机写进来了"。 */
  printf("[bt] SELFTEST write cmd \"selftest\" ->\n");
  n = pw_write_cmd(NULL, &pw_attrs[5], "selftest", 8, 0, 0);
  printf("[bt] SELFTEST write ret=%d (want 8)\n", (int)n);
  if (n != 8 || g_cmd_count == 0)
    {
      fails++;
    }

  /* ④ 通知路径演练。
   * 为什么需要：这条链（CCC 订阅 → 周期采样 → bt_gatt_notify）**只在有订阅者时**
   * 才会跑，实验室里没有手机 ⇒ 它从来没被执行过；万一它坏在 B3 现场，表现就是
   * "手机订阅了但一个包都收不到"，而且没有任何日志。
   * 这里直接调一次 bt_gatt_notify()：
   *   - 没有订阅者时，zblue 的 gatt_notify_mc() 初值就是 -ENOTCONN（已核对源码
   *     host/gatt.c:3053），notify_cb 找不到 cfg->value == NOTIFY 的订阅者就不会改它；
   *   - 手机订阅之后，**同一处的 rc 应该变成 0** —— 这就是 B3 要看的那条判据。
   * 这一步证明"属性句柄能解析 + 调用链通"，并把期望值钉在日志里。 */
  {
    int nrc;

    g_nfy_on = 1;
    pw_sample_once(1);
    nrc = bt_gatt_notify(NULL, &pw_attrs[PW_ATTR_SENSOR_VALUE], &g_pkt,
                         sizeof(g_pkt));
    g_nfy_on = 0;

    printf("[bt] SELFTEST notify(无订阅者) rc=%d (期望 %d=-ENOTCONN；"
           "手机订阅后这里应变成 0)\n", nrc, -ENOTCONN);
    if (nrc != -ENOTCONN)
      {
        fails++;
      }
  }

  printf("[bt] SELFTEST %s (fails=%d)\n", fails == 0 ? "PASS" : "FAIL", fails);
  return fails;
}

/* ── 对外入口 ───────────────────────────────────────────────────────── */

int pw_btgatt_start(void)
{
  bt_addr_le_t addrs[1];
  size_t count = 1;
  char addr[18];
  int rc;

  memset(&g_pkt, 0, sizeof(g_pkt));

  rc = bt_gatt_service_register(&pw_svc);
  printf("[bt] B2 gatt register rc=%d %s\n", rc, rc == 0 ? "(ok)" : "(FAILED)");
  if (rc)
    {
      return rc;
    }

  k_work_init_delayable(&g_sample_work, pw_sample_work_handler);

  rc = bt_conn_cb_register(&pw_conn_cb);
  printf("[bt] B2 conn cb register rc=%d\n", rc);

  /* ⚠️ 广播数据里我们写的是 "PhyWear"，但 **GAP 的 Device Name 特性**取的是
   * `CONFIG_BT_DEVICE_NAME`（本板默认 "Zephyr"）—— 两者不一致时：
   * 手机首次扫描看到 "PhyWear"，连上后读名字（或系统缓存）就变成 "Zephyr"。
   * **实测（2026-09-18，宿主 BlueZ）**：断开重连后设备名显示为 'Zephyr'，
   * 就是踩了这个坑。这里把 GAP 名字也设成 "PhyWear"。
   * （需要 CONFIG_BT_DEVICE_NAME_DYNAMIC=y，本板已开。） */
  rc = bt_set_name("PhyWear");
  printf("[bt] B2 set_name \"PhyWear\" rc=%d\n", rc);

  rc = bt_le_adv_start(BT_LE_ADV_CONN, pw_ad, ARRAY_SIZE(pw_ad), pw_sd,
                       ARRAY_SIZE(pw_sd));
  printf("[bt] B2 adv start rc=%d %s\n", rc, rc == 0 ? "(ok)" : "(FAILED)");
  if (rc)
    {
      return rc;
    }

  bt_id_get(addrs, &count);
  if (count > 0)
    {
      snprintf(addr, sizeof(addr), "%02X:%02X:%02X:%02X:%02X:%02X",
               addrs[0].a.val[5], addrs[0].a.val[4], addrs[0].a.val[3],
               addrs[0].a.val[2], addrs[0].a.val[1], addrs[0].a.val[0]);
    }
  else
    {
      snprintf(addr, sizeof(addr), "??");
    }

  /* 别把 identity 地址当成"广播地址"：实测 H4 原文里
   * LE Set Extended Advertising Parameters 的 own_addr_type = 0x01(RANDOM)，
   * 且先发了 LE Set Advertising Set Random Address —— 手机上看到的是**随机地址**，
   * 按 MAC 找会找不到，必须按名字找。（所以这里同时打印 identity 供对照。） */
  printf("[bt] B2 READY name=PhyWear identity=%s\n", addr);
  printf("[bt] B2 NOTE 广播用随机地址(EXT_ADV+PRIVACY)：手机请按名字找，别按 MAC\n");
  printf("[bt] B2 svc e0f1a000-1b2c-4d5e-8f90-a1b2c3d4e5f6\n");
  printf("[bt] B2   sensor e0f1a001 READ|NOTIFY (16B) / cmd e0f1a002 WRITE\n");

  k_work_reschedule(&g_sample_work, K_MSEC(PW_SAMPLE_MS));

  pw_btgatt_selftest();
  return 0;
}
