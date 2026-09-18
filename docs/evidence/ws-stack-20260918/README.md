# 证据：把 NuttX hpwork/lpwork 栈从 16096 B 收到 8192 B（−15,808 B SRAM）

日期：2026-09-18 深夜
被测：PhyWear 手表（SF32LB52-MOD-1-N16R8）

## 0. 为什么动这里

任务 3 的 PPP over BLE 需要约 18~21 KB SRAM，而当时 SRAM 已到 **94.69%**、
实测悬崖在 **95.98%（能开机）↔ 96.76%（挂死）** 之间 —— 也就是只剩 4~5 KB。
按 `arm-none-eabi-nm --print-size --size-sort` 排 SRAM 大户时发现：

```
  16096  b  g_lp_work_stack.0     ← NuttX lpwork 栈
  16096  b  g_hp_work_stack.1     ← NuttX hpwork 栈
```

两笔都是 **16,096 B**，而 NuttX 默认只有 **2,048 B**。查来源：
`vendor/sifli/.../nsh-ai/defconfig` 里只有 `CONFIG_DEFAULT_TASK_STACKSIZE=16096`，
而 `nuttx/sched/Kconfig` 里 `SCHED_HPWORKSTACKSIZE`/`SCHED_LPWORKSTACKSIZE`
的 default 就是 `DEFAULT_TASK_STACKSIZE` —— 也就是说，**这两条栈从没被有意识地配过**，
只是继承了板级那个偏大的默认值。

## 1. 改法与实测数字（真机烧录 + 复位，构建输出为证）

```
CONFIG_SCHED_HPWORKSTACKSIZE=8192
CONFIG_SCHED_LPWORKSTACKSIZE=8192
```

| 配置 | SRAM | 结果 |
|---|---|---|
| 改动前 | 496,452 B（94.69%） | ✅ 正常开机 |
| 改动后 | **480,644 B（91.68%）** | ✅ 正常开机，**−15,808 B** |

## 2. 8 KB 够不够 —— 用**完整负载**验，不是"看起来够"

hpwork/lpwork 上跑的东西在本固件里是明确的（`grep work_queue(HPWORK|LPWORK)`）：

* `vendor/sifli/chips/sf32lb52/sf32lb52_bt_adapter.c` —— **BT HCI 接收 worker**
* `vendor/sifli/boards/sf32lb52/drivers/input/ft6146.c` —— **触摸屏 worker**
* 其余命中的都是本固件没编进来的驱动（e1000 / igb / bcmf / ieee802154 等）

所以"压这两条栈"的风险面 = BT 数据面 + 触摸屏。验证方式是把这个风险面**跑满**：

| 验证 | 结果 | 证据 |
|---|---|---|
| 设备侧一键验收（含 BT 服务端自检、采样、notify 演练、60 s 浸泡） | **18/18 PASS** | `docs/evidence/accept-20260918-ppp2/SUMMARY.txt` |
| 宿主当 BLE 中心设备做**双向文本往返**（压 HCI 接收 worker） | 设备侧 8/8 + 宿主侧 9/9 | 本目录 `b3.log` / `central.log`（`echo: hello-…` 成立） |
| GUI 主页截图 sha256 | 与金标 `e356689d…` **逐字节相同** | `accept-20260918-ppp2/shot/root.png` |
| 同口径帧率（30 s 心跳） | loops/s 196，**fps 12 不变** | `accept-20260918-ppp2/R1_fps.log` |
| 60 s 浸泡 | 无 panic / 断言 / 重启 | `accept-20260918-ppp2/R3_soak.log` |
| **触摸屏 worker**（同一队列） | ⚠️ **未验证** —— 自动脚本造不出真实触摸事件 | 需人工在屏上点几下确认（`ft6146_data_worker` 只做一次 ~6 B I2C 读 + `touch_event()`，8 KB 是 NuttX 默认 2 KB 的 4 倍） |

⇒ **如实定性**：BT 数据面这条路已经压满并验证通过；**触摸这条只做了代码级判断，没有实测**，
列为残余风险（人工点一下即可关闭，见 `docs/07` 坑 43 的"怎么验"）。

## 3. 复核

```bash
cd docs/evidence/ws-stack-20260918 && sha256sum -c SHA256SUMS.txt
grep -a "SELFTEST\|echo:" b3.log | tail -20      # 文本双向往返
grep -a "PASS\|FAIL" central.log                 # 宿主侧中心设备判定
```
