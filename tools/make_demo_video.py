#!/usr/bin/env python3
# tools/make_demo_video.py   （上游/文档内引用路径：tools/phywear/make_demo_video.py）
#
# SPDX-License-Identifier: Apache-2.0
#
# 生成 PhyWear 演示视频 **v0.1 静态素材版**：
#   1280x720 / H.264 / yuv420p / 无音轨，按 docs/08_演示视频拍摄脚本.md（v4）分段，
#   画面全部由 **已有素材** 拼成 —— 真机截图、模拟器截图、串口日志文字卡。
#
# ⚠️ 本机没有真实拍摄素材（真机无视频输出、模拟器取帧 0.3 fps、screenrecord 不可用），
#    所以本片 **不是实拍演示**：片头与片尾都明确标注「v0.1 静态素材版」，
#    每一帧的页脚也常驻该声明。**不要**把本片当成真机动态画面。
#
# 用法：
#   python3 tools/make_demo_video.py --repo ~/work/contest2026_427_xinpingqihe \
#       --out ~/work/contest2026_427_xinpingqihe/docs/evidence/demo-20260915/phywear-demo-v0.1.mp4
#
#   # 只渲染分镜 PNG（不编码），用于快速校对排版/中文渲染：
#   python3 tools/make_demo_video.py --dry-run
#   # 只渲染第 N 张分镜：
#   python3 tools/make_demo_video.py --dry-run --only 6
#   # 编码完成后自动抽 3 帧 PNG 做人工复核：
#   python3 tools/make_demo_video.py --extract-frames
#
# 依赖：Pillow + CJK 字体；ffmpeg（自动探测，见 find_ffmpeg()）。
#
# 设计约束（刻意为之，勿"优化"掉）：
#   * 素材缺失 → **直接报错退出**，不静默跳过、不用占位图顶替；
#   * 模拟器截图 → 片内强制打「模拟器截图」角标；
#   * 串口日志卡 → 片内强制打「串口日志·非画面」角标；
#   * 页脚常驻「v0.1 静态素材版 · 非实拍动态画面」；
#   * 文字排版溢出卡片/画布 → **报错**，不裁切。

import argparse
import glob as globmod
import os
import re
import shutil
import subprocess
import sys
import time

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    sys.exit("需要 Pillow：python3 -m pip install --user --break-system-packages Pillow")

# ---------------------------------------------------------------- 画布与时间轴

W, H = 1280, 720
FPS_DEFAULT = 25

HEADER_H = 76
FOOTER_Y = 600
CONTENT_TOP = HEADER_H + 8          # 84
CONTENT_BOT = FOOTER_Y - 4          # 596

# 分段时长（秒）——严格对齐 docs/08 v4 的时间轴总览（合计 4:55 = 295 s，硬上限 300 s）
SEG_PLAN = [
    ("片头", 8),
    ("第 1 段", 20),
    ("第 2 段", 37),
    ("第 3 段", 30),
    ("第 4 段", 30),
    ("第 5 段", 35),
    ("第 6 段", 45),
    ("第 7 段", 35),
    ("第 8 段", 25),
    ("第 9 段", 30),
]
TOTAL_SECS = sum(s for _, s in SEG_PLAN)

MAX_SECS = 300                      # 交付硬上限：≤5 分钟

# ---------------------------------------------------------------- 颜色

BG = (16, 18, 24)
PANEL = (26, 30, 38)
PANEL_B = (60, 68, 84)
FG = (234, 237, 244)
DIM = (150, 158, 172)
ACCENT = (120, 190, 255)
WARN = (255, 196, 110)
OK = (128, 226, 158)
HI = (255, 214, 120)
CMD = (140, 215, 160)

KIND = {
    "real":  ("真机截图",        (28, 74, 52),  (110, 220, 150)),
    "sim":   ("模拟器截图",      (104, 56, 16), (255, 170, 80)),
    "log":   ("串口日志·非画面",  (30, 54, 92),  (126, 188, 255)),
    "mixed": ("真机 + 串口日志",  (52, 42, 92),  (176, 156, 255)),
    "v01":   ("v0.1 静态素材版",  (96, 28, 40),  (255, 132, 142)),
    "concl": ("结论 · 非画面",    (30, 54, 92),  (126, 188, 255)),
}

FOOTER_L1 = ("v0.1 静态素材版 · 全部画面为已有真机/模拟器截图与串口日志文字卡"
             "（非实拍动态画面）· 真机动态画面待实拍 · 无音轨")

# ---------------------------------------------------------------- 字体

SANS_BOLD_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
SANS_REG_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
MONO_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",   # index 7 = Mono CJK SC
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]
# .ttc 里 "Noto Sans CJK SC" / "Noto Sans Mono CJK SC" 的 face index
SANS_INDEX = 2
MONO_INDEX = 7

_font_cache = {}


def _pick(cands, what):
    for p in cands:
        if os.path.exists(p):
            return p
    sys.exit("找不到 %s 字体（候选：%s）；中文会渲染成方框，故直接失败。" % (what, cands))


FONT_SANS_BOLD = None
FONT_SANS_REG = None
FONT_MONO = None


def font(kind, size):
    """kind: 'b' 粗体标题 / 'r' 正文 / 'm' 等宽（日志）。"""
    key = (kind, size)
    if key in _font_cache:
        return _font_cache[key]
    if kind == "m":
        f = ImageFont.truetype(FONT_MONO, size, index=MONO_INDEX) if FONT_MONO.endswith(".ttc") \
            else ImageFont.truetype(FONT_MONO, size)
    else:
        path = FONT_SANS_BOLD if kind == "b" else FONT_SANS_REG
        f = ImageFont.truetype(path, size, index=SANS_INDEX)
    _font_cache[key] = f
    return f


def init_fonts():
    global FONT_SANS_BOLD, FONT_SANS_REG, FONT_MONO
    FONT_SANS_BOLD = _pick(SANS_BOLD_CANDIDATES, "CJK 粗体")
    FONT_SANS_REG = _pick(SANS_REG_CANDIDATES, "CJK 常规")
    FONT_MONO = _pick(MONO_CANDIDATES, "等宽")


# ---------------------------------------------------------------- 文本工具


def wrap(draw, text, f, maxw):
    """按像素宽度折行；兼容中英混排（逐字符测宽）。显式 \n 保留。"""
    out = []
    for para in text.split("\n"):
        if not para:
            out.append("")
            continue
        cur = ""
        for ch in para:
            if draw.textlength(cur + ch, font=f) <= maxw:
                cur += ch
            else:
                # 英文/数字尽量在空格处断开
                if " " in cur and ord(ch) < 0x2E80:
                    head, _, tail = cur.rpartition(" ")
                    out.append(head)
                    cur = tail + ch
                else:
                    out.append(cur)
                    cur = ch
        out.append(cur)
    return out


def draw_lines(draw, x, y, lines, f, color, lh, maxw=None):
    for ln in lines:
        draw.text((x, y), ln, font=f, fill=color)
        y += lh
    return y


def text_block(draw, x, y, text, f, color, maxw, lh):
    """折行 + 绘制，返回新的 y。"""
    ys = y
    for ln in wrap(draw, text, f, maxw):
        draw.text((x, ys), ln, font=f, fill=color)
        ys += lh
    return ys


def block_height(draw, texts, f, maxw, lh):
    h = 0
    for t in texts:
        h += len(wrap(draw, t, f, maxw)) * lh
    return h


def rrect(draw, box, r, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


# ---------------------------------------------------------------- 素材

S = "docs/evidence"

# 素材清单：所有在 slides 里出现的路径都必须在这里，缺失即报错。
ASSETS = [
    f"{S}/ui-batch2-20260915/rev-real-home.png",
    f"{S}/ui-batch2-20260915/rev-real-raw-num.png",
    f"{S}/ui-batch2-20260915/rev-real-raw-curve.png",
    f"{S}/ui-batch2-20260915/realboard-raw-3pages.png",
    f"{S}/ui-batch2-20260915/realboard-incline-dial.png",
    f"{S}/ui-batch2-20260915/realboard-stopwatch-ring.png",
    f"{S}/ui-batch2-20260915/sim-incline-dial.png",
    f"{S}/ui-batch2-20260915/sim-stopwatch-ring.png",
    f"{S}/imu-ui-20260915/real-imu-live.png",
    f"{S}/imu-ui-20260915/real-imu-bias.png",
    f"{S}/imu-ui-20260915/real-imu-mag.png",
    f"{S}/imu-ui-20260915/real-bench-three.png",
    f"{S}/imu-ui-20260915/sim-bench-three.png",
    f"{S}/realboard-20260912/00_root.png",
    f"{S}/realboard-20260912/00_主页面总览.png",
    f"{S}/realboard-20260912/00_说明数据页总览.png",
    f"{S}/realboard-20260912/01_raw.png",
    f"{S}/realboard-20260912/02_pendulum.png",
    f"{S}/realboard-20260912/20_pend_p2.png",
    f"{S}/acoustic-20260913/tone.png",
    f"{S}/acoustic-20260913/mic.png",
]

# ---------------------------------------------------------------- 分镜（v0.1）
#
# 每个分镜 = 一段（或一段内的一个画面状态）。secs 之和必须 == TOTAL_SECS。
# layout:
#   title  —— 大字卡（片头/片尾），右侧一张真机截图
#   watch  —— 1 张 390x450 手表截图 + 右侧字幕栏
#   watch2 —— 2 张手表截图（0.86x） + 右侧字幕栏
#   wide   —— 1 张宽图（按框缩放） + 下方字幕
#   log    —— 串口日志文字卡 + 下方字幕

SLIDES = [
    # ---------------- 片头 ----------------
    dict(seg="片头", num="0 / 10", secs=8, layout="title", kind="v01",
         title="PhyWear · 腕上智慧物理工坊",
         images=[f"{S}/ui-batch2-20260915/rev-real-home.png"],
         caption="真机截图 rev-real-home.png",
         lines=[
             "立创黄山派 SF32LB52 · 2026 openvela AI 硬件大赛",
             "",
             "⚠ 本片是 v0.1 **静态素材版（样片）**，不是实拍演示：",
             "   全部画面来自 · 真机截图 · 模拟器截图 · 串口日志文字卡",
             "   片内无任何实拍动态画面，也无音轨（真人讲稿录音待补）",
         ],
         notes=[]),

    # ---------------- 第 1 段 ----------------
    dict(seg="第 1 段", num="1 / 10", secs=20, layout="watch", kind="real",
         title="一句话定位",
         images=[f"{S}/realboard-20260912/00_root.png"],
         caption="真机截图 realboard-20260912/00_root.png",
         lines=[
             "把手机 phyphox 的物理实验搬到手表上",
             "",
             "玩法来源：phyphox（致谢 / GPL v3）",
             "实现：C + LVGL 独立重实现（Apache-2.0）",
         ],
         notes=[
             "⚠ EPIC 硬件加速来自官方 PR #31/#41/#121，非本队自研",
             "本队做的是集成（板级/BSP）与 UI、算法层实现",
             "玩法为致敬 phyphox，代码为本队独立编写，见 app/phywear/NOTICE.md",
         ]),

    # ---------------- 第 2 段 ----------------
    dict(seg="第 2 段", num="2 / 10", secs=13, layout="watch", kind="real",
         title="真机开机 · 全中文界面",
         images=[f"{S}/realboard-20260912/00_主页面总览.png"],
         caption="真机截图 00_主页面总览.png（主页与各页拼图）",
         lines=[
             "真机实拍（静态帧）· 全中文 · 8 宫格主菜单",
             "",
             "串口：phywear lang zh",
             "开机 SFBL → ABCD → … → NuttShell；ai_agent 已开机自启",
         ],
         notes=[
             "本帧为真机静态截图，开机过程与界面滑动属动态画面 → 待实拍",
         ]),

    dict(seg="第 2 段", num="2 / 10", secs=12, layout="wide", kind="real",
         title="原始传感器 · 6 页实时数据",
         images=[f"{S}/ui-batch2-20260915/realboard-raw-3pages.png"],
         caption="真机截图 realboard-raw-3pages.png（三页并排）",
         lines=[
             "6 页原始传感器：加速度 / 陀螺 / 磁场 … 每页实时数值 + 图线",
         ],
         notes=[
             "素材：真机截图 docs/evidence/ui-batch2-20260915/",
         ]),

    dict(seg="第 2 段", num="2 / 10", secs=12, layout="watch2", kind="real",
         title="声学：2 个可用 + 2 个规划",
         images=[f"{S}/acoustic-20260913/tone.png", f"{S}/acoustic-20260913/mic.png"],
         caption="真机截图 acoustic-20260913/{tone,mic}.png",
         lines=[
             "左：音频发生器（发正弦）",
             "右：麦克风电平页",
             "",
             "如实说明：响度受板载小喇叭限制、同板声耦合弱",
         ],
         notes=[
             "真机实测：1 kHz 正弦时麦克风峰值 12,000 LSB（−8.4 dBFS）",
         ]),

    # ---------------- 第 3 段 ----------------
    dict(seg="第 3 段", num="3 / 10", secs=15, layout="watch", kind="real",
         title="双视图 · 默认大数字风格",
         images=[f"{S}/ui-batch2-20260915/rev-real-raw-num.png"],
         caption="真机截图 rev-real-raw-num.png",
         lines=[
             "默认大数字风格（同环境光页）",
             "",
             "轻点屏幕 → 切到图线视图（本片为静态帧，切换动作待实拍）",
         ],
         notes=[
             "本批新增：三轴页双视图",
         ]),

    dict(seg="第 3 段", num="3 / 10", secs=15, layout="watch", kind="real",
         title="每轴独立量程",
         images=[f"{S}/ui-batch2-20260915/rev-real-raw-curve.png"],
         caption="真机截图 rev-real-raw-curve.png",
         lines=[
             "曲线「看不出变化」的根因：三轴共用一个满量程",
             "重力把 Z 顶到 2 g，0.12 g 的 Y 只剩 ~3 px",
             "",
             "改成每轴独立量程后：Y 的摆动 3 px → 16 px",
         ],
         notes=[
             "底部标注变成每轴一个，如 +/-1 / 0.20 / 2 g",
             "复核脚本：docs/evidence/ui-batch2-20260915/verify_curve_scale.py",
         ]),

    # ---------------- 第 4 段 ----------------
    dict(seg="第 4 段", num="4 / 10", secs=15, layout="watch2", kind="real",
         title="倾角指针盘 + 秒表环形进度（真机）",
         images=[f"{S}/ui-batch2-20260915/realboard-incline-dial.png",
                 f"{S}/ui-batch2-20260915/realboard-stopwatch-ring.png"],
         caption="真机截图 realboard-incline-dial.png / realboard-stopwatch-ring.png",
         lines=[
             "左：指针式角度盘，13 根刻度逐格点亮",
             "右：秒表环形进度",
             "",
             "两者都避开 lv_arc",
             "（软件圆弧把帧率 26 → 23）",
         ],
         notes=[
             "改用刻度短线 + 指针 / 分段圆弧实现",
             "秒表页有 1 帧代价 fps 10 → 9（如实说明）",
         ]),

    dict(seg="第 4 段", num="4 / 10", secs=15, layout="watch2", kind="sim",
         title="同两页 · 模拟器对照帧",
         images=[f"{S}/ui-batch2-20260915/sim-incline-dial.png",
                 f"{S}/ui-batch2-20260915/sim-stopwatch-ring.png"],
         caption="模拟器截图 sim-incline-dial.png / sim-stopwatch-ring.png",
         lines=[
             "⚠ 本帧两张图均为模拟器截图",
             "（goldfish arm64 模拟器，非真机）",
             "",
             "用来说明「倾斜表身 → 刻度点亮」与环形进度的外观",
         ],
         notes=[
             "真机同两页见前一帧；模拟器数据 ≠ 真机测量",
         ]),

    # ---------------- 第 5 段 ----------------
    dict(seg="第 5 段", num="5 / 10", secs=18, layout="watch", kind="real",
         title="单摆测 g",
         images=[f"{S}/realboard-20260912/02_pendulum.png"],
         caption="真机截图 realboard-20260912/02_pendulum.png",
         lines=[
             "g = 4π²L / T²",
             "",
             "页面自己 50 Hz 采样 + 解算，摆长 L 可调",
             "本帧为静态截图：真实摆动一次的过程 → 待实拍",
         ],
         notes=[
             "⚠ 把板子静置时的读数说成「实验结果」是红线；见下一帧",
         ]),

    dict(seg="第 5 段", num="5 / 10", secs=17, layout="watch", kind="real",
         title="单摆测 g · 口径与残留风险",
         images=[f"{S}/realboard-20260912/20_pend_p2.png"],
         caption="真机截图 realboard-20260912/20_pend_p2.png（第 2 页）",
         lines=[
             "真机实测：摆测 g 偏差 ≈ 1.8%",
             "",
             "⚠ 如实说明：板子静止/桌面振动时，页面仍可能算出无效 g",
             "   上报前有三道有效性门，但窗口内仍有残余漏过",
         ],
         notes=[
             "真机静止 70 s：修复前 ~3 条/秒垃圾上报 → 修复后 1 条被拦 + 1 条漏过",
             "根治需改单摆页运动判据（未做）",
         ]),

    # ---------------- 第 6 段 ----------------
    dict(seg="第 6 段", num="6 / 10", secs=15, layout="watch", kind="mixed",
         title="主动 + 执行（本片最重要）",
         images=[f"{S}/realboard-20260912/02_pendulum.png"],
         caption="真机截图 02_pendulum.png（被自动打开的单摆页）",
         lines=[
             "它不经对话、自己发起：",
             "手晃手表 3 秒 → 自己跑实验 → 结果回到手表",
             "",
             "链路：检测到持续摆动",
             "  → [tools] Executing tool: phywear_run_experiment",
             "  → ai-coach: AI: 已自动测重力加速度: g = 6.09 m/s^2",
         ],
         notes=[
             "⚠ 本片的「晃表」动作没有实拍素材（⑤-1 真实晃表片段待补）",
             "本帧画面为静态截图 + 真机日志文字，不是拍摄的动态画面",
         ]),

    dict(seg="第 6 段", num="6 / 10", secs=15, layout="log", kind="log",
         title="主动场景全链路（串口日志）",
         images=[],
         caption="来源：docs/evidence/proactive-20260915/README.md + real-*.log",
         card=dict(
             head="串口日志 · 非画面（真机实测，无头链路）",
             lines=[
                 ("hi",  "PhyWear 巡检：检测到持续摆动（窗口振幅 x.xx g，持续 2.5 s）"),
                 ("dim", "  ↓ 事件写入手表 AI 消息日志；pw_ai_ask() 推给端侧 Agent"),
                 ("out", "[tools] Executing tool: phywear_run_experiment"),
                 ("out", "  {screen:\"pendulum\", seconds:10}"),
                 ("dim", "  ↓ 工具经 pw_ai_request_open() 请求 GUI 线程开页"),
                 ("out", "[phywear] 单摆页被自动打开 → 页面自己 50 Hz 采样解算"),
                 ("dim", "  ↓ pw_ai_publish_result(\"pendulum\", g, ...) 登记结果"),
                 ("hi",  "ai-coach: AI: 已自动测重力加速度: g = 6.09 m/s^2 (T = 1.800 s)"),
             ]),
         lines=[
             "架构关键：I2C 上永远只有 GUI 一路在采样；",
             "Agent 只「开页 → 等结果 → 读结果」",
         ],
         notes=[
             "09-13 的「十几秒整机卡住」根因就是 Agent 自己也在 50 Hz 采 IMU，已修掉",
             "同时修掉两个真崩溃：velaclaw_ask 未初始化断言、工具 cJSON 双重释放",
             "振幅为变量，脚本与日志以 x.xx 记；此处不编造具体数值",
         ]),

    dict(seg="第 6 段", num="6 / 10", secs=15, layout="log", kind="log",
         title="三道有效性门与残余风险",
         images=[],
         caption="来源：docs/evidence/proactive-20260915/{README.md,real-still-withheld.log}",
         card=dict(
             head="串口日志 · 非画面（有效性门 · 真机静止 70 s）",
             lines=[
                 ("out", "三道门（只影响「要不要上报」，页面原始数值不过滤、不隐藏）："),
                 ("out", "  ① 独立数据：距上次评估 ≥50 个新样本（1 s）"),
                 ("out", "  ② 可复现：连续两次估计相差 ≤8%"),
                 ("out", "  ③ 合理性：g ∈ 5.0 .. 15.0 m/s^2"),
                 ("hi",  "[phywear] pendulum result withheld: g=3.93 out of"),
                 ("hi",  "          5.0..15.0 m/s^2 (implausible measurement)"),
                 ("hi",  "[phywear] result pendulum: 7.5240 m/s^2 (T=1.620s)  ← 残余漏过"),
             ]),
         lines=[
             "⚠ 主动场景的「测量值」只有在用户真的摆动时才有意义；",
             "振动桌面能连续产生各种垃圾值，任何窗口都会偶尔漏。",
         ],
         notes=[
             "本片未包含「真实晃表 → 可信 g」的实拍片段（待真人挥手确认）",
         ]),

    # ---------------- 第 7 段 ----------------
    dict(seg="第 7 段", num="7 / 10", secs=9, layout="watch", kind="mixed",
         title="惯性标尺 · 姿态解算（真机日志）",
         images=[f"{S}/imu-ui-20260915/real-imu-live.png"],
         caption="真机截图 imu-ui-20260915/real-imu-live.png（水平仪实时页）",
         lines=[
             "自写 Mahony MARG 显式互补滤波（未抄参考包）",
             "",
             "串口 phywear ahrs 20 实测：",
             "roll=+178.12 pitch=+23.63 yaw=+75.63",
             "accel-tilt r=+178.74 p=+21.94",
         ],
         notes=[
             "AHRS 的 roll/pitch 与加速度计独立解算一致到 ~0.5°",
             "⚠ 只说一致性与稳定性，不宣称精度指标（无转台真值）",
             "本帧为静态截图 + 日志文字：手动转动表身的过程 → 待实拍",
         ]),

    dict(seg="第 7 段", num="7 / 10", secs=9, layout="watch2", kind="real",
         title="标定：零偏 + 重力六面 / 磁椭球",
         images=[f"{S}/imu-ui-20260915/real-imu-bias.png",
                 f"{S}/imu-ui-20260915/real-imu-mag.png"],
         caption="真机截图 real-imu-bias.png / real-imu-mag.png",
         lines=[
             "全流式充分统计，不新增采样缓冲",
             "（AHRS 76 B / 零偏 28 B / 六面 ~100 B / 磁椭球 360 B）",
             "",
             "磁力计只治偏航：",
             "关磁 yaw 以 ~2°/s 漂",
             "（估出陀螺零偏 −2.18 dps 与漂移率吻合）",
             "开磁后 yaw 稳定不漂",
         ],
         notes=[
             "本帧为静态截图：标定向导的真实点按 → 待实拍",
         ]),

    dict(seg="第 7 段", num="7 / 10", secs=9, layout="wide", kind="real",
         title="标定链路数值复验（真机 [BENCH]）",
         images=[f"{S}/imu-ui-20260915/real-bench-three.png"],
         caption="真机截图 imu-ui-20260915/real-bench-three.png",
         lines=[
             "零偏 +572 / −458 / +687 mdps（注入真值 573 / −458 / 688）→ 偏差 ≤1 mdps",
             "磁中心 +35 / −120 / +60 mG（注入真值 35 / −120 / 60）→ 偏差 ≤1 mG",
             "⚠ 标题带 [BENCH] = 注入合成数据走完整流程，不是测量结果",
         ],
         notes=[
             "重力六面法：零偏 +0.020/−0.015/+0.030 g，系数 0.980/1.020/0.990",
         ]),

    dict(seg="第 7 段", num="7 / 10", secs=8, layout="wide", kind="sim",
         title="同三条标定链路（模拟器 [BENCH]）",
         images=[f"{S}/imu-ui-20260915/sim-bench-three.png"],
         caption="模拟器截图 imu-ui-20260915/sim-bench-three.png",
         lines=[
             "⚠ 本帧为模拟器截图（goldfish arm64），标题同样带 [BENCH]",
             "模拟器注入同一组真值走完三条链路；真机同结果见前一帧",
         ],
         notes=[
             "模拟器数据 ≠ 真机测量",
         ]),

    # ---------------- 第 8 段 ----------------
    dict(seg="第 8 段", num="8 / 10", secs=13, layout="wide", kind="real",
         title="性能与稳定性（真机实测）",
         images=[f"{S}/realboard-20260912/00_说明数据页总览.png"],
         caption="真机截图 00_说明数据页总览.png（真机页面对照总览）",
         lines=[
             "lvgldemo benchmark：PhyWear 场景 41 FPS（render 22 / flush 0）",
             "页面级探针：原始页 fps 12 / loops 57（数值与图线两视图同值）",
             "倾角 25 · 秒表 9 · 水平仪 26 · 单摆 8~16（随是否在解算波动）",
             "固件 2,083,708 B（flash 12.42%）· 链接期 SRAM 492,560 B / 512 KB ≈ 93.96%",
         ],
         notes=[
             "EPIC 硬件加速 = 官方 PR #31/#41/#121（非本队自研）",
             "数字以现场输出为准；本片引用的是已归档的真机实测值",
         ]),

    dict(seg="第 8 段", num="8 / 10", secs=12, layout="log", kind="log",
         title="性能 / 稳定性 / SRAM 口径",
         images=[],
         caption="来源：docs/06 §真机验证记录、docs/evidence/{ui-batch2,imu-ui}-20260915/README.md",
         card=dict(
             head="串口日志 · 非画面（真机实测数字）",
             lines=[
                 ("cmd", "$ lvgldemo benchmark"),
                 ("hi",  "PhyWear 场景 41 FPS（render 22 / flush 0）"),
                 ("cmd", "$ 页面级探针（心跳行 loops/s= 与 fps=）"),
                 ("out", "原始传感器页 fps 12 / loops 57（数值与图线两视图同值）"),
                 ("out", "倾角 25 · 秒表 9 · 水平仪 26 · 单摆 8~16"),
                 ("out", "固件 2,083,708 B · 链接期 SRAM 492,560 B / 512 KB ≈ 93.96%"),
                 ("hi",  "启动：连续 5/5、6/6 复位正常（SFBL → ABCD → NSH）"),
                 ("out", "7 分钟泡机 0 复位（麦克风/扬声器互斥修复后）"),
             ]),
         lines=[
             "⚠ SRAM 口径如实说明：",
             "累计新增已超用户给的「不超过 ~2 KB」额度",
         ],
         notes=[
             "脚本 v4 口径（491,576 B / 93.76%）超约 88 B；最新固件口径（492,560 B）超约 1 KB",
             "已向用户报告并请其定口径（是否放宽额度 / 关掉现有功能换空间）",
         ]),

    # ---------------- 第 9 段 ----------------
    dict(seg="第 9 段", num="9 / 10", secs=10, layout="log", kind="concl",
         title="④ 蓝牙探针：no-go（三条断点）",
         images=[],
         caption="来源：docs/evidence/bt-probe-20260915/README.md",
         card=dict(
             head="结论 · 非画面（时间盒探针，已全部回退并验证复原）",
             lines=[
                 ("hi",  "结论：暂不可交付（no-go）—— 差的是「构建接线」，不是硬件"),
                 ("out", "片上蓝牙控制器有（LCPU，无需外挂控制器固件）"),
                 ("out", ".config 里 107 个 CONFIG_BT*/BLUETOOTH* 项是开的"),
                 ("out", "但基线固件里只有 2 个 BT 符号（nm nuttx | grep -ci）"),
                 ("out", "根因 1：CONFIG_UART_BTH4 未开 → /dev/ttyHCI0 从不注册"),
                 ("out", "根因 2：CONFIG_NET_BLUETOOTH 未开 → BT 目标文件一个都没编"),
                 ("out", "根因 3（决定性）：port/drivers/bluetooth/hci/ 没有 CMakeLists"),
                 ("hi",  "undefined reference to __init___device_dts_ord_..._zephyr_bt_hci_ttyHCI0_ORD"),
             ]),
         lines=[
             "如实说：没有任何「蓝牙能用」的结论，",
             "也没证明 LCPU 控制器一定会应答 HCI。",
         ],
         notes=[
             "SRAM 已 ≈93.96%，zblue 的静态 net_buf 池大概率放不下（最大风险）",
         ]),

    dict(seg="第 9 段", num="9 / 10", secs=10, layout="log", kind="concl",
         title="片尾 · 已知限制（如实列出）",
         images=[],
         caption="完整清单：docs/01_项目描述_如实版.md §已知限制",
         card=dict(
             head="结论 · 非画面（片尾必须出现的限制 ≥3 条）",
             lines=[
                 ("out", "① 真机无网络栈 —— LLM 自由对话只在模拟器；真机走离线意图"),
                 ("out", "② SRAM 492,560 B ≈ 93.96%；累计新增已超「~2 KB」额度"),
                 ("out", "③ ③ 精度无转台真值 —— 只说与加速度计解算一致 ~0.5°"),
                 ("out", "④ ④ 蓝牙 no-go（三条断点见前段）"),
                 ("out", "⑤ 秒表页 fps 10 → 9（一帧代价，如实说明）"),
                 ("out", "⑥ 单摆页运动判据未改：静止/振动桌面仍可能漏过无效 g"),
                 ("out", "⑦ 声学响度受板载小喇叭限制、同板声耦合弱"),
             ]),
         lines=[
             "未实现的部分一律写进「已知限制」，不写成已实现。",
         ],
         notes=[]),

    dict(seg="第 9 段", num="9 / 10", secs=10, layout="title", kind="v01",
         title="本片为 v0.1 静态素材版（样片）",
         images=[f"{S}/ui-batch2-20260915/rev-real-home.png"],
         caption="真机截图 rev-real-home.png",
         lines=[
             "—— 不是实拍演示 ——",
             "",
             "本片缺失（待补，已如实标注）：",
             "① 真机动态画面（开机 / 触控切视图 / 倾斜看指针盘 / 单摆真摆动）",
             "② 真人讲稿录音（本片无音轨）",
             "③ ⑤-1 真实晃表 3 秒 → 自动跑实验 → 可信 g 的实拍片段",
             "",
             "补齐后出 v1.0 实拍版；分镜对照表见 storyboard.png",
         ],
         notes=[
             "拍摄脚本：docs/08_演示视频拍摄脚本.md（v4，10 段 / 4:55）",
         ]),
]


# ---------------------------------------------------------------- 绘制：公共件


def draw_header(d, slide):
    d.rectangle([0, 0, W, HEADER_H - 1], fill=(18, 21, 28))
    d.line([0, HEADER_H - 1, W, HEADER_H - 1], fill=(46, 52, 66))

    fb = font("b", 24)
    # 段标签
    seg = slide["seg"]
    tw = d.textlength(seg, font=fb)
    bw = max(120, int(tw) + 36)
    rrect(d, [32, 16, 32 + bw, 60], 8, fill=(28, 44, 68), outline=(70, 120, 180), width=1)
    d.text((32 + (bw - tw) / 2, 24), seg, font=fb, fill=ACCENT)

    # 序号
    fn = font("r", 20)
    d.text((32 + bw + 12, 28), slide["num"], font=fn, fill=DIM)

    # 标题
    ft = font("b", 34)
    tx = 32 + bw + 12 + int(d.textlength(slide["num"], font=fn)) + 20
    # 素材来源角标占右侧，标题超宽就缩字号
    label, bg, fg = KIND[slide["kind"]]
    fk = font("b", 22)
    kw = int(d.textlength(label, font=fk)) + 32
    kx = W - 32 - kw
    while d.textlength(slide["title"], font=ft) > (kx - 24 - tx) and ft.size > 20:
        ft = font("b", ft.size - 2)
    d.text((tx, 22), slide["title"], font=ft, fill=FG)

    # 素材角标
    rrect(d, [kx, 16, W - 32, 60], 8, fill=bg, outline=fg, width=1)
    d.text((kx + (kw - d.textlength(label, font=fk)) / 2, 24), label, font=fk, fill=fg)


def draw_footer(d, t_abs):
    d.rectangle([0, FOOTER_Y, W, H], fill=(13, 15, 21))
    d.line([0, FOOTER_Y, W, FOOTER_Y], fill=(46, 52, 66))
    d.text((32, 610), FOOTER_L1, font=font("r", 20), fill=WARN)
    d.text((32, 640), "无音轨 · 无实拍动态画面 · 真机动态画面待实拍", font=font("r", 18), fill=DIM)

    # 进度条
    bx0, bx1, by = 32, W - 32, 672
    rrect(d, [bx0, by, bx1, by + 8], 4, fill=(44, 50, 62))
    frac = max(0.0, min(1.0, t_abs / TOTAL_SECS))
    fw = int((bx1 - bx0) * frac)
    if fw > 2:
        rrect(d, [bx0, by, bx0 + fw, by + 8], 4, fill=ACCENT)

    tc = "%02d:%02d / %02d:%02d" % (int(t_abs) // 60, int(t_abs) % 60,
                                    TOTAL_SECS // 60, TOTAL_SECS % 60)
    d.text((bx1 - d.textlength(tc, font=font("b", 20)), 686), tc, font=font("b", 20), fill=FG)
    d.text((bx0, 686), "PhyWear · 腕上智慧物理工坊 · 立创黄山派 SF32LB52",
           font=font("r", 18), fill=DIM)


def draw_badge_on_image(d, x, y, text, fg):
    """在图片左上角打来源角标（模拟器 / [BENCH] 等）。"""
    f = font("b", 20)
    tw = int(d.textlength(text, font=f))
    rrect(d, [x + 8, y + 8, x + 8 + tw + 24, y + 8 + 34], 6, fill=(12, 14, 20, )[:3],
          outline=fg, width=2)
    d.text((x + 20, y + 13), text, font=f, fill=fg)


def load_scaled(path, box_w, box_h, s):
    im = Image.open(os.path.join(BASE, path)).convert("RGB")
    k = min(box_w / im.width, box_h / im.height, s)
    return im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)


def paste_watch(canvas, im, x, y, label=None, label_fg=OK):
    d = ImageDraw.Draw(canvas)
    d.rectangle([x - 3, y - 3, x + im.width + 2, y + im.height + 2], fill=(50, 56, 70))
    canvas.paste(im, (x, y))
    if label:
        draw_badge_on_image(d, x, y, label, label_fg)


def panel(d, x0, y0, x1, y1, lines, notes, cap):
    """右侧字幕栏：大字幕 + 小字注释 + 素材路径。

    栏宽不同自动降一档字号（watch2 的右栏只有 ~490 px，用 28 号会挤爆）。"""
    maxw = x1 - x0
    narrow = maxw < 620
    f_l = font("b", 25 if narrow else 28)
    llh = 35 if narrow else 40
    f_n = font("r", 20 if narrow else 22)
    nlh = 29 if narrow else 32
    f_c = font("r", 18)
    y = y0
    for ln in lines:
        if ln == "":
            y += 16
            continue
        for w in wrap(d, ln, f_l, maxw):
            if y + llh - 4 > y1:
                raise RuntimeError("字幕溢出（x0=%s）：%r" % (x0, ln))
            d.text((x0, y), w, font=f_l, fill=FG)
            y += llh
    if notes:
        y += 14
        d.line([x0, y, x1, y], fill=(52, 58, 72))
        y += 12
        for n in notes:
            for w in wrap(d, n, f_n, maxw):
                if y + nlh - 2 > y1:
                    raise RuntimeError("注释溢出：%r" % (n,))
                d.text((x0, y), w, font=f_n, fill=DIM)
                y += nlh
    if cap:
        for w in wrap(d, "素材：" + cap, f_c, maxw):
            if y + 24 > y1:
                raise RuntimeError("素材行溢出：%r" % (cap,))
            d.text((x0, y), w, font=f_c, fill=(120, 128, 144))
            y += 26


def draw_log_card(d, x0, y0, x1, card, cap, lines, notes, y_bottom):
    f_h = font("b", 24)
    f_m = font("m", 22)
    f_l = font("b", 26)
    f_n = font("r", 21)
    f_c = font("r", 18)

    inner_w = (x1 - x0) - 36
    # 先算卡片高度（含折行）
    wrapped = []
    for kind, txt in card["lines"]:
        wrapped.append((kind, wrap(d, txt, f_m, inner_w)))
    ch = 30 + 40
    for _, ws in wrapped:
        ch += 31 * len(ws)
    ch += 16
    y1 = y0 + ch
    if y1 > 470:
        raise RuntimeError("日志卡过高（%d px），会挤掉字幕" % ch)

    rrect(d, [x0, y0, x1, y1], 10, fill=PANEL, outline=PANEL_B, width=2)
    d.text((x0 + 18, y0 + 12), card["head"], font=f_h, fill=ACCENT)
    d.line([x0 + 18, y0 + 44, x1 - 18, y0 + 44], fill=(52, 58, 72))
    yy = y0 + 52
    for kind, ws in wrapped:
        color = {"hi": HI, "cmd": CMD, "out": FG, "dim": DIM}[kind]
        for w in ws:
            d.text((x0 + 18, yy), w, font=f_m, fill=color)
            yy += 31

    yy = y1 + 16
    # 素材行固定贴在底部一行，正文不得侵入
    body_bottom = (y_bottom - 26) if cap else y_bottom
    for ln in lines:
        for w in wrap(d, ln, f_l, x1 - x0):
            if yy + 32 > body_bottom:
                raise RuntimeError("日志页字幕溢出：%r" % (ln,))
            d.text((x0, yy), w, font=f_l, fill=FG)
            yy += 36
    for n in notes:
        for w in wrap(d, n, f_n, x1 - x0):
            if yy + 30 > body_bottom:
                raise RuntimeError("日志页注释溢出：%r" % (n,))
            d.text((x0, yy), w, font=f_n, fill=DIM)
            yy += 30
    if cap:
        d.text((x0, y_bottom - 22), "素材：" + cap, font=f_c, fill=(120, 128, 144))


def badge_for(slide, path):
    """根据来源给图片打角标。"""
    if slide["kind"] == "sim":
        return "模拟器截图"
    if "bench" in os.path.basename(path):
        return "真机截图 [BENCH] 注入数据"
    return None


# ---------------------------------------------------------------- 渲染一张分镜


def render_slide(slide):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # 内容底纹
    d.rectangle([0, HEADER_H, W, FOOTER_Y - 1], fill=(20, 23, 30))

    layout = slide["layout"]
    imgs = slide.get("images") or []

    if layout == "title":
        y = 108
        f1 = font("b", 42)
        d.text((56, y), slide["title"], font=f1, fill=FG)
        y += 74
        for ln in slide["lines"]:
            if ln == "":
                y += 12
                continue
            warn = ln.startswith("⚠")
            for w in wrap(d, ln, font("r", 27), 720):
                d.text((56, y), w, font=font("r", 27), fill=WARN if warn else FG)
                y += 38
        if slide["notes"]:
            y += 8
            for n in slide["notes"]:
                for w in wrap(d, n, font("r", 20), 720):
                    d.text((56, y), w, font=font("r", 20), fill=DIM)
                    y += 28
        if imgs:
            im = load_scaled(imgs[0], 390, 450, 1.0)
            paste_watch(img, im, W - 56 - im.width, 120, badge_for(slide, imgs[0]))
        if y > 580:
            raise RuntimeError("title 页文字溢出：%s" % slide["title"])

    elif layout == "watch":
        im = load_scaled(imgs[0], 390, 450, 1.0)
        paste_watch(img, im, 40, 113, badge_for(slide, imgs[0]))
        panel(d, 470, 106, W - 32, 588, slide["lines"], slide["notes"], slide.get("caption"))

    elif layout == "watch2":
        im1 = load_scaled(imgs[0], 340, 392, 0.88)
        im2 = load_scaled(imgs[1], 340, 392, 0.88)
        y = CONTENT_TOP + (512 - im1.height) // 2
        paste_watch(img, im1, 40, y, badge_for(slide, imgs[0]))
        paste_watch(img, im2, 40 + im1.width + 20, y, badge_for(slide, imgs[1]))
        panel(d, 40 + im1.width + im2.width + 40, 106, W - 32, 588,
              slide["lines"], slide["notes"], slide.get("caption"))

    elif layout == "wide":
        # 先量出文字需要多少高度，再把剩下的都给图片（图不会被裁、字不会溢出）
        f_l = font("b", 26)
        f_n = font("r", 21)
        sub_lines = []
        for ln in slide["lines"]:
            sub_lines.extend(wrap(d, ln, f_l, 1200))
        note_lines = []
        for n in slide["notes"]:
            note_lines.extend(wrap(d, n, f_n, 1200))
        cap_h = 26 if slide.get("caption") else 0
        reserved = 14 + len(sub_lines) * 36 + (12 + len(note_lines) * 29 if note_lines else 0) + cap_h
        img_max_h = CONTENT_BOT - CONTENT_TOP - 8 - reserved
        if img_max_h < 180:
            raise RuntimeError("wide 页文字太多，图片只剩 %d px：%s" % (img_max_h, slide["title"]))
        im = load_scaled(imgs[0], 1200, min(392, img_max_h), 1.0)
        x = (W - im.width) // 2
        paste_watch(img, im, x, CONTENT_TOP + 2, badge_for(slide, imgs[0]))
        y = CONTENT_TOP + 2 + im.height + 14
        for w in sub_lines:
            d.text((40, y), w, font=f_l, fill=FG)
            y += 36
        if note_lines:
            y += 12
        for w in note_lines:
            d.text((40, y), w, font=f_n, fill=DIM)
            y += 29
        if slide.get("caption"):
            d.text((40, CONTENT_BOT - 22), "素材：" + slide["caption"],
                   font=font("r", 18), fill=(120, 128, 144))
        if y > CONTENT_BOT - cap_h:
            raise RuntimeError("wide 页文字溢出：%s" % slide["title"])

    elif layout == "log":
        draw_log_card(d, 40, CONTENT_TOP, W - 40, slide["card"], slide.get("caption"),
                      slide["lines"], slide["notes"], CONTENT_BOT)
    else:
        raise RuntimeError("未知 layout: %s" % layout)

    return img


# ---------------------------------------------------------------- ffmpeg


def find_ffmpeg(explicit=None):
    cands = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("PHYWEAR_FFMPEG"):
        cands.append(os.environ["PHYWEAR_FFMPEG"])
    w = shutil.which("ffmpeg")
    if w:
        cands.append(w)
    for pat in ("/tmp/fftools/imageio_ffmpeg/binaries/ffmpeg-*",
                os.path.expanduser("~/.local/lib/python3*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*"),
                os.path.expanduser("~/.local/bin/ffmpeg-*"),
                "/usr/lib/python3/dist-packages/imageio_ffmpeg/binaries/ffmpeg-*"):
        cands.extend(sorted(globmod.glob(pat)))
    try:  # 最后尝试导入 imageio_ffmpeg（若已装）
        import imageio_ffmpeg  # type: ignore
        cands.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass

    tried = []
    for c in cands:
        if not c:
            continue
        tried.append(c)
        if not (os.path.isfile(c) and os.access(c, os.X_OK)):
            continue
        try:
            out = subprocess.run([c, "-hide_banner", "-version"], capture_output=True,
                                 text=True, timeout=30)
        except Exception:
            continue
        if out.returncode == 0:
            ver = (out.stdout or out.stderr).splitlines()[0]
            print("[ffmpeg] %s\n         %s" % (c, ver))
            return c
    sys.exit("找不到可执行的 ffmpeg。尝试过：\n  " + "\n  ".join(tried) +
             "\n\n离线安装（免 sudo，约 30 MB）：\n"
             "  python3 -m pip download --no-deps --dest /tmp/ffdl imageio-ffmpeg\n"
             "  mkdir -p /tmp/fftools && python3 -c \"import zipfile,glob;\"\\\n"
             "    \"zipfile.ZipFile(glob.glob('/tmp/ffdl/imageio_ffmpeg-*.whl')[0]).extractall('/tmp/fftools')\"\n"
             "  chmod +x /tmp/fftools/imageio_ffmpeg/binaries/ffmpeg-*\n"
             "  export PHYWEAR_FFMPEG=$(ls /tmp/fftools/imageio_ffmpeg/binaries/ffmpeg-*)")


def find_ffprobe(ffmpeg, explicit=None):
    cands = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("PHYWEAR_FFPROBE"):
        cands.append(os.environ["PHYWEAR_FFPROBE"])
    w = shutil.which("ffprobe")
    if w:
        cands.append(w)
    for pat in ("/tmp/fftools/**/ffprobe*", os.path.expanduser("~/.local/bin/ffprobe*")):
        cands.extend(sorted(globmod.glob(pat, recursive=True)))
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            try:
                out = subprocess.run([c, "-hide_banner", "-version"], capture_output=True,
                                     text=True, timeout=30)
                if out.returncode == 0:
                    return c
            except Exception:
                continue
    return None


def probe(path, ffmpeg, ffprobe):
    """返回 (info:dict, raw:str)。imageio-ffmpeg 只带 ffmpeg、不带 ffprobe，
    此时退回 `ffmpeg -i`（同样报出容器/流事实：时长、分辨率、编码、pix_fmt）。"""
    if ffprobe:
        cmd = [ffprobe, "-hide_banner", "-v", "error", "-show_format", "-show_streams", path]
        r = subprocess.run(cmd, capture_output=True, text=True)
        raw = r.stdout
        info = {}
        m = re.search(r"^duration=([\d.]+)", raw, re.M)
        if m:
            info["duration"] = float(m.group(1))
        m = re.search(r"^width=(\d+)", raw, re.M)
        m2 = re.search(r"^height=(\d+)", raw, re.M)
        if m and m2:
            info["width"], info["height"] = int(m.group(1)), int(m2.group(1))
        m = re.search(r"^codec_name=(\S+)", raw, re.M)
        if m:
            info["codec"] = m.group(1)
        m = re.search(r"^pix_fmt=(\S+)", raw, re.M)
        if m:
            info["pix_fmt"] = m.group(1)
        m = re.search(r"^nb_frames=(\d+)", raw, re.M)
        if m:
            info["nb_frames"] = int(m.group(1))
        m = re.search(r"^bit_rate=(\d+)", raw, re.M)
        if m:
            info["bit_rate"] = int(m.group(1))
        info["tool"] = ffprobe
        return info, raw

    r = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True)
    raw = r.stderr
    info = {"tool": ffmpeg + "  (-i 模式：本机 imageio-ffmpeg 包未附带独立 ffprobe)"}
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", raw)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Video:\s*(\w+).*?,\s*(\d+)x(\d+)", raw)
    if m:
        info["codec"], info["width"], info["height"] = m.group(1), int(m.group(2)), int(m.group(3))
    m = re.search(r"Video:.*?(yuv\w+)", raw)
    if m:
        info["pix_fmt"] = m.group(1)
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", raw)
    if m:
        info["fps"] = float(m.group(1))
    m = re.search(r"bitrate:\s*(\d+)\s*kb/s", raw)
    if m:
        info["bit_rate"] = int(m.group(1)) * 1000
    return info, raw


def extract_frames(path, ffmpeg, times, outdir):
    os.makedirs(outdir, exist_ok=True)
    made = []
    for i, t in enumerate(times):
        o = os.path.join(outdir, "check-frame-%d.png" % (i + 1))
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", str(t), "-i", path, "-frames:v", "1", o], check=True)
        made.append(o)
    return made


# ---------------------------------------------------------------- 主流程


BASE = "."


def check_assets(repo):
    missing = [p for p in ASSETS if not os.path.isfile(os.path.join(repo, p))]
    if missing:
        print("✗ 缺少 %d 个素材，拒绝继续（不静默跳过、不用占位图）：" % len(missing),
              file=sys.stderr)
        for p in missing:
            print("   - " + p, file=sys.stderr)
        sys.exit(2)
    # 顺带校验分镜里引用的每张图都在清单里
    used = set()
    for s in SLIDES:
        for p in (s.get("images") or []):
            used.add(p)
    unknown = sorted(used - set(ASSETS))
    if unknown:
        sys.exit("✗ 分镜引用了未登记的素材（会导致来源角标失真）：%s" % unknown)
    return len(ASSETS)


def validate_plan():
    tot = sum(s["secs"] for s in SLIDES)
    if tot != TOTAL_SECS:
        sys.exit("✗ 分镜总时长 %d s ≠ 脚本 v4 的 %d s（请同步 docs/08 时间轴）" % (tot, TOTAL_SECS))
    if TOTAL_SECS > MAX_SECS:
        sys.exit("✗ 总时长 %d s 超过交付上限 %d s" % (TOTAL_SECS, MAX_SECS))
    seen = {}
    for name, want in SEG_PLAN:
        seen[name] = seen.get(name, 0) + want
    got = {}
    for s in SLIDES:
        got[s["seg"]] = got.get(s["seg"], 0) + s["secs"]
    for k in seen:
        if seen[k] != got.get(k, 0):
            sys.exit("✗ 「%s」脚本 %d s，分镜 %d s —— 不一致" % (k, seen[k], got.get(k, 0)))
    return tot


def main():
    global BASE
    ap = argparse.ArgumentParser(description="生成 PhyWear 演示视频 v0.1（静态素材版）")
    ap.add_argument("--repo", default=os.path.expanduser("~/work/contest2026_427_xinpingqihe"))
    ap.add_argument("--out", default=None,
                    help="默认 <repo>/docs/evidence/demo-20260915/phywear-demo-v0.1.mp4")
    ap.add_argument("--fps", type=int, default=FPS_DEFAULT)
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--ffmpeg", default=None)
    ap.add_argument("--ffprobe", default=None)
    ap.add_argument("--dry-run", action="store_true", help="只渲染分镜 PNG，不编码")
    ap.add_argument("--only", type=int, default=None, help="配合 --dry-run：只渲染第 N 张（1 起）")
    ap.add_argument("--extract-frames", nargs="?", const="", default=None,
                    metavar="t1,t2,t3", help="编码后抽帧 PNG（默认 5,150,290）")
    ap.add_argument("--check-dir", default=None, help="抽帧输出目录")
    a = ap.parse_args()

    repo = os.path.abspath(os.path.expanduser(a.repo))
    BASE = repo
    if not os.path.isdir(repo):
        sys.exit("✗ --repo 不存在：%s" % repo)
    out = a.out or os.path.join(repo, "docs/evidence/demo-20260915/phywear-demo-v0.1.mp4")
    out = os.path.abspath(os.path.expanduser(out))
    check_dir = a.check_dir or os.path.join(os.path.dirname(out), "check-frames")

    print("== PhyWear 演示视频 v0.1（静态素材版）==")
    print("repo      : %s" % repo)
    print("out       : %s" % out)
    n_assets = check_assets(repo)
    print("素材校验  : %d/%d 全部存在" % (n_assets, n_assets))
    total = validate_plan()
    print("分镜      : %d 张 / %d 段 / %d s (%d:%02d) / %d fps / %dx%d"
          % (len(SLIDES), len(SEG_PLAN), total, total // 60, total % 60, a.fps, W, H))

    init_fonts()
    print("字体      : %s (index %d)\n            %s (index %d)"
          % (FONT_SANS_BOLD, SANS_INDEX, FONT_MONO, MONO_INDEX))

    if a.dry_run:
        d = os.path.join(os.path.dirname(out), "slides-preview")
        os.makedirs(d, exist_ok=True)
        for i, s in enumerate(SLIDES, 1):
            if a.only and i != a.only:
                continue
            img = render_slide(s)
            d0 = ImageDraw.Draw(img)
            draw_header(d0, s)
            draw_footer(d0, sum(x["secs"] for x in SLIDES[:i - 1]) + s["secs"] / 2)
            p = os.path.join(d, "slide-%02d-%s.png" % (i, s["seg"].replace(" ", "")))
            img.save(p)
            print("  %s  (%s, %ds)" % (p, s["title"], s["secs"]))
        print("dry-run 完成：%d 张 PNG → %s" % (len(SLIDES) if not a.only else 1, d))
        return 0

    ffmpeg = find_ffmpeg(a.ffmpeg)
    ffprobe = find_ffprobe(ffmpeg, a.ffprobe)
    print("[probe]   : %s" % (ffprobe or "（未找到 ffprobe，退回 `ffmpeg -i`）"))

    # ---- 预渲染所有分镜底图（内存里只有静态部分，逐帧只重画页脚）
    t0 = time.time()
    bases = []
    for i, s in enumerate(SLIDES, 1):
        img = render_slide(s)
        d = ImageDraw.Draw(img)
        draw_header(d, s)
        bases.append(img)
    print("[render]  %d 张分镜底图 %.1fs" % (len(bases), time.time() - t0))

    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-stats", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
           "-r", str(a.fps), "-i", "-",
           "-an",
           "-c:v", "libx264", "-preset", a.preset, "-crf", str(a.crf),
           "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.0",
           "-movflags", "+faststart", out]
    print("[encode]  %s" % " ".join(cmd))
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)

    frames_total = int(round(total * a.fps))
    fi = 0
    t0 = time.time()
    try:
        for slide, base in zip(SLIDES, bases):
            nf = int(round(slide["secs"] * a.fps))
            for k in range(nf):
                t_abs = (fi + 0.5) / a.fps
                fr = base.copy()
                draw_footer(ImageDraw.Draw(fr), t_abs)
                proc.stdin.write(fr.tobytes())
                fi += 1
            if fi % 250 < nf:
                el = time.time() - t0
                sys.stderr.write("\r  帧 %5d/%5d  %.0f%%  %.0fs"
                                 % (fi, frames_total, 100.0 * fi / frames_total, el))
                sys.stderr.flush()
    except BrokenPipeError:
        err = proc.stderr.read().decode("utf-8", "replace")
        sys.exit("✗ ffmpeg 提前退出：\n" + err[-3000:])
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
    rc = proc.wait()
    err = proc.stderr.read().decode("utf-8", "replace")
    sys.stderr.write("\n")
    if rc != 0:
        sys.exit("✗ ffmpeg 退出码 %d：\n%s" % (rc, err[-3000:]))
    print("[encode]  完成 %.1fs，%d 帧" % (time.time() - t0, fi))

    # ---- 验证
    info, raw = probe(out, ffmpeg, ffprobe)
    size = os.path.getsize(out)
    print("\n== 产物验证 ==")
    print("path      : %s" % out)
    print("bytes     : %d (%.2f MiB)" % (size, size / 1048576.0))
    for k in ("duration", "width", "height", "codec", "pix_fmt", "fps", "bit_rate"):
        if k in info:
            print("%-10s: %s" % (k, info[k]))
    print("probed by : %s" % info.get("tool"))

    ok = True
    if info.get("duration") is None or info["duration"] > MAX_SECS + 0.6:
        print("✗ 时长校验失败"); ok = False
    if info.get("width") != W or info.get("height") != H:
        print("✗ 分辨率校验失败"); ok = False
    if (info.get("width", 0) % 2) or (info.get("height", 0) % 2):
        print("✗ 宽高非偶数"); ok = False
    if info.get("codec") != "h264":
        print("✗ 编码不是 H.264（%s）" % info.get("codec")); ok = False
    if info.get("pix_fmt") != "yuv420p":
        print("✗ pix_fmt 不是 yuv420p（%s）" % info.get("pix_fmt")); ok = False
    print("校验      : %s" % ("✅ 全部通过" if ok else "❌ 有失败项"))

    if a.extract_frames is not None:
        times = [float(x) for x in a.extract_frames.split(",")] if a.extract_frames else [5.0, 150.0, 290.0]
        made = extract_frames(out, ffmpeg, times, check_dir)
        print("抽帧      :")
        for t, p in zip(times, made):
            print("  t=%-6s %s" % (t, p))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
