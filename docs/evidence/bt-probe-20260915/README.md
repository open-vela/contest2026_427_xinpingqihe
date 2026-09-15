# ④ 蓝牙探针（时间盒实验，2026-09-15）—— 结论：**暂不可交付（no-go），但差的是"构建接线"不是硬件**

> 用户问过"蓝牙是否可以提上日程"。本目录是**时间盒探针**的结论：**能不能起来**、
> 差在哪、要多少代价 —— 不是交付。全部结论都有命令与文件位置可复核。

---

## 1. 结论速览

| 结论 | 证据 |
|---|---|
| **片上蓝牙控制器是有的**（LCPU），不需要外挂固件 | `vendor/sifli/chips/sf32lb52/sf32lb52_bth4.c` 里 `lcpu_power_on()`、`sf32lb52_hci_register_callback()`；且 `vendor/sifli` 下**没有任何** BT controller 固件 `.bin`（本来也不需要） |
| **BT 主机栈在配置里是打开的** | `.config` 有 `CONFIG_BT_H4=y`、`CONFIG_BT_HCI_HOST=y`、`CONFIG_BT_CLASSIC=y`、`CONFIG_BT_EXT_ADV=y`、`CONFIG_BT_UART_ON_DEV_NAME="/dev/ttyHCI0"`，共 **107** 个 `CONFIG_BT*/BLUETOOTH*` 项 |
| **但固件里几乎没有 BT 代码** | 基线固件 `nm nuttx \| grep -ci "bt_\|bluetooth"` = **2** |
| **根因 1：`UART_BTH4` 没开** | defconfig 里 `# CONFIG_UART_BTH4 is not set`；板级 `sf32lb52_bt_initialize()` 正是 `#ifdef CONFIG_UART_BTH4` 才被调用 → `/dev/ttyHCI0` 从不注册 |
| **根因 2：NuttX 的 BT socket 层被 gated 掉** | `net/bluetooth/CMakeLists.txt` 全部源码在 `if(CONFIG_NET_BLUETOOTH)` 内，而该符号（及其依赖 `WIRELESS_BLUETOOTH`）未开 → **一个 BT 目标文件都没编** |
| **根因 3（决定性）：端口自己的 H4 驱动没有任何 CMakeLists 编它** | 驱动源码在 `external/zblue/zblue/port/drivers/bluetooth/hci/h4_uart.c`（用 `CONFIG_BT_UART_H4_ON_DEV_NAME`），但 `port/drivers/bluetooth/hci/` **目录下没有 CMakeLists.txt**；而 `port/sections/defines.c:543` 已经声明了 `__init___device_dts_ord_..._zephyr_bt_hci_ttyHCI0/1_ORD` → **声明了没人实现，必然链接失败** |

**探针实测**：把 `CONFIG_UART_BTH4=y` 加进 defconfig 后，设备树会生成 `zephyr_bt_hci_ttyHCI0/ttyHCI1` 节点，但链接在最后一步报：

```
apps/external/zblue/libzblue.a(defines.c.o): undefined reference to
  `__init___device_dts_ord_DT_N_INST_0_zephyr_bt_hci_ttyHCI0_ORD'
```

> 注：`external/zblue/zblue/drivers/bluetooth/hci/h4.c`（上游那份，`DT_DRV_COMPAT zephyr_bt_hci_uart`）
> 虽然存在，但它**不匹配**本端口的设备树节点（节点是 `zephyr_bt_hci_ttyHCI`）；我曾经把这一份
> 加进 CMake 试过，仍然缺符号（因为匹配不上），已回退。真正该编的是 `port/` 下那份 `h4_uart.c`。

---

## 2. 探针的边界（我做了什么、没做什么）

**做了**（都在工作区，**已全部回退并验证复原**）：
1. 记录基线固件（`md5 9ed0d480…`，2,076,244 B）；
2. 在 `nsh-ai` defconfig 追加 `CONFIG_WIRELESS_BLUETOOTH=y / CONFIG_NET_BLUETOOTH=y / CONFIG_UART_BTH4=y`；
3. `rm .config` + 重新 configure + 全量构建 → 得到上表的链接失败；
4. 试补 `h4.c` 到上游 CMake → 仍缺符号 → 回退；
5. 回退 defconfig 与 CMake，重新 configure + 构建，**用 md5 验证固件回到基线**。

**没做**（超出时间盒，需要用户先定）：
- 给 `port/drivers/bluetooth/hci/` 写 CMakeLists、打开 `CONFIG_BT_UART_H4_*`、处理 zblue 的
  `net_buf` 静态池内存（见下）、真机跑 HCI 命令、写 GATT 服务；
- 因此**没有任何"蓝牙能用"的结论**，也没有证明 LCPU 控制器一定会应答。

---

## 3. 如果要做，代价与风险（估）

| 项 | 说明 |
|---|---|
| 工作量 | **0.5~1.5 天**到"能在手表上跑通 HCI reset/scan"；再做 GATT 服务与手机侧对接另算 |
| SRAM（**最大风险**） | zblue 的 `net_buf` 池是**静态数组**（`NET_BUF_POOL_DEFINE`），没有 PSRAM 支持。本板 SRAM 已用 **93.7%**（探针前累计已超"不超过 ~2 KB"额度），开 BT 极可能直接**链接失败**（SRAM 100% 会链接报错，`.config` 里 `CONFIG_BT_*_TX_STACK_SIZE`、RX/TX 线程栈都要吃 SRAM） |
| 已知坑 | ① 该 defconfig 由 CMake 列表解析，**行内注释里的引号/全角括号会让 configure 直接报 `string sub-command REGEX ... needs at least 5 arguments`**（本次踩到，去掉注释即恢复）；② `CONFIG_BT_DEVICE_NAME="Zephyr"`，手机扫描会看到 "Zephyr"，要改；③ `CONFIG_BT_CLASSIC=y`（BR/EDR）对穿戴设备基本无用，先关掉省资源 |
| 是否需要外挂控制器固件 | **不需要**（控制器在片内 LCPU），但**未经验证**：探针没走到"发一条 HCI 命令看回答"这一步 |

---

## 4. 给用户的建议

**建议：不在本轮排期蓝牙。** 理由：
1. 它卡在**构建接线 + SRAM**两道关，不是"配置一开就有"；
2. 当前 SRAM 已 93.7% 且**已超用户给的 ~2 KB 新增额度**，BT 的静态 `net_buf` 池大概率直接放不下；
3. 赛道评分里"主动 + 执行"（⑤-1 已交付）与"演示视频"的权重明显更高，而 ③ 的 UI 也还没落地。

**要重启它需要用户先定两件事**：① SRAM 额度是否放宽（或接受关掉某些现有功能换 BT）；
② 是否接受 0.5~1.5 天投入只为"能跑 HCI"（不含 GATT 业务）。

复现探针（不要在生产固件上做）：

```bash
cd ~/openvela
cp vendor/sifli/boards/sf32lb52/sf32lb52_lchspi_ulp/configs/nsh-ai/defconfig /tmp/defconfig.bak
printf 'CONFIG_WIRELESS_BLUETOOTH=y\nCONFIG_NET_BLUETOOTH=y\nCONFIG_UART_BTH4=y\n' >> \
  vendor/sifli/boards/sf32lb52/sf32lb52_lchspi_ulp/configs/nsh-ai/defconfig
rm -f cmake_out/sf32lb52_lchspi_ulp_nsh_ai/.config
cmake -B cmake_out/sf32lb52_lchspi_ulp_nsh_ai -S nuttx -GNinja \
  -DBOARD_CONFIG=../vendor/sifli/boards/sf32lb52/sf32lb52_lchspi_ulp/configs/nsh-ai \
  -DEXTRA_FLAGS="-Wno-cpp -Wno-deprecated-declarations"
cmake --build cmake_out/sf32lb52_lchspi_ulp_nsh_ai -j16 2>&1 | tail -20   # 预期：ttyHCI0 init 未定义
# 复原：cp /tmp/defconfig.bak <defconfig>; rm .config; 重新 configure + build；核对 md5
```
