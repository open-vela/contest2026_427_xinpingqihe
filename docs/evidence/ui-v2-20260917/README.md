# UI v2 改版 + BUG 修复 — 证据包（2026-09-17）

真机：立创·黄山派 SF32LB52-MOD-1-N16R8，CO5300 390×450。
本轮改动全部只提交本地仓库（不推送）；回退锚点 **`git tag pre-ui-redesign-20260917`**。

---

## 1. 修复的 BUG（用户报："多个界面的可用内容缺失到只剩一个"）

**现象**：主页点板块（工具/力学/声学…）进去后，列表**只剩一行**，而且显示的是**最后一项**。

**定位（真机复现 + 代码级）**：`pw_board_list()` 的错峰入场调用
`pw_motion_slide_in_y_at()`，早期实现在函数内部用 `lv_obj_get_y()` 取"动画终点"。
LVGL v9 里 `lv_obj_set_y()` 只写**样式 Y**，而 `lv_obj_get_y()` 读的是**已布局的 coords**
（`coords.y1 - parent->coords.y1 + scroll_y - space_top`）—— 刚创建、布局尚未刷新的行两者不一致，
于是**同批 5 行的动画终点全被算成同一个 y**，5 行完全重叠，只有最后绘制的那一行可见。

**对照证据**：

| 文件 | 说明 |
|---|---|
| `01_board_before_1row.png` | 修复前：工具板块只有"水平仪"一行（= 表里最后一项，压在其它行上） |
| `02_board_after_5rows.png` | 修复后：加速度频谱 / 倾角 / 磁性标尺 / 磁场频谱 / 水平仪 **5 行全部列出** |

**修法**：API 改为**显式传基准 y**（`pw_motion_slide_in_y_at(obj, base_y, from_dy, dur, delay)`），
并在启动动画前先把对象落到起点（避免延时期间"先出现再跳"）；无延时版 `pw_motion_slide_in_y()`
改用 `lv_obj_get_y_aligned()`（样式值）——同类陷阱一并修掉。

**顺带**：板块行高 76 → 64 px（5 行 360 px 放进 396 px 内容区；原来第 5 行会被裁掉 24 px）。

---

## 2. 交互补强（用户报："按键无反应 / 部分按钮没有"）

- **统一按压态做成"样式"而不是逐个注册事件**：`pw_press_style()`（已挂在 `pw_card_new()` 上）
  = 按下 `translate_y +3px` + 底色 → Muted + **120 ms C4 过渡**；因为所有卡片/按钮/列表行/宫格
  都由 `pw_card_new()` 创建，**整个 App 的按压反馈一次性统一**，不再"有的有有的没有"。
- **修掉"按在图标上没反应"**：`lv_obj_create()` 默认带 `CLICKABLE`，装饰性子件（宫格图标块、
  板块行色条）会把按压状态从卡片上**抢走**。新增 `pw_deco()` 取消其可点性，已用于这两处。
- **切屏转场**：所有走 `pw_topbar()` 的页面，内容区从 **+16 px 落位（C2 260 ms，单对象）**；
  主页 8 块宫格 **+12 px 错峰入场（40 ms 步进）**；IMU 与原始传感器的多页屏改为**动画滚动**
  （`LV_ANIM_ON`，原来是一步跳过去）。
- 全 App 扫描确认：只剩**分页圆点**是 `lv_obj_create` 且无按压态 —— 它是装饰件，本就不该响应。

> 动效约束（沿用 `stacks/lvgl.csv` 与 `motion-lvgl.csv`）：单次 ≤ 320 ms、单对象、不循环、
> 只改几何/底色，不碰 transform/圆角/阴影。

---

## 3. 视觉（先出 HTML 设计稿，再落到 LVGL）

- **设计稿**：`docs/design/phywear-ui-v2-mockup.html`（渲染图 `docs/design/phywear-ui-v2-mockup.png`，
  用 headless Chrome 出图）。色板/字号层级取自
  `third_party/ui-ux-pro-max/data/colors-lvgl.csv`、`typography-lvgl.csv`；
  上游 `scripts/design_system.py "smartwatch realtime sensor dashboard dark"` 推荐的正是这套暗色板
  （Background `#0F172A` / Card `#1B2336` / Muted `#272F42` / Muted FG `#94A3B8` / Border `#475569`，
  Accent `#22C55E`）；它推荐的 Glassmorphism 依赖**模糊**，本板 EPIC 不支持，故用
  "分层纯色 + 1px 描边 + 强调色小块"翻译。
- **本轮落地**：卡片统一 **1px hairline 描边**（`PW_COL_LINE`，替代 EPIC 禁用的阴影）；
  宫格图标块 **圆形 → 圆角方块**（更接近现代 App 观感）；原始传感器页加 **3px 强调条章节标签**。

---

## 4. 一并修掉的排版缺陷

子代理逐图审查 8 张真机截图时发现：**加速度频谱页**的 "x: 频率 0.25 Hz" 原来放在卡片 `(12,228)`，
而卡片高 246、控件条就在那个位置 —— 文字**压在 X-/X+/Y-/Y+ 按钮上**。
现改为与 Y 轴标签同一行**右对齐**（`LV_ALIGN_TOP_RIGHT, -12, 4`），不再占用图区、不与控件重叠。
见 `04_spec_overlap_fixed.png`。

---

## 5. 帧率与资源（同页同命令、30 s 心跳、`phywear lang zh cap raw`）

| 构建 | loops/s | fps |
|---|---|---|
| Stage 前（色板 v1 + 动效 6 处） | **208** | 12 |
| Stage A + B（含 1px 描边） | **200** | 12 |
| 只把描边关掉（`PW_CARD_LINE=0`） | **203** | 12 |

- **fps 三档完全相同（12）**；描边的成本约 **1.5% loops/s**（203 → 200），换来的观感提升明显，保留。
- flash：2,485,140 → **2,486,532 B**；**SRAM 489,136 B 未变（0 B 增长）**。
- 板上固件 = 本地产物 md5 **`256e0612f3548f870310f2789f0fb6a1`**（2,486,532 B）。
  ⚠️ 固件非可重现构建，md5 只在"板上 vs 同一产物"时有意义。

---

## 6. 仍未做（如实清单）

1. 设计稿里的**逐页版式铺开**（读数页 KV 行 + hairline 分隔、状态点、图表区留白）本轮只落地了
   全局 token 与少数页面，**没有逐页重构**。
2. **数值变化脉冲（D1）、读数页淡入（C1）**未接。
3. **按压手感需要人手指确认**：真机没有触摸注入（`phywear tap` 只在模拟器编入
   `CONFIG_UINPUT_TOUCH` 时存在），远程无法代按。
4. `pwshot.py sweep` 在**持续重绘页**上仍会被"行一致性"校验拒绝并提前中止（取图工具限制），
   本轮证据仍是**单页单次启动**抓图；声学 4 页、秒表、光门、掌声、设置、关于仍无本轮截图。
5. 回退手段：`git tag pre-ui-redesign-20260917`；宏 `PW_UI_MOTION=0`（动效全关）、
   `PW_CARD_LINE=0`（描边关）、`PW_CARD_RADIUS`（圆角）。
