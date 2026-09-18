#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pw_bt_ppp.py —— 宿主侧：把 BLE 与一个 PTY 接起来，给 pppd 用

板子侧（`phywear btppp`）已经提供两个 GATT 特征做**字节管道**：
    …a101  WRITE  主机 → 设备（PPP 字节）
    …a102  NOTIFY 设备 → 主机（PPP 字节）
本脚本把它们接到一个伪终端（PTY）上，于是宿主的 `pppd` 就能像接串口一样
在这个 PTY 上跑起来 —— 两端的 PPP 一握手，手表就拿到 IP。

用法（**两步，第二步需要 root**）：
    # ① 本脚本（普通用户即可）：连手表、建 PTY、开始搬运字节
    python3 tools/phywear/pw_bt_ppp.py
    #    它会打印 /dev/pts/N
    # ② 另开一个终端，用 pppd 在同一个 PTY 上起对端（需要 sudo）
    sudo pppd /dev/pts/N 115200 noauth 192.168.7.1:192.168.7.2 local nodetach

    之后在**板子**上：
    ifconfig                     # 应出现 ppp0 且拿到 192.168.7.2
    ping 192.168.7.1             # 通宿主

⚠️ 前置条件（2026-09-18 实测，务必先看）：
    本板 SRAM 余量只有约 4~5 KB，而 NuttX 的 pppd 需要 ~16 KB 栈
    ⇒ **当前出货固件的 PW_BT_PPP=0（没编进 PPP）**，`phywear btppp` 只会
    打印原因。要真跑起来，得先腾出约 18 KB SRAM 再把该宏置 1。
    详见 docs/16 §13.6e 与 docs/evidence/bt-ppp-20260918/README.md。

为什么用 GATT 而不是 L2CAP CoC：CoC 吞吐更好，但 Linux 用户态走 LE CoC 的
socket API 很别扭；而 GATT 这条路已有现成且验证过的实现。ATT MTU 协商到 517
⇒ 单次可带 ~514 B，跑 LCP/IPCP/ping 足够（CoC 留作后续优化）。
"""

import argparse
import errno
import os
import pty
import re
import select
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pw_ble_central as C  # noqa: E402

PPP_RX_UUID = "e0f1a101-1b2c-4d5e-8f90-a1b2c3d4e5f6"
PPP_TX_UUID = "e0f1a102-1b2c-4d5e-8f90-a1b2c3d4e5f6"


def log(m):
    print(f"[host-ppp] {m}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="PhyWear")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--chunk", type=int, default=0,
                    help="每次 GATT 写的字节数；0 = 按协商 MTU 自动（MTU-3）")
    args = ap.parse_args()

    c = C.Central(args.name, args.timeout)
    if not c.find_adapter():
        return 2

    dev = c.scan_for()
    if not dev:
        log("没扫到手表 —— 板子上先跑过 `phywear btppp` 了吗？")
        return 1

    if not c.connect(dev):
        log("连接失败")
        return 1

    svc, chrs = c.gatt(dev)
    rx = chrs.get(PPP_RX_UUID)
    tx = chrs.get(PPP_TX_UUID)
    if not rx or not tx:
        log("没找到 PPP 特征（…a101/…a102）—— 说明固件里 PW_BT_PPP=0 "
            "或服务没注册成功。板子侧 `phywear btppp` 会打印原因。")
        return 1

    # ATT MTU 从 …a003 的状态串里读（设备会回真实协商值），据此定分片大小
    chunk = args.chunk
    if chunk <= 0:
        chunk = 180                      # 保守默认（MTU 23 时为 20，这里会再夹）
        s3 = chrs.get(C.TEXT_UUID)
        if s3:
            try:
                st = c.read(s3).decode("utf-8", "replace")
                m = re.search(r"mtu=(\d+)", st)
                if m:
                    mtu = int(m.group(1))
                    chunk = max(20, mtu - 3)
                    log(f"设备上报 MTU={mtu} ⇒ 每次写 {chunk} B")
            except Exception as e:                      # noqa: BLE001
                log(f"读 MTU 失败（用默认 {chunk} B）：{e}")

    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)
    log("=" * 66)
    log(f"PTY 就绪：{slave_name}")
    log("另开一个终端，用 root 起 pppd（对面就是它）：")
    log(f"    sudo pppd {slave_name} 115200 noauth 192.168.7.1:192.168.7.2 "
        "local nodetach")
    log("然后在**板子**上： ifconfig  /  ping 192.168.7.1")
    log("=" * 66)

    # 设备 → 主机：订阅通知，收到就写进 PTY master
    import dbus
    import dbus.mainloop.glib
    from gi.repository import GLib

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    ctx = GLib.MainContext.default()
    to_pty = bytearray()

    def on_props(interface, changed, invalidated):
        if interface == C.GATT_CHR_IFACE and "Value" in changed:
            to_pty.extend(bytes(bytearray(changed["Value"])))

    c.bus.add_signal_receiver(on_props, dbus_interface=C.PROPS_IFACE,
                              signal_name="PropertiesChanged", path=tx)
    tx_iface = dbus.Interface(c.bus.get_object(C.BLUEZ, tx), C.GATT_CHR_IFACE)
    tx_iface.StartNotify()
    log("已订阅设备→主机 通知，开始搬运")

    n_rx = n_tx = 0
    try:
        while True:
            while ctx.pending():
                ctx.iteration(False)

            if to_pty:
                try:
                    w = os.write(master, bytes(to_pty))
                    n_rx += w
                    del to_pty[:w]
                except OSError as e:
                    if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                        raise

            r, _, _ = select.select([master], [], [], 0.05)
            if r:
                data = os.read(master, chunk)
                if data:
                    # 主机 → 设备：按 MTU 分片写。GATT 写是有序的，
                    # 所以 PPP 的 HDLC 帧在设备侧能原样拼回来。
                    for i in range(0, len(data), chunk):
                        c.write(rx, data[i:i + chunk])
                        n_tx += len(data[i:i + chunk])
    except KeyboardInterrupt:
        log("退出")
    finally:
        log(f"累计：主机→设备 {n_tx} B，设备→主机 {n_rx} B")
        try:
            tx_iface.StopNotify()
        except Exception:                                # noqa: BLE001
            pass
        os.close(master)
        os.close(slave)
    return 0


if __name__ == "__main__":
    sys.exit(main())
