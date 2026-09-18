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

static ssize_t pw_read_sensor(struct bt_conn *conn,
                              const struct bt_gatt_attr *attr, void *buf,
                              uint16_t len, uint16_t offset)
{
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
#define PW_ACC_TRIES    6

static int pw_imu_mag_ok(const struct pw_imu_s *imu)
{
  long ax = imu->ax;
  long ay = imu->ay;
  long az = imu->az;
  long mag2 = ax * ax + ay * ay + az * az;

  return mag2 >= (long)PW_ACC_MIN_MG * PW_ACC_MIN_MG &&
         mag2 <= (long)PW_ACC_MAX_MG * PW_ACC_MAX_MG;
}

static void pw_sample_once(void)
{
  struct pw_imu_s imu;
  struct pw_light_s light;
  int i;
  int ok = 0;

  memset(&imu, 0, sizeof(imu));

  for (i = 0; i < PW_ACC_TRIES && !ok; i++)
    {
      if (pw_sensors_read_imu_oneshot(&imu) == 0 && pw_imu_mag_ok(&imu))
        {
          ok = 1;
        }
      else if (i + 1 < PW_ACC_TRIES)
        {
          usleep(25000);
        }
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
  else
    {
      printf("[bt] imu WARN 连续 %d 次都没有合矢量合理的样本\n", PW_ACC_TRIES);
    }

  if (pw_sensors_read_light_oneshot(&light) == 0)
    {
      g_pkt.lux = (uint16_t)(light.lux < 0 ? 0 : light.lux);
    }
}

static void pw_sample_work_handler(struct k_work *work)
{
  (void)work;

  pw_sample_once();

  if (g_nfy_on)
    {
      int rc = bt_gatt_notify(NULL, &pw_attrs[PW_ATTR_SENSOR_VALUE], &g_pkt,
                              sizeof(g_pkt));
      if (rc && rc != -ENOTCONN)
        {
          printf("[bt] notify rc=%d\n", rc);
        }
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
      printf("[bt] SELFTEST attr[%u] %s perm=0x%02x read=%c write=%c\n",
             (unsigned)i, u, a->perm, a->read ? 'Y' : '-',
             a->write ? 'Y' : '-');
    }

  /* ② 读路径：先采样，再走真实 read helper */
  pw_sample_once();

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

  /* ③ 写路径：等价于手机往命令特征写 "ping" */
  printf("[bt] SELFTEST write cmd \"ping\" ->\n");
  n = pw_write_cmd(NULL, &pw_attrs[5], "ping", 4, 0, 0);
  printf("[bt] SELFTEST write ret=%d (want 4)\n", (int)n);
  if (n != 4 || g_cmd_count == 0)
    {
      fails++;
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
