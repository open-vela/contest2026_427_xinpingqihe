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
    ("selftest", "服务端自检 PASS", r"\[bt\] SELFTEST PASS",
     "设备侧 GATT 数据库/读写路径就没过 —— 先修这个，别怀疑手机（看 SELFTEST 明细）"),
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
    """判定。

    ⚠️ 关键：订阅(ccc)与写入(cmd #)这两项**必须在"已连接"之后**才算数 ——
    否则设备侧自检自己写的那条命令会被误判成"手机写进来了"（这个假 PASS
    在本轮真的发生过，已修：自检改用 selftest 命令名 + 这里限定区间）。
    """
    m = None
    for m in re.finditer(r"\[bt\] B3 connected: .* err=0", text):
        pass
    after_conn = text[m.end():] if m else ""

    rows = []
    ok_all = True
    for name, label, pattern, hint, *negate in CHECKS:
        scope = after_conn if name in ("notify_on", "cmd_in") else text
        hit = re.search(pattern, scope) is not None
        good = (not hit) if negate else hit
        rows.append((name, label, good, hint))
        if not good:
            ok_all = False
    return rows, ok_all


# ── 判定器自测（不需要板子/手机）─────────────────────────────────────────
# 为什么要它：这个脚本**只在"没有手机"的方向上做过负对照**——万一手机那边
# 一切正常、而这里正则/区间写错，人就会看到假 FAIL，还以为设备坏了。
# 下面用合成日志把"应当 PASS"的方向也测一遍，顺带钉住几条关键语义。
SELFTEST_CASES = [
    ("理想情况：连上→订阅→写 ping", True, """\
[bt] B2 adv start rc=0 (ok)
[bt] SELFTEST PASS (fails=0)
[bt] cmd #1 (8 B): 'selftest'
[bt] B3 connected: 5A:58:62:96:E7:57 err=0
[bt] ccc changed -> notify ON
[bt] cmd #2 (4 B): 'ping'
[bt] cmd: pong
"""),
    ("没连上（其余证据齐全）", False, """\
[bt] B2 adv start rc=0 (ok)
[bt] SELFTEST PASS (fails=0)
[bt] cmd #1 (8 B): 'selftest'
"""),
    ("写入发生在连接之前 —— 那是自检写的，不能算", False, """\
[bt] B2 adv start rc=0 (ok)
[bt] SELFTEST PASS (fails=0)
[bt] cmd #1 (8 B): 'selftest'
[bt] ccc changed -> notify ON
"""),
    ("连上后通知报错 —— 要 FAIL", False, """\
[bt] B2 adv start rc=0 (ok)
[bt] SELFTEST PASS (fails=0)
[bt] B3 connected: 5A:58:62:96:E7:57 err=0
[bt] ccc changed -> notify ON
[bt] cmd #2 (4 B): 'ping'
[bt] notify rc=-22
"""),
    ("先 OFF 后 ON（手机来回切过订阅）—— 仍应 PASS", True, """\
[bt] B2 adv start rc=0 (ok)
[bt] SELFTEST PASS (fails=0)
[bt] B3 connected: 5A:58:62:96:E7:57 err=0
[bt] ccc changed -> notify ON
[bt] ccc changed -> notify OFF
[bt] ccc changed -> notify ON
[bt] cmd #2 (4 B): 'ping'
"""),
]


def run_selftest():
    bad = 0
    for label, want, text in SELFTEST_CASES:
        rows, got = evaluate(text)
        mark = "PASS" if got == want else "FAIL"
        if got != want:
            bad += 1
            det = ", ".join(f"{n}={'P' if g else 'F'}" for n, _, g, _ in rows)
            print(f"{mark}  {label}  期望 {want} 实得 {got}  [{det}]")
        else:
            print(f"{mark}  {label}  期望 {want} 实得 {got}")
    print("-" * 60)
    print("判定器自测：" + ("✅ 全部符合预期" if bad == 0 else f"❌ {bad} 例不符"))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__ + "\n\n注意：本脚本在**电脑**上运行；手机只用来点 BLE 调试 App，"
                  "不需要在手机上跑任何脚本。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="",
                    help="证据落盘目录（--selftest 时不需要）")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=1000000)
    ap.add_argument("--wait", type=float, default=240.0,
                    help="给人工操作留的窗口（秒），默认 240")
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="不碰板子：用合成日志测判定器本身（正/反两个方向）")
    args = ap.parse_args()

    if args.selftest:
        return run_selftest()

    if not args.out:
        ap.error("需要 --out <目录>（除非用 --selftest）")

    if not os.path.exists(args.port):
        log(f"致命：{args.port} 不存在（板子掉线？烧录与串口都会静默无输出）")
        return 2

    sess = Session(args.port, args.baud, boot_timeout=60.0)
    try:
        if not args.no_reset:
            sess.reset()

        # ⚠️ 必须先确认命令**真的生效**再让用户去手机上找设备。
        # 本板控制台被 NSH 与 ai_agent 的 vela> CLI **抢同一个 stdin**：
        # 谁先阻塞在 read 谁拿到那一行。实测约一半的概率 phywear 会被 vela> 吃掉，
        # 回一句 "Unknown command: phywear"，于是根本没有广播 ——
        # 如果不检查就提示用户"去连 PhyWear"，人会白折腾一轮还以为设备坏了。
        started = False
        for attempt in range(1, 6):
            sess.send("phywear cap bt", wait=False)
            sess.pump(12.0)
            if b"[bt] B2 adv start rc=0" in bytes(sess.raw):
                log(f"命令已生效（第 {attempt} 次尝试）")
                started = True
                break
            log(f"第 {attempt} 次没生效（多半被 vela> CLI 吃了），重试……")

        if not started:
            log("致命：连续 5 次都没能让 `phywear cap bt` 生效 —— "
                "板子没在广播，别再试手机了；先看下面日志里有没有 'Unknown command: phywear'")

        log("=" * 68)
        log("本脚本在**电脑**上跑；手机只需当一个蓝牙扫描/连接工具")
        log("（手机不用装本项目的任何东西、不用跑任何脚本 —— 装个 nRF Connect /")
        log(" LightBlue 之类的通用 BLE 调试 App 即可，商店直接搜）")
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
