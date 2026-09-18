#!/usr/bin/env python3
"""pw_ble_central.py —— 宿主侧 BLE 中心设备（BlueZ + D-Bus），用来验 PhyWear 的 B3

为什么有它：B3 原本设计成"人工用手机连一次"，但**本机现在有蓝牙控制器**
（VM 直通 Intel AX201，hci0）。既然能力具备，就不该再把可自动化的部分留给人。

它做的事与手机上用 nRF Connect 点的那几下**完全对应**：
  1. 扫描，按**名字**找 "PhyWear"（⚠️ 手表广播用随机地址，按 MAC 找不到）
  2. 连接，等 ServicesResolved
  3. 找到服务 e0f1a000-… 与它的两条特征
  4. 读 …a001（期望 16 B，解出 ax/ay/az）
  5. 对 …a001 打开 Notify，收 2 个包
  6. 往 …a002 写 "ping"
最后打 PASS/FAIL 表。

依赖：python3-dbus（本机已装）。不需要 bleak。
用法：
    python3 tools/phywear/pw_ble_central.py [--name PhyWear] [--timeout 30]
"""

import argparse
import sys
import time

import dbus
import dbus.mainloop.glib          # noqa: F401  (需要它才能收 D-Bus 信号)
from gi.repository import GLib

# 收 PropertiesChanged（通知值）必须把连接挂到一个主循环上，否则 add_signal_receiver
# 会抛 "D-Bus connections must be attached to a main loop"（第一版就栽在这里）。
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

BLUEZ = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
GATT_SVC_IFACE = "org.bluez.GattService1"
GATT_CHR_IFACE = "org.bluez.GattCharacteristic1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
OM_IFACE = "org.freedesktop.DBus.ObjectManager"

SVC_UUID = "e0f1a000-1b2c-4d5e-8f90-a1b2c3d4e5f6"
SENSOR_UUID = "e0f1a001-1b2c-4d5e-8f90-a1b2c3d4e5f6"
CMD_UUID = "e0f1a002-1b2c-4d5e-8f90-a1b2c3d4e5f6"


def log(m):
    print(f"[central] {m}", flush=True)


def norm(uuid):
    return str(uuid).lower()


class Central:
    def __init__(self, name, timeout):
        self.name = name
        self.timeout = timeout
        self.bus = dbus.SystemBus()
        self.om = dbus.Interface(self.bus.get_object(BLUEZ, "/"), OM_IFACE)
        self.adapter_path = None
        self.adapter = None

    def find_adapter(self):
        for path, ifaces in self.om.GetManagedObjects().items():
            if ADAPTER_IFACE in ifaces:
                self.adapter_path = path
                self.adapter = dbus.Interface(
                    self.bus.get_object(BLUEZ, path), ADAPTER_IFACE)
                log(f"adapter = {path}")
                return True
        return False

    def props(self, path, iface):
        return dbus.Interface(self.bus.get_object(BLUEZ, path), PROPS_IFACE)

    def forget_stale(self):
        """扫描前清掉 BlueZ 里缓存的目标设备记录。

        为什么必须做：手表广播用**随机地址**，每次上电可能换一个；而 BlueZ 会把
        上一次的 `dev_XX_XX_…` 连同 `Name="PhyWear"` 一起缓存下来。于是
        GetManagedObjects() 里存在一个**名字对得上、但早就不广播**的对象，
        扫描循环会立刻命中它（第一版就差点连到一个死地址上）。
        清掉之后，命中的一定是本轮真正扫到的那个。
        """
        try:
            objects = self.om.GetManagedObjects()
        except dbus.DBusException as e:
            log(f"读对象表失败，跳过清理：{e.get_dbus_name()}")
            return
        for path, ifaces in objects.items():
            if DEVICE_IFACE not in ifaces:
                continue
            if not str(path).startswith(self.adapter_path + "/dev_"):
                continue
            d = ifaces[DEVICE_IFACE]
            nm = str(d.get("Name", d.get("Alias", "")))
            if self.name.lower() not in nm.lower():
                continue
            if bool(d.get("Connected", False)):
                log(f"已有连接中的 {nm!r} @ {path}，保留")
                continue
            try:
                self.adapter.RemoveDevice(dbus.ObjectPath(path))
                log(f"清掉缓存记录 {nm!r} @ {path}（随机地址可能已变）")
            except dbus.DBusException as e:
                log(f"  清理 {path} 失败：{e.get_dbus_name()}（继续）")

    def scan_for(self):
        """扫描并按名字（或服务 UUID）找到目标设备。"""
        try:
            self.adapter.SetDiscoveryFilter(
                dbus.Dictionary({"Transport": dbus.String("le")},
                                signature="sv"))
        except dbus.DBusException as e:
            log(f"SetDiscoveryFilter 跳过（{e.get_dbus_name()}）")

        self.forget_stale()

        log(f"开始扫描，找名字含 {self.name!r} 的设备 ……")
        self.adapter.StartDiscovery()
        deadline = time.time() + self.timeout
        found = None
        seen = {}
        try:
            while time.time() < deadline:
                for path, ifaces in self.om.GetManagedObjects().items():
                    if DEVICE_IFACE not in ifaces:
                        continue
                    if not str(path).startswith(self.adapter_path + "/dev_"):
                        continue
                    d = ifaces[DEVICE_IFACE]
                    nm = str(d.get("Name", d.get("Alias", "")))
                    seen[str(path)] = nm
                    uuids = [norm(u) for u in d.get("UUIDs", [])]
                    if self.name.lower() in nm.lower() or SVC_UUID in uuids:
                        found = path
                        log(f"找到：{nm!r} @ {path}"
                            f"（UUID 命中={SVC_UUID in uuids}）")
                        break
                if found:
                    break
                time.sleep(1.0)
        finally:
            try:
                self.adapter.StopDiscovery()
            except dbus.DBusException:
                pass
        if not found:
            log(f"扫描 {self.timeout}s 没找到。期间看到的设备：")
            for p, n in list(seen.items())[:12]:
                log(f"    {n!r}  {p}")
        return found

    def connect(self, dev_path):
        """连接并等服务解析。

        为什么要重试：BlueZ 自己也会去 auto-connect，两条路撞上时
        `Device1.Connect()` 会直接抛 `NoReply`（第一版就撞了一次）。
        所以这里把 Connect 包在重试里，并且**以属性为准**判断成功。
        """
        p = self.props(dev_path, DEVICE_IFACE)
        dev = dbus.Interface(self.bus.get_object(BLUEZ, dev_path), DEVICE_IFACE)
        deadline = time.time() + self.timeout
        attempt = 0
        while time.time() < deadline:
            if bool(p.Get(DEVICE_IFACE, "Connected")) and \
                    bool(p.Get(DEVICE_IFACE, "ServicesResolved")):
                log(f"已连接且服务已解析（{str(p.Get(DEVICE_IFACE, 'Address'))}）")
                return True
            if attempt < 3 and not bool(p.Get(DEVICE_IFACE, "Connected")):
                attempt += 1
                log(f"发起连接（第 {attempt} 次）……")
                try:
                    dev.Connect()
                except dbus.DBusException as e:
                    # NoReply / InProgress：多半是 BlueZ 自己正在连，继续等属性变化
                    log(f"  Connect() 抛 {e.get_dbus_name()}，继续等属性……")
            time.sleep(0.5)
        log("连接或服务解析超时")
        return False

    def gatt(self, dev_path):
        """返回 (service_path, {uuid: chr_path})"""
        svc, chrs = None, {}
        for path, ifaces in self.om.GetManagedObjects().items():
            if GATT_SVC_IFACE in ifaces:
                if str(ifaces[GATT_SVC_IFACE].get("Device", "")) != str(dev_path):
                    continue
                if norm(ifaces[GATT_SVC_IFACE]["UUID"]) == SVC_UUID:
                    svc = path
            if GATT_CHR_IFACE in ifaces and svc is None:
                chrs[norm(ifaces[GATT_CHR_IFACE]["UUID"])] = path
        # 第二遍：拿到 svc 之后再按 Service 归属挑特征
        if svc:
            chrs = {}
            for path, ifaces in self.om.GetManagedObjects().items():
                if GATT_CHR_IFACE in ifaces and \
                        str(ifaces[GATT_CHR_IFACE].get("Service", "")) == str(svc):
                    chrs[norm(ifaces[GATT_CHR_IFACE]["UUID"])] = path
        return svc, chrs

    def read(self, chr_path):
        iface = dbus.Interface(self.bus.get_object(BLUEZ, chr_path),
                               GATT_CHR_IFACE)
        val = iface.ReadValue(dbus.Dictionary({}, signature="sv"))
        return bytes(bytearray(val))

    def write(self, chr_path, data):
        iface = dbus.Interface(self.bus.get_object(BLUEZ, chr_path),
                               GATT_CHR_IFACE)
        iface.WriteValue(dbus.Array([dbus.Byte(b) for b in data],
                                    signature="y"),
                         dbus.Dictionary({}, signature="sv"))

    def notify(self, chr_path, want, timeout):
        """订阅并收集 want 个包（按 handle/时间窗去重）。"""
        got = []
        iface = dbus.Interface(self.bus.get_object(BLUEZ, chr_path),
                               GATT_CHR_IFACE)

        def on_props(interface, changed, invalidated):
            if interface != GATT_CHR_IFACE:
                return
            if "Value" in changed:
                got.append(bytes(bytearray(changed["Value"])))

        self.bus.add_signal_receiver(
            on_props, dbus_interface=PROPS_IFACE,
            signal_name="PropertiesChanged", path=chr_path)

        iface.StartNotify()
        deadline = time.time() + timeout
        ctx = GLib.MainContext.default()
        while time.time() < deadline and len(got) < want:
            # 把主循环里已排队的信号处理掉（非阻塞 iteration）
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.1)
        try:
            iface.StopNotify()
        except dbus.DBusException:
            pass
        self.bus.remove_signal_receiver(on_props,
                                        dbus_interface=PROPS_IFACE,
                                        signal_name="PropertiesChanged",
                                        path=chr_path)
        return got


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="PhyWear")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--notify-count", type=int, default=2)
    args = ap.parse_args()

    rows = []

    def check(label, ok, detail=""):
        rows.append((label, ok, detail))
        log(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")

    c = Central(args.name, args.timeout)
    if not c.find_adapter():
        log("找不到 BlueZ adapter —— 宿主没有蓝牙控制器")
        return 2

    dev = c.scan_for()
    check("扫描到 PhyWear", dev is not None, str(dev) if dev else "未找到")
    if not dev:
        return 1

    check("连接成功且服务已解析", c.connect(dev))
    svc, chrs = c.gatt(dev)
    check("找到服务 e0f1a000-…", svc is not None, str(svc) if svc else "")
    sensor = chrs.get(SENSOR_UUID)
    cmd = chrs.get(CMD_UUID)
    check("找到 sensor 特征 …a001", sensor is not None)
    check("找到 cmd 特征 …a002", cmd is not None)
    if not (svc and sensor and cmd):
        return 1

    try:
        val = c.read(sensor)
        ok = len(val) == 16
        ax = int.from_bytes(val[0:2], "little", signed=True) if ok else 0
        ay = int.from_bytes(val[2:4], "little", signed=True) if ok else 0
        az = int.from_bytes(val[4:6], "little", signed=True) if ok else 0
        mag = (ax * ax + ay * ay + az * az) ** 0.5
        check("读 …a001 得到 16 B", ok, f"{len(val)} B")
        check("解出加速度且 |a| 合理(300~3000mg)", 300 <= mag <= 3000,
              f"ax={ax} ay={ay} az={az} mg |a|={mag:.0f} mg")
    except dbus.DBusException as e:
        check("读 …a001 得到 16 B", False, e.get_dbus_name())

    try:
        packets = c.notify(sensor, args.notify_count, args.timeout)
        check(f"Notify 收到 >= {args.notify_count} 个包",
              len(packets) >= args.notify_count,
              f"收到 {len(packets)} 个，首个 {packets[0].hex() if packets else '-'}")
    except dbus.DBusException as e:
        check(f"Notify 收到 >= {args.notify_count} 个包", False, e.get_dbus_name())

    try:
        c.write(cmd, b"ping")
        check("写 …a002 'ping' 成功", True)
    except dbus.DBusException as e:
        check("写 …a002 'ping' 成功", False, e.get_dbus_name())

    print()
    w = max(len(r[0]) for r in rows)
    print(f"{'判定':<6}{'检查项':<{w}}  说明")
    print("-" * 78)
    for n, ok, d in rows:
        print(f"{'PASS' if ok else 'FAIL':<6}{n:<{w}}  {d}")
    print("-" * 78)
    allok = all(r[1] for r in rows)
    print("结论：" + ("✅ 宿主侧 BLE 中心设备全过（B3 设备侧 + 空口均成立）"
                    if allok else "❌ 有未过项"))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
