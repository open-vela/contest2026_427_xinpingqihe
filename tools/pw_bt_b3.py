#!/usr/bin/env python3
"""pw_bt_b3.py —— PhyWear 蓝牙 B3（手机连接读写）自动判定器

为什么单独写一个：
  B3 是**人工**动作（拿手机连），但"算不算通过"不该靠肉眼看手机屏幕。
  这个脚本把设备侧该证明的东西全部落成串口原文并自动判定：

    ① 广播起来了 / 可连接        ← [bt] B2 adv start rc=0
    ② 手机连上了                  ← [bt] B3 connected: … err=0
    ③ 手机订阅了 Notify           ← [bt] ccc changed -> notify ON
    ④ 手机写命令进来了            ← [bt] cmd #n (x B): '…'
    ⑤ 通知真的发出去了            ← 订阅后无 [bt] notify rc=… 报错（有报错即 FAIL）
    ⑥ 断开正常                    ← [bt] B3 disconnected: …（可选，不断开不算失败）

用法（板子插着、串口空闲）：
    python3 tools/phywear/pw_bt_b3.py --out docs/evidence/bt-b3-$(date +%Y%m%d-%H%M)
  脚本会复位板子、跑 `phywear cap bt`，然后**打印提示并等 --wait 秒**（默认 240），
  这段时间里用手机（nRF Connect / LightBlue）：
      按名字找 "PhyWear"（不要按 MAC —— 广播用的是随机地址）
      → 连接 → 读特征 …a001 → 对 …a001 打开 Notify → 往 …a002 写 ping
  到时间后脚本打印 PASS/FAIL 表并把日志 + sha256 落盘。

退出码：0 = ①②③④⑤ 全过；非 0 = 有未过的项（明细见 stdout 与 <out>/b3.log）。
"""

import argparse
import hashlib
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pw_serial import Session, dump, log  # noqa: E402

CHECKS = [
    ("adv_started", "广播启动 rc=0", r"\[bt\] B2 adv start rc=0",
     "设备没在广播 —— 检查 phywear cap bt 的输出"),
    ("connected", "手机已连接", r"\[bt\] B3 connected: .* err=0",
     "手机没连上 —— 按名字找 PhyWear；本板广播用随机地址，按 MAC 找不到"),
    ("notify_on", "手机订阅 Notify", r"\[bt\] ccc changed -> notify ON",
     "没订阅 —— 在 nRF Connect 里点该特征右侧的三个箭头(Notify)"),
    ("cmd_in", "手机写入命令", r"\[bt\] cmd #\d+ \(\d+ B\): '.*'",
     "没收到写 —— 往 …a002 写一段 ASCII（例如 ping）"),
    ("no_notify_err", "通知无报错", r"\[bt\] notify rc=",
     "通知发送报错 —— 把该行贴给 DSH", True),
]


def evaluate(text):
    rows = []
    ok_all = True
    for name, label, pattern, hint, *negate in CHECKS:
        hit = re.search(pattern, text) is not None
        good = (not hit) if negate else hit
        rows.append((name, label, good, hint))
        if not good:
            ok_all = False
    return rows, ok_all


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=1000000)
    ap.add_argument("--wait", type=float, default=240.0,
                    help="给人工操作留的窗口（秒），默认 240")
    ap.add_argument("--no-reset", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.port):
        log(f"致命：{args.port} 不存在（板子掉线？烧录与串口都会静默无输出）")
        return 2

    sess = Session(args.port, args.baud, boot_timeout=60.0)
    try:
        if not args.no_reset:
            sess.reset()
        sess.send("phywear cap bt", wait=False)
        sess.pump(12.0)

        log("=" * 68)
        log("现在请用手机操作（脚本正在采集）：")
        log("  1) 蓝牙扫描里按**名字**找 \"PhyWear\"（不要按 MAC）")
        log("  2) 连接")
        log("  3) 读特征 e0f1a001（16 B 传感器包）")
        log("  4) 对 e0f1a001 打开 Notify")
        log("  5) 往 e0f1a002 写 ASCII 命令，例如  ping")
        log(f"窗口 {args.wait:.0f} 秒 ……")
        log("=" * 68)
        sess.pump(args.wait)
    finally:
        raw_path, log_path = dump(args.out, "b3", sess)
        text = bytes(sess.raw).decode("utf-8", "replace")
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)

    rows, ok_all = evaluate(text)

    print()
    print(f"{'判定':<6}{'检查项':<18}说明")
    print("-" * 68)
    for _name, label, good, hint in rows:
        mark = "PASS" if good else "FAIL"
        print(f"{mark:<6}{label:<18}{'' if good else hint}")
    print("-" * 68)
    print(f"结论：{'✅ B3 通过（设备侧证据齐全）' if ok_all else '❌ B3 未通过（见上表）'}")
    print(f"日志：{log_path}\n原始：{raw_path}")
    with open(log_path, "rb") as fh:
        print(f"log sha256 = {hashlib.sha256(fh.read()).hexdigest()}")

    if ok_all:
        out = os.path.join(args.out, "b3.log")
        print("\n把上面这段 + 手机截图一起放进 docs/evidence/ 即可作为 B3 通过证据。")
        _ = out
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
