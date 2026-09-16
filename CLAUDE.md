# CLAUDE.md —— PhyWear 项目记忆（Claude Code / 小米 MiMo 必读）

> 本文件是本项目**唯一权威记忆**：任何 Claude Code / MiMo 会话**开始前先读它**。
> 细则在 `.claude/skills/*/SKILL.md` 里（本文件只放"必须知道"的部分）。

## 0. 角色分工（2026-09-14 队内决定，勿擅自更改）

| 角色 | 谁 | 职责 |
|---|---|---|
| **主力** | DeepSeek Harness（DSH） | 实现、驱动/BSP、构建、烧录、真机验证、文档、证据、提交 |
| **辅助** | Claude Code + 小米 MiMo（**本机**运行） | ①独立复核 DSH 的改动（审核员）②产出可入 `logs/` 的 AI Coding 日志 ③文档/脚本二次校对 |

- **不再迁移到新电脑**（原"换机器"计划作废）：MiMo 留在**本机**，以辅助身份参与。
- **禁止刷日志**：只为**真实任务**开会话。`logs/` 必须反映真实工作，不许为凑数跑无意义会话——评审会看内容，造假扣分。
- 涉及改代码 / 编译 / 烧录 / 摸板子的活归 DSH；MiMo 以**审核 + 校对 + 取证**为主，除非队长明确指定。
- **提交策略（2026-09-14 队长决定）**：**平时只提交到本地仓库**（`submit_427.sh --execute` 默认就是本地提交、不推送）；
  **等作品全部完结再一次性推送远端**（`--push`），避免提交太多次浪费时间。查看待推送：`submit_427.sh --status`。

## 1. 当前状态（截至 2026-09-15 00:0x，改动后请更新）

| 项 | 值 |
|---|---|
| 参赛仓 / 分支 | `contest2026_427_xinpingqihe` / `dev-ai-contest-2026`（本地仓库即权威） |
| **提交策略** | **平时只提交本地**（`submit_427.sh --execute`）；作品完结再 `--push` 一次性推（见 §0） |
| 本地领先官方 | **11+ 个提交攒在本地**（以 `submit_427.sh --status` 为准，此表不追自增计数） |
| 官方仓分支 | `a83ad3e686d1`（PR #11/#12/#13 已合并） |
| 待合并 PR | **#14**（已推送、`mergeable=true/clean`、7 提交：MiMo 的 JUDGES.md + 协作记忆 + 打包修复 + 规则 S14）；按新策略可留到完结一起合 |
| **动效×风格 整合方案（2026-09-16，待执行）** | 用户目标定为「**帧率不变前提下营造高级感**」；上游 main 分支 zip 已**全量入库**（13 个数据 CSV 含 **`motion.csv` 17 条动效库**、22 个栈、5 个脚本，2.6 MB；核心文件与 2.15.0 镜像逐字节一致）。方案见 `docs/12_动效与风格整合方案.md`：动效触发翻译（hover→press、scroll→页面进入、裁掉滚动/轮播）+ 5 档曲线（power/back/elastic/expo/sine → C1 settle/C2 soft/C3 bounce/C4 snap/D1 pulse）+ 时长规范（取上游再收敛到 320 ms 硬顶）+ 8 处接入分两批 + 风格体系（页面类型→实名风格→色板→字号→密度）+ 三阶段落地与逐项开关。**待用户批准后执行** |
| **UI/UX Skill 规划：本轮补齐（2026-09-16）** | 数据层**已照搬入库**：`third_party/ui-ux-pro-max/`（上游 v2.15.0，jsDelivr 镜像取件，MIT，5 个数据 CSV + 2 参考栈）；导入实测 styles **88 条**（cost:low 62 / moderate 19 / high 7）、colors 192×19；**新增栈层 `stacks/lvgl.csv`**（14 条：11 verified + 3 needs-verify）；Skill 升级为**上游实名 + 逐条翻译**。修正一个真 bug：导入器原按整行扫关键字，把 Dark Mode (OLED)/Bento/Flat 误判为禁用 → 改字段级判定。**UI 实际改版（输出层）仍待批准**，见 `docs/10 §2.5` |
| LVGL 官方跑分（2026-09-16） | **全场景平均 39 FPS / CPU 83% / render 22 ms / flush 0**；轻场景天花板 **56~63 FPS**；重场景 CPU 光栅瓶颈（满屏文字 15 FPS / 58 ms）。入口 `phywear lvbench`（demos 进固件 +278 KB）；证据 `docs/evidence/lvbench-20260916/` |
| **面板帧率上限已定案（2026-09-16）** | CO5300AF-01 手册（V0.00）核实：**VFR = 60 Hz（Typ）**，命令表 7.4 **无任何帧率控制寄存器**（无 C6h）⇒ 60 Hz 是硬上限，实测量 56~63 FPS 与规格吻合；用户目标"60 以上"在此屏上**物理不可达**。TE 支持（35h/34h/44h/**45h 读扫描行**）但板级 pinmux 无 TE 走线。**剩余唯一软件杠杆 = 主机侧 ~4 ms/帧**（20.8 ms vs 16.7 ms）。见 `docs/11` |
| 面板时序方案（2026-09-16，待批） | **只读调研，未动工**。取证发现：`CONFIG_LCD_CO5300_VSYNC_ENABLE=1` 但驱动写的是 `#ifdef LCD_CO5300_VSYNC_ENABLE`（**前缀不匹配 ⇒ VSYNC/TE 是死代码**，实际 `SYNC_DISABLE` + TE 关闭）；初始化表**未写帧率寄存器**；TE 引脚在板级 pinmux 查不到（走线待原理图确认）。方案三档 A 测量/B 帧率寄存器（需手册）/C 开 VSYNC（TE 未证实，黑屏可重烧救），见 `docs/11_面板时序与VSYNC方案_待批.md` |
| **P0 帧率规划：结案（2026-09-16）** | 主机侧只读审查完成：等待链全事件驱动（1/5 s 仅兜底）、行中断 10 行≈0.37 ms、**渲染与推屏已重叠**（`write_fb_cb_done_send` 先启动 LCDC 再 flush_ready）、**忙等 vs 2 ms 的 A/B 完全相同**（48~49 / 17.0 vs 17.1 ms）⇒ **无可挖项**。最终结论：面板 **60 Hz 是物理上限**，本队页面 **48~49 FPS（82%）**，静置态已 **+30.8%**；六个假设全部否决留档 |
| P0 第三轮 | 第五个假设（脏区并集）实测否决：读数聚拢使并集面积 −45%，fps 47~49 / 16.9 ms 与 48~49 / 17.1 ms 实质相同 ⇒ 版式回退。**五个假设全部否决**，指向 16~18 ms 固有帧节拍 |
| P0 首轮（2026-09-16） | ✅ 主循环自适应休眠（`PW_LOOP_SLEEP_MAX_MS`，+30.8%）；❌ 圆角 14→0 **零增益**（保留 14）；**𠆊正**「每帧全屏推屏」误判（`fb_flush_start` 用的是脏区 `fb_clip`）。证据 `docs/evidence/p0-20260916/`，详见 `docs/03 §4.12` |
| 下一步任务（2026-09-16 追加） | 蓝牙阶段 A ✅（HCI 通，host 栈属阶段 B）；③ 轨迹页 ✅（真机静置不漂/推停循环，手推真值待人工）；P1-2 动效查表 + LVGL 版 Skill ✅（`docs/10`，真机计时待补）。原六项：**② ✅ → ① ✅ → ⑤-1 ✅ → ③ 🔶 算法核心 + UI 已交付（主页入口未接）→ ④ ✅ 探针（no-go）→ 视频 🔶 脚本 v4 + 分镜表已交付，成片待实拍**。③ 剩：板块归属（生活/工具）→ 接 tile 一行、真值测量（六面/磁/二维）。**视频成片需人拍**（真机无视频输出、模拟器 0.3 fps、无 ffmpeg） |
| ③ 惯性标尺（**放「工具」板块**，用户 2026-09-15 已定） | 代码核心已交付并三层验证（`docs/evidence/imu-ahrs-20260915/`）：`pw_ahrs`（Mahony MARG，状态 76 B）+ `pw_calib`（六面法/磁椭球/陀螺零偏，全流式充分统计）+ 主机单测 `tools/phywear/hosttest_math.sh` + 真机无头入口 `phywear ahrs/calib`。真机实测：AHRS 与加速度计解算倾角一致 ~0.5°；mag 开后 yaw 稳定不漂。**精度指标仍无转台真值**；磁软铁只能定到一个未知旋转 |
| ⚠️ SRAM 现状（2026-09-16 更新） | 蓝牙阶段 A 打开 `UART_BTH4` 后 **487,460 B（92.98%）**；g_allsyms 的 **−45,952 B** 仍在（否则会是 98%+）：把只读符号表 `g_allsyms` 由 `.data` 改 `const` 放 flash（`nuttx/tools/mkallsyms.py` 两处；实测 **−45,952 B**、flash 不变、panic 符号解析保留）。现比最初基线**低约 43 KB**，蓝牙所需 ~41 KB 也因此有余量 |
| ⑤-1 主动场景 | 已打通并真机验证（`docs/evidence/proactive-20260915/`）：晃表 → Agent 自动跑单摆实验 → 结果进手表 AI 日志。修掉两个真崩溃（message_bus 未初始化时 push 断言、工具 cJSON 双重释放）；补上 `ai_agent` 开机自启（板级 `etc/init.d/rc.sysinit`）。**待用户挥手确认"真实摆动 → 可信 g"**；三道有效性门有残余漏过（70s 内 1 条），根治需改单摆页运动判据 |
| P1 动效首批（2026-09-16） | 曲线分档 C1/C2/C3/C4（生成器 + `pw_motion_spring_q14_t` + 4 个 path 包装）+ 主页 tile 按压反馈（y+2px，C4 150ms，`PW_UI_MOTION` 可控）。固件 2,485,712 B（flash 14.82%）/ SRAM **489,136 B（93.30%，+1,676 B，来源待归因）**；root 页 fps 与改动前同区间（3~7）⇒ 静置无退化。**按压观感需人手指确认**（无法远程按屏） |
| **UI 全面改版首批（2026-09-16，用户批准后执行）** | 全局色板换 `colors-lvgl.csv` 一套（BG `0x101418→0x0F172A`、CARD `0x1A222C→0x1B2336`、CARD_LT `0x26333F→0x272F42`、TEXT 纯白→`0xF8FAFC`、DIM `0x8A97A5→0x94A3B8`、GRID `0x475569`；强调色未动）；动效落地 6 处（主页按压、工具板错峰入场、轨迹页归零 C3 回弹、页指示点脉冲、IMU/标定/向导按钮按压、秒表 Clear/± 按压），新增共享 API `pw_motion_press_feedback/settle_y/pulse_opa`。**A/B（同页同命令 30 s 心跳）**：主页 282 vs 284 loops/s、`cap raw` 208 vs 209 loops/s，**fps 均相同（3 / 12）**；flash 2,486,288 vs 2,485,144 B（动效层 +1,144 B，可关）；**SRAM 489,136 B 两侧相同**。**未做**：§4 Bento 版式重排（圆角保留 14，实测 14→0 零增益）、读数/图表/向导页级动效、逐页截图 8/16（sweep 在实时页被行一致性校验拒绝，工具限制）。按压手感待人工。见 `docs/10 §6` |
| **UI v2 改版 + BUG 修复（2026-09-17，用户反馈驱动）** | ①**修掉真 BUG**：板块列表只剩 1 行 —— `pw_motion_slide_in_y_at()` 内部读 `lv_obj_get_y()` 当动画终点，而 LVGL v9 里 `lv_obj_set_y` 只写样式 Y、`lv_obj_get_y` 读已布局 coords，新建行尚未布局 ⇒ **N 行终点算成同一个 y 而完全重叠**；改为**显式传 base y** + 动画前落到起点 + 无延时版用 `lv_obj_get_y_aligned()`；行高 76→64px（5 行才放得进内容区）。②**交互补强**：按压反馈改成**样式**（`pw_press_style()` 挂在 `pw_card_new()` 上：translate_y+3px + 底色 Muted + 120ms C4）⇒ 全 App 统一；新增 `pw_deco()` 修掉「装饰子件抢走按压状态」；切屏转场（`pw_topbar` 内容区 +16px 落位 C2 260ms）、主页宫格错峰入场、多页屏动画滚动。③**视觉**：HTML 设计稿（`docs/design/phywear-ui-v2-mockup.html`）→ 卡片 1px hairline 描边、图标块圆角方块、原始页章节条；修掉频谱页「x:频率」压按钮。**数字（cap raw，30s 心跳）**：208 → **195 loops/s，fps 全程 12 不变**（关描边 203；读数页 v2 版式再 −2.5%）⇒ **帧率不退化**；flash **2,486,804 B**，SRAM **489,136 B 不变**。另：读数页 v2 版式（大数字 + 右侧单位列 + 行间 hairline + 实时点，单位从数值串挪出）。回退 tag `pre-ui-redesign-20260917`，宏 `PW_UI_MOTION=0`/`PW_CARD_LINE=0`。**未做**：逐页版式铺开、数值脉冲 D1、按压手感待人工。见 `docs/evidence/ui-v2-20260917/README.md` |
| 真机固件 | 板上 = **UI v2 构建（含读数页 v2 版式）**（**2,486,804 B** / flash 14.82% / SRAM **489,136 B**；本地产物 md5 `c62545edae6e301070dd3e389fa50503` —— ⚠️ 固件**非可重现**，md5 只在「板上 vs 同一产物」时有意义）。含蓝牙阶段 A、P1-2 动效查表（151 vs 2088 ns/次）、P0 主循环自适应休眠、UI v2（色板 + 统一按压态 + 切屏转场 + hairline 描边 + 读数页大数字/单位列/实时点）。⚠️ `flash_with_ftab.sh` 读回校验会把 `sftool` 读取**超时**误报成「固件 ❌ 不一致」（stderr 被 `>/dev/null` 吞掉），本轮复现多次，实际写入正常（板子正常启动跑 GUI，截图已核）。更早回退点 `~/桌面/PhyWear-rollback-20260915-0002/` |
| 板子 | 立创·黄山派 SF32LB52-MOD-1-N16R8；`/dev/ttyUSB0` @1000000 8N1 —— 09-15 22:1x 短暂掉线后恢复；09-16 21:08 重新插拔后恢复，动效真机计时与截图验证已补齐（`docs/evidence/motion-20260916/`） |
| AI 日志 | `logs/XPQHyue/` **50 会话 / 15,283 事件**，`validate-log.py` ✅ ALL OK |
| 回退点 | `~/桌面/PhyWear-rollback-20260915-0002/`（含改动前固件 `484b64ca…`，本批真机 A/B 用的就是它；`rollback.sh --check`） |
| 迁移包 | `~/桌面/PhyWear-migrate-20260914.zip` |

## 2. 铁律（违反 = 停止并报告，不许绕过）

1. **提交只走脚本**：`bash .claude/skills/phywear-submit/submit_427.sh`（默认演练；`--execute` = **只提交本地**；`--push` = 额外推远端，**仅在里程碑/作品完结时**）。不许手敲 `git push`、不许 force-push 官方仓。细则见 `phywear-submit/SKILL.md` 的 S1–S15。
2. **串口铁律**：`pyserial` 打开后立刻 `dtr=False; rts=False`；`picocom` 必须 `--noreset --lower-rts --lower-dtr`；`/dev/ttyUSB0` **独占**；**禁止 `erase_flash`**；**一个 boot 只跑一个 `phywear` GUI**。
3. **密钥不入库**：MiMo Key 只在 `~/.config/phywear/mimo.key`（600）；`tp-`/`sk-`/`ghp_` 出现在提交里 = 直接中止。
4. **构建产物不入库**：`cmake_out/`、`nuttx.bin`、`*.o/*.a`、`.zip`；唯一允许的二进制是 `board/ftab_openvela.bin`。
5. **只许 rebase**：不许产生 merge commit；提交作者固定 `XPQHyue <15770782523@163.com>`。
6. **改完必须回仓**：工作区（`~/openvela`）改动要 `sync_back.py --execute` + 重生成 `manifest.json`，否则评审 clone 看不到。
7. **数据可追溯**：每个数字都要指到代码/日志/证据；**模拟器数据 ≠ 真机测量**；未实现不得写成已实现（写进「已知限制」不扣分）。
8. **归属如实**：EPIC 硬件加速来自官方 PR #31/#41/#121（非本队原创）；phyphox 仅灵感来源（见 `app/phywear/NOTICE.md`）；主动场景**默认开启**（`PW_WATCH_PROACTIVE 1`，推送走受保护的 `pw_ai_ask()`，Agent 不在时不 panic）；`ai_agent` 已开机自启；真机无网络栈。

9. **PR 合并后必须先把本地 rebase 到官方最新再继续**：rebase-merge 会改写 SHA，本地若还带着旧 SHA 的提交，
   新 PR 会显示成"重复提交 + 冲突"（`mergeable=false / dirty`）。正确顺序：
   `git fetch origin` → `git rebase --onto origin/dev-ai-contest-2026 <上次已合并的最后一个提交>` → 再提交/推送。
   （2026-09-14 实际踩过：PR #14 一开始显示 7 提交 20 文件且冲突。）

## 3. 每次会话结束必做（独立 10 分维度）

```bash
bash .claude/skills/phywear-migrate/finish_session.sh
```
把本次 Claude Code 会话导出到 `logs/XPQHyue/<日期>/` 并校验。**白名单工具只有 claude-code / codex / opencode / kiro**；DSH 不计，只能作补充证据。

## 4. 常用命令

```bash
python3 .claude/skills/phywear-migrate/check_env.py          # 本机环境检测
python3 .claude/skills/phywear-reproduce/check_progress.py   # 进度/一致性 P0–P7（P2 = 快照↔工作区）
python3 .claude/skills/phywear-reproduce/sync_back.py        # 工作区 → 参赛仓 快照回写（先演练）
bash    .claude/skills/phywear-submit/submit_427.sh          # 提交（唯一入口：演练 / --execute 只本地 / --push 推远端）
bash    .claude/skills/phywear-submit/submit_427.sh --status # 看本地攒了多少个待推送提交
bash    .claude/skills/phywear-migrate/make_rollback.sh      # 打新回退点
bash    .claude/skills/phywear-migrate/rollback.sh --check    # 与回退点比对
```

## 5. 审核 DSH 改动时看什么

按 **审核员清单** 执行（条目见 `docs/07 §7.2` 与本文档第 2 节）：红线扫描（密钥/产物/静默忽略/merge commit/邮箱）→
一致性（P2、Skill blob、manifest）→ 真实性（数字对代码/日志）→ 功能回归（真机自检清单 `docs/07 §5`）→
文档口径 → 流程合规（`submit_427.sh`、远端 SHA、日志导出）→ 回退保障。
**证据不足时结论只能是 ⚠️ 或 ❌，不许给 ✅。**
