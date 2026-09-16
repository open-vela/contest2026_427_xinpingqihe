#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 ui-ux-pro-max-skill 的设计数据库 CSV 导入成**本板可用的 LVGL token 报告**。

背景（P1-2 / 问题 2）：
  上游 Skill 的数据层（风格清单 / 色板 / 字体配对 / 推理规则 / UX 指南）是**栈无关**的，
  值得吸纳；但它的产出层是 HTML+Tailwind，目标浏览器。本板是 390×450 AMOLED +
  SF32LB52 + EPIC + LVGL 9，所以只把**数据**搬过来、按本板约束**筛掉高负载项**。

为什么不能直接照搬：
  · 颜色要转 RGB565（16bpp 面板）；
  · 风格里凡是依赖 shadow / blur / glass / neumorph / 渐变 / 3D 的，在本板会被 EPIC
    拒绝并回退 CPU 软渲染（见 skills/phywear-lvgl-ui.md §4），必须标为"禁用"；
  · 字体是 5 个固定字号的位图子集，Web 字体配对只能映射到"层级意图"，不能映射字体名。

用法：
    python3 tools/phywear/import_ui_skill.py --sample          # 用内置样例自检（无网络也能跑）
    python3 tools/phywear/import_ui_skill.py --in <skill目录>   # 导入真实 CSV（目录或单个 .csv）
    python3 tools/phywear/import_ui_skill.py --in <目录> --out docs/generated

**诚实声明**：本仓库的开发环境无法访问 github/raw.githubusercontent（DNS 解析到非公网地址），
因此"真实 CSV"这条路径**未经实测**；`--sample` 路径已实测。列名识别采用关键字匹配 +
"识别不到的列如实报告"，不做猜测填充。
"""

import argparse
import csv
import glob
import os
import sys

# 列名关键字 → 类别（大小写不敏感，任一命中即归类）
COLUMN_HINTS = {
    "style": ["style", "风格", "name", "category", "vibe", "mood"],
    "palette": ["palette", "color", "colour", "色", "hex", "bg", "background"],
    "font": ["font", "type", "字体", "pairing", "heading", "body"],
    "rule": ["rule", "when", "trigger", "recommend", "推理", "规则"],
    "ux": ["ux", "guideline", "do", "dont", "don't", "指南", "accessib"],
}

# 命中即判为"本板高负载，禁用"（EPIC 不支持 → CPU 软渲染）
HEAVY_KEYWORDS = ["shadow", "blur", "glass", "neumorph",
                  "3d", "glow", "backdrop", "阴影", "模糊", "拟态"]

# 条件可用：渐变**只有 2 段 + HOR/VER 方向**才走 EPIC（见 lv_draw_sifli_epic.c 的
# FILL 分支），所以不能一刀切禁用，标为"需人工确认"。
CONDITIONAL_KEYWORDS = ["gradient", "渐变"]


def hex_to_rgb565(h: str):
    """'#RRGGBB' → (r5g6b5, r8, g8, b8)；失败返回 None。"""
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None
    return (((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3), r, g, b)


def luminance(r, g, b):
    def f(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = luminance(*a), luminance(*b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def classify(header):
    out = {}
    for i, col in enumerate(header):
        low = col.lower()
        for cat, keys in COLUMN_HINTS.items():
            if any(k in low for k in keys):
                out.setdefault(cat, []).append((i, col))
    return out


def read_rows(path):
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        rd = csv.reader(f)
        rows = [r for r in rd if any(c.strip() for c in r)]
    return rows


def import_file(path, out_dir):
    rows = read_rows(path)
    if not rows:
        return None
    header = rows[0]
    body = rows[1:]
    cats = classify(header)
    name = os.path.basename(path)

    lines = ["### `%s`（%d 行数据，%d 列）" % (name, len(body), len(header)), ""]
    lines.append("识别到的列：")
    for cat in ["style", "palette", "font", "rule", "ux"]:
        if cat in cats:
            lines.append("- **%s**：%s" % (cat, ", ".join("`%s`" % c for _, c in cats[cat])))
    unknown = [c for i, c in enumerate(header)
               if not any(i in [j for j, _ in v] for v in cats.values())]
    if unknown:
        lines.append("- 未识别列（如实列出，不猜测）：%s" % ", ".join("`%s`" % c for c in unknown))
    lines.append("")

    # 风格筛选：高负载直接标禁用
    if "style" in cats:
        si = cats["style"][0][0]
        heavy, cond, ok = [], [], []
        for r in body:
            if si >= len(r):
                continue
            text = " ".join(r).lower()
            if any(k in text for k in HEAVY_KEYWORDS):
                heavy.append(r[si])
            elif any(k in text for k in CONDITIONAL_KEYWORDS):
                cond.append(r[si])
            else:
                ok.append(r[si])
        lines.append("**低负载可用风格（%d）**：%s" % (len(ok), ", ".join(ok[:20]) or "（无）"))
        lines.append("")
        lines.append("**条件可用（%d）**：%s —— 渐变必须**仅 2 段且方向为 HOR/VER**，"
                     "否则 EPIC 拒绝 → CPU 软渲染，需人工确认。"
                     % (len(cond), ", ".join(cond[:20]) or "（无）"))
        lines.append("")
        lines.append("**本板禁用（依赖 shadow/blur/3D/毛玻璃，EPIC 会回退 CPU，%d）**：%s"
                     % (len(heavy), ", ".join(heavy[:20]) or "（无）"))
        lines.append("")

    # 色板：转 RGB565 + 对比度检查
    if "palette" in cats:
        pi = cats["palette"][0][0]
        seen = set()
        conv = []
        for r in body:
            if pi < len(r):
                v = hex_to_rgb565(r[pi])
                if v and r[pi] not in seen:
                    seen.add(r[pi])
                    conv.append((r[pi], v))
        if conv:
            lines.append("**色值 → RGB565（本板 %d 种）**：" % len(conv))
            lines.append("")
            lines.append("| 源色 | RGB565 | 对比度（**按文字色**对近黑底算） |")
            lines.append("|---|---|---|")
            for src, (c565, rr, gg, bb) in conv[:32]:
                on_bg = contrast((rr, gg, bb), (10, 12, 16))   # 近黑背景
                note = ("当正文色可用" if on_bg >= 4.5
                        else ("当大字号可用" if on_bg >= 3.0 else "只宜当背景/装饰"))
                lines.append("| `%s` | `0x%04X` | %.2f:1 → %s |" % (src, c565, on_bg, note))
            lines.append("")

    if "font" in cats:
        lines.append("**字体配对 → 本板只能映射层级意图**（无 Web 字体导入；本板 5 个位图子集字号："
                     "`pw_font_14/16/20/24/28` + Montserrat 数字）。")
        lines.append("")
    if "rule" in cats or "ux" in cats:
        n = sum(len(cats[k]) for k in ("rule", "ux") if k in cats)
        lines.append("**规则/指南列**（%d 列）：原文保留在报告里供人工筛选，不自动改代码。" % n)
        lines.append("")

    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, name.replace(".csv", "") + ".md")
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return dst


SAMPLE_CSV = """style,category,hex,notes
Flat Dark,low-load,#0b0e12,solid blocks, no radius
Bento Grid,low-load,#161b22,rect cards only
Glassmorphism,web,#88aaff,backdrop blur
Neumorphism,web,#ddeeff,dual shadow
Linear Gradient,direction,#4fc3f7,2-stop H/V only ok
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", help="上游 skill 目录或单个 .csv")
    ap.add_argument("--out", default="/tmp/phywear_ui_skill_import")
    ap.add_argument("--sample", action="store_true", help="用内置样例自检（无网络可用）")
    args = ap.parse_args()

    if args.sample:
        os.makedirs(args.out, exist_ok=True)
        p = os.path.join(args.out, "sample_input.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write(SAMPLE_CSV)
        got = import_file(p, args.out)
        print("样例导入 → %s" % got)
        print(open(got, encoding="utf-8").read())
        return 0

    if not args.src:
        print("需要 --in <目录/CSV> 或 --sample")
        return 2
    files = ([args.src] if args.src.endswith(".csv")
             else sorted(glob.glob(os.path.join(args.src, "**", "*.csv"), recursive=True)))
    if not files:
        print("❌ 没找到 CSV：%s" % args.src)
        return 1
    n = 0
    for f in files:
        got = import_file(f, args.out)
        if got:
            print("导入 %s → %s" % (os.path.basename(f), got))
            n += 1
    print("共导入 %d 个 CSV；报告目录 %s" % (n, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
