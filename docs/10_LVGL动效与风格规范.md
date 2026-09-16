# 10 · LVGL 动效与风格规范（P1-2 落地记录 + ui-ux-pro-max-skill 吸纳）

> 阶段：**已开工并落盘**（2026-09-16）。前置审查见上一轮《实验性审查报告》；
> 回退锚点：参赛仓 git tag **`pre-motion-skill-20260916`**（在本次改动之前）。

## 0. 两个问题的一句话结论

- **问题 1（解析式 vs 查表）**：曲线形状固定、每秒被算几百次，**查表法可行且划算** ——
  离线按帧率采样存 flash、运行时只做"索引 + 线性插值"，实测主机上 **0.59 ns/次 vs 解析式 6.40 ns/次（×10.8）**，
  代价是 **516 B flash、0 B SRAM**，精度损失在 LVGL 的 0/1024 分辨率下约 **2.4 个步进（视觉不可辨）**。
- **问题 2（HTML 版 Skill → LVGL 版）**：**数据层可吸纳**（风格/色板/字体配对/推理规则/UX 指南的分类骨架），
  **栈层与输出层必须重写**（HTML+Tailwind → LVGL 样式属性与 obj 配置），**Web 专属全部裁掉**
  （Google Fonts 导入、响应式断点、键盘 focus、hover、React 等）。

---

## 1. 问题 1：查表法动效（已实现）

### 1.1 曲线与存储

两条曲线（都归一化到 u = t/duration ∈ [0,1]）：

| 曲线 | 公式 | 参数 | 用途 |
|---|---|---|---|
| `spring` | 欠阻尼二阶阶跃 `1 − e^(−ζωu)·[cos(ω_d u) + (ζω/ω_d)·sin(ω_d u)]`，`ω_d=ω√(1−ζ²)` | ζ=0.40、ω=12，过冲峰值 **1.2494** | 进入/回弹/归位（默认手感） |
| `decay` | `e^(−6u)·sin(2π·2u)` 按峰值归一化 | a=6、f=2 | "抖一下"的提示反馈 |

- 存储：**Q14 定点（1.0 = 16384）、`int16_t`、129 点/条**，两条共 **516 B，落 flash(.rodata)**。
  选 Q14 而非 Q16：spring 有过冲（峰值 1.25），Q16 表示不到 1.0 以上；Q16+uint32 会让表涨到 1 KB 且插值要走 64 位。
- **末帧严格到位**：原始 `s_raw(1)=1.0035703`，若直接制表，动画最后一拍会从 1.0036 跳回 end_value
  （位移 200 px 时约 0.7 px 的"哒"一下）。所以曲线整体除以 `s(1)`（系数 **0.9964424**，
  由生成器输出、解析式实现引用同一常量），并把 `u ≥ 1024` 强制返回 `1.0`。

### 1.2 精度（实测，不是估计）

`python3 tools/phywear/gen_motion_table.py` 的输出：

| 曲线 | 表长 | 插值最大误差 | 出现在 | 量化上界 |
|---|---|---|---|---|
| spring | 129×2 B | **1.06e-03** | u=0.004（起步段曲率最大处） | 3.1e-05 |
| decay | 129×2 B | **2.29e-03** | u=0.020（首个峰） | 3.1e-05 |

- 误差 ∝ h²：N 从 64 → 128，误差降到 1/4，flash 只多 260 B、**运行时成本完全不变**，所以取 128。
- 换算到 LVGL：path 内部把曲线映射到 0…1024，`2.29e-3 × 1024 ≈ **2.4 个步进**` —— 对位置动画不可辨。
- 主机单测 `pw_motion_selftest`：**rc=0，max_err=7.93e-04**（端点必须是 0 与严格 1.0、越界时间收敛到末值、
  过冲峰值必须落在 1.15~1.35 之间、查表与解析逐点误差 < 3e-3）。

### 1.3 计算节省量（实测 + 待补）

| 环境 | 查表 | 解析式（expf+sinf+cosf） | 倍数 | 出处 |
|---|---|---|---|---|
| 主机 x86-64 `-O2`（20 万次） | **0.59 ns/次** | **6.40 ns/次** | **×10.8** | `pw_math_hosttest --bench` |
| 真机 SF32LB52 | ⏳ **待补测** | ⏳ | ⏳ | `phywear motionbench 20000` |

> 真机数字待补的原因如实说明：烧录过程中**板子从 USB 总线上掉线**（`lsusb` 里 CH340 已消失，
> 不是 `/dev` 节点问题），需要人工重新插拔才能继续。主机比值可作参考但**不能替代真机实测**
> （Cortex-M33 上 `expf/sinf` 是 libm 调用，差距通常比 x86 更大）。

### 1.4 实施步骤（已按此执行）

1. `tools/phywear/gen_motion_table.py` 离线生成曲线 + 精度报告 + 归一化系数（**唯一真值源**，表文件禁止手改）。
2. `pw_motion.{c,h}` 纯算法实现：整数查表 + 线性插值；**解析式实现始终保留**（对照计时 + 回退）。
3. `pw_motion_lvgl.{c,h}` 适配层：`pw_motion_path_spring` / `pw_motion_path_decay` 直接替换
   `lv_anim_path_linear`；`pw_motion_slide_in_y()` 便捷滑入。
4. 真机计时入口 `phywear motionbench [iters]`（用 `clock_gettime` 分别计时两条实现）。
5. 接入一处可见动效：切到「轨迹」页时图形卡以弹簧滑入（`phywear_imu.c` 的 `imu_set_page`，单对象 320 ms）。
6. 主机单测纳入 `pw_motion.c` + `pw_motion_table.c`（`hosttest_math.sh`）。

**成本实测**：固件 2,204,456 → **2,206,056 B（+1,600 B，含计时打印修正后的重建）**，md5 `0c9f8e90574cad28fba6f33a7b980f06`，flash 13.14% → **13.15%**，
**SRAM 不变（487,460 B / 92.98%）** —— 表在 flash，模块零静态 SRAM，符合"SRAM 不新增"。

---

## 2. 问题 2：ui-ux-pro-max-skill → LVGL 版（已实现）

来源：<https://github.com/nextlevelbuilder/ui-ux-pro-max-skill>（MIT）。
**本次开发环境无法访问 github/raw.githubusercontent（DNS 解析到非公网地址）**，因此改造是
**按其"设计数据库"的分类骨架**做的，不照抄具体条目；真实 CSV 的导入路径见 §2.4（已实现但**未用真数据实测**）。

### 2.1 可吸纳 / 需重写 / 裁掉

| 层 | 处理 | 说明 |
|---|---|---|
| 数据层：风格清单 | **吸纳**（重新定值） | 只保留低负载方向：Flat Dark、Bento 网格、Instrument；Neumorphism/Glassmorphism/3D 明确禁用（依赖阴影/模糊/渐变，EPIC 会回退 CPU） |
| 数据层：色板 | **吸纳** | 用本仓既有 token（`PW_COL_*`），新色必须给 RGB565 与对比度检查 |
| 数据层：字体配对 | **吸纳为"层级意图"** | 本板只有 5 个位图子集字号（`pw_font_14/16/20/24/28`）+ Montserrat 数字；Web 字体名无意义，且**新增中文必须重跑 `gen_fonts.sh`** |
| 数据层：推理规则 | **吸纳** | "什么场景用什么风格"改为按页面类型分派（读数页→Instrument、入口页→Bento…） |
| 数据层：UX 指南 | **吸纳**（阈值按本板改） | 触控目标 ≥44 px、>300 ms 操作必须有进度反馈、失败如实显示、状态色+文字双编码 |
| 栈层 | **重写** | HTML/Tailwind → LVGL 9：`bg-color/opa`、`radius`、`bg_grad`、`text_font`、`flex/grid`、`lv_anim` |
| 输出层 | **重写** | 不再产出 HTML 片段，改为 **LVGL 样式属性/obj 配置**（本仓风格：`pw_card_new`/`pw_label_new` 等封装） |
| Web 专属 | **裁掉** | Google Fonts 动态导入、`@media` 响应式断点、键盘 focus 环、hover 态、CSS 变量/预处理器、Tailwind JIT、React/Vue 组件、SVG 图标、浏览器惯性滚动 |

### 2.2 关键映射表（Web → 本板 LVGL）

| Web 概念 | LVGL 9 API | 本板约束（性能铁律） |
|---|---|---|
| `background-color` | `lv_obj_set_style_bg_color/opa` | 纯色，AMOLED 近黑省电 |
| `border-radius` | `lv_obj_set_style_radius` | **必须 0**：EPIC 的 FILL/BORDER 分支 `radius != 0 → return 0`（回退 CPU）；确需圆角须面积 ≤32×32 且不在每帧重绘路径 |
| `box-shadow` | `lv_obj_set_style_shadow_*` | **禁用**（`LV_DRAW_SW_SHADOW_CACHE_SIZE=0`，且属复杂路径） |
| `linear-gradient` | `lv_obj_set_style_bg_grad_*` | **仅 2 段、方向仅 HOR/VER**（EPIC 限制） |
| `filter: blur` | `lv_obj_set_style_blur_*` | **禁用** |
| `font-family` | `lv_obj_set_style_text_font` | 5 个位图子集字号 + Montserrat 数字；中文进表用 `gen_fonts.sh` |
| `flex` / `grid` | `lv_obj_set_flex_flow` / `grid_dsc` | 固定 390×450，优先绝对定位（`lv_obj_set_pos`），减少布局重排 |
| `transition` + `cubic-bezier` | `lv_anim` + `lv_anim_set_path_cb` | **用 `pw_motion_path_spring`（查表）**；单属性、≤320 ms、不循环 |
| `transform: scale/rotate` | `lv_obj_set_style_transform_*` | **避免**（走 SW 变换，EPIC 不支持） |
| `@media` 断点 | — | 裁掉（单一分辨率） |
| `:focus-visible` | `lv_group` | 裁掉（无键盘；触控为主） |

### 2.3 交付物

| 文件 | 内容 |
|---|---|
| `apps/examples/phywear/skills/phywear-lvgl-ui.md` | **LVGL 版 Skill 本体**（7 节：风格/色板/字体/性能铁律/UX/动效/裁掉清单），随固件安装到 `/data/agent/skills/`，AI Agent 可直接读到 |
| `pw_skill_blob.c` | 由 `gen_skill_blob.py` 重新生成（现在 **2 个 skill**，`--check` 通过） |
| `tools/phywear/import_ui_skill.py` | 数据层导入器：读上游 CSV → 列名自动归类 → 风格按"低负载/条件可用/禁用"三档筛选 → 色值转 RGB565 并算对比度 → 输出报告；`--sample` 自检已实测 |

### 2.4 导入器的诚实边界

- `--sample`（内置样例）**已实测通过**：正确识别列、把 Glassmorphism/Neumorphism 判为禁用、
  把渐变判为"条件可用（仅 2 段 H/V）"、色值转 RGB565 并给出对比度与"当文字色是否可用"。
- `--in <上游目录>` 这条路径**未用真实 CSV 实测**（网络受限），且**上游列名未核实**，
  因此导入器对识别不到的列一律"如实列出、不猜测填充"。
- 拿到 CSV 后应做的事：先 `--in` 跑一遍看报告 → 人工筛出低负载风格 → 再决定是否写进
  `skills/phywear-lvgl-ui.md`（**不自动改代码**）。

---

## 3. 回退方案

| 层级 | 手段 | 具体 |
|---|---|---|
| 仓库 | **git tag** | `pre-motion-skill-20260916`（本次改动之前的 HEAD） |
| 仓库 | 独立提交 | 动效模块与 Skill 分成两个提交，可单独 `git revert` |
| 代码 | **宏开关** | `PW_MOTION_USE_TABLE=0` → 整模块切回解析式（调用点不变，行为等价） |
| 代码 | 单点回退 | 把 `lv_anim_set_path_cb(&a, pw_motion_path_spring)` 换回 `lv_anim_path_linear`；或删掉 `phywear_imu.c::imu_set_page` 里那三行动效 |
| 数据 | 表可重生成 | 曲线/参数在 `gen_motion_table.py`，改回即重跑脚本；表文件是生成物，不手改 |
| 固件 | 烧回上一版 | 上一版固件 md5 `2e00ecf22243b0bc942c3933f77f6660`（2,204,456 B，蓝牙阶段 A）；烧录走 `flash_with_ftab.sh` |

**回滚触发条件（任一命中即回退，不做"再调调看"）**：
1. 同口径 A/B 下 **fps 下降 >5%**（口径见 §4）；
2. 启动异常、页面卡死、动画末帧跳动或对象停在错误位置；
3. SRAM 越界（>512 KB）或 flash 越界；
4. `pw_motion_selftest` 或 `gen_skill_blob.py --check` 失败。

---

## 4. 验证口径（与上一轮《审查报告》P0-0 一致）

- **fps/loops**：30 s 心跳 `[phywear] alive t=Ns loops/s=… fps=…`（`fps` = `LV_EVENT_RENDER_READY` 计数/秒）。
- **同口径要求**：同页面、同注入状态（bench 开/关）、同是否跑 Agent、同窗口长度。
- 本次动效接入点的 A/B 口径：`phywear lang zh cap imutraj` 后连续三次切页（触发动画）× 30 s 心跳，
  与"删掉动画"版本对比。**该真机 A/B 因板子掉线尚未执行，待补。**

---

## 5. 参考依据（只取思路，不照搬）

- **ui-ux-pro-max-skill**（数据层分类骨架、低负载风格取向）—— 见 §2；其 HTML/Tailwind 产出层不适用本板。
- **B站 b23_tv_Sc54nzn**（PID 调参易抖、UI 动画首选贝塞尔/解析式）—— 本模块据此**不做 PID**，
  改为一组固定解析曲线的查表。
- **GitHub feitangyuan_motion-web**（物理阻尼/弹簧）—— 取"用带物理参数的闭式解"这一点，
  在本板把它固化成 flash 表（Web 端的求解器思路不需要，也不该带到 MCU）。
- **B站 b23_tv_SPZ0czSz / GitHub RealNee_lvgl_demo**（DMA/双缓冲/局部刷新）—— 属 P0 面板流量问题，
  与本文档的动效层无直接关系，留待 P0 动工时按同口径实测。
