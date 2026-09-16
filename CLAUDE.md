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
| 下一步任务（2026-09-16 追加） | 蓝牙阶段 A ✅（HCI 通，host 栈属阶段 B）；③ 轨迹页 ✅（真机静置不漂/推停循环，手推真值待人工）；P1-2 动效查表 + LVGL 版 Skill ✅（`docs/10`，真机计时待补）。原六项：**② ✅ → ① ✅ → ⑤-1 ✅ → ③ 🔶 算法核心 + UI 已交付（主页入口未接）→ ④ ✅ 探针（no-go）→ 视频 🔶 脚本 v4 + 分镜表已交付，成片待实拍**。③ 剩：板块归属（生活/工具）→ 接 tile 一行、真值测量（六面/磁/二维）。**视频成片需人拍**（真机无视频输出、模拟器 0.3 fps、无 ffmpeg） |
| ③ 惯性标尺（**放「工具」板块**，用户 2026-09-15 已定） | 代码核心已交付并三层验证（`docs/evidence/imu-ahrs-20260915/`）：`pw_ahrs`（Mahony MARG，状态 76 B）+ `pw_calib`（六面法/磁椭球/陀螺零偏，全流式充分统计）+ 主机单测 `tools/phywear/hosttest_math.sh` + 真机无头入口 `phywear ahrs/calib`。真机实测：AHRS 与加速度计解算倾角一致 ~0.5°；mag 开后 yaw 稳定不漂。**精度指标仍无转台真值**；磁软铁只能定到一个未知旋转 |
| ⚠️ SRAM 现状（2026-09-16 更新） | 蓝牙阶段 A 打开 `UART_BTH4` 后 **487,460 B（92.98%）**；g_allsyms 的 **−45,952 B** 仍在（否则会是 98%+）：把只读符号表 `g_allsyms` 由 `.data` 改 `const` 放 flash（`nuttx/tools/mkallsyms.py` 两处；实测 **−45,952 B**、flash 不变、panic 符号解析保留）。现比最初基线**低约 43 KB**，蓝牙所需 ~41 KB 也因此有余量 |
| ⑤-1 主动场景 | 已打通并真机验证（`docs/evidence/proactive-20260915/`）：晃表 → Agent 自动跑单摆实验 → 结果进手表 AI 日志。修掉两个真崩溃（message_bus 未初始化时 push 断言、工具 cJSON 双重释放）；补上 `ai_agent` 开机自启（板级 `etc/init.d/rc.sysinit`）。**待用户挥手确认"真实摆动 → 可信 g"**；三道有效性门有残余漏过（70s 内 1 条），根治需改单摆页运动判据 |
| 真机固件 | 板上 `0c9f8e90574cad28fba6f33a7b980f06`（**2,206,056 B** / SRAM **487,460 B（92.98%）** / flash 13.15%），读回比对通过；含蓝牙阶段 A（`/dev/ttyHCI0` + HCI 探针）与 P1-2 动效查表（真机 `motionbench` 151 vs 2088 ns/次）；含 allsyms→flash、mag/light oneshot、+2 条离线意图；⚠️ 固件**非可重现构建**（镜像嵌构建时间），md5 只在「板上 vs 同一产物」时有意义；上一版 `484b64ca2e9cd157a191f8ea7d022f9a` 留档在 `~/桌面/PhyWear-rollback-20260915-0002/firmware/` |
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
