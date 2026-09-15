# ③ 惯性标尺 UI（水平仪 + 标定向导）证据（2026-09-15）

> 承接 `docs/evidence/imu-ahrs-20260915/`（算法核心）。本目录是**界面层**：4 页横滑屏
> 「水平仪」——实时数值 / 零偏标定 / 重力标定 / 磁标定。
> **板块归属（生活/工具）仍待用户拍板**，所以本批只提供无头入口与 AI 切页，
> 主页 tile 入口**故意没接**（落位时改一行即可）。

---

## 1. 四个页面

| 页 | 内容 | 用的标定库 | 真值来源 |
|---|---|---|---|
| 0 实时数值 | 水平仪（卡片 + 十字线 + 小球，**小球靠改坐标而不是旋转对象**）+ roll/pitch/yaw + 零偏估计读数 + 磁有无 | `pw_ahrs` | — |
| 1 零偏标定 | 「开始采样 / 停止采样」，实时显示样本数；停止即求解并给出零偏与离散 | `pw_calib_bias_*` | 静止时角速度为零 |
| 2 重力标定 | 六个面各取 1 s 样本（`+X/-X/+Y/-Y/+Z/-Z 朝下`），第 6 面取完自动求解 | `pw_calib_six_*` | 重力（理想读数 ±1 g） |
| 3 磁标定 | 全方向转动采样，停止即拟合椭球，给硬铁中心/平均场强/形状离散 | `pw_calib_mag_*` | 场强恒定（椭球） |

**硬件友好点**：全程不用旋转控件（EPIC 不加速 rotate、会退回软件光栅）；
不新增采样缓冲——AHRS 76 B、零偏累加 28 B、六面法 ~100 B、磁椭球 360 B，全是流式充分统计。

## 2. 真机实测

| 项 | 结果 |
|---|---|
| 固件 | md5 `f2a2fab356f4701a6a0b39043c705ffd`，**2,082,992 B**（flash 12.42%），读回比对通过 |
| **链接期 SRAM** | **492,512 B / 512 KB（93.94%）**；本批 **+936 B**（491,576 → 492,512） |
| **运行时堆** | `free` 实测：root 页 **333,696 B** → 水平仪页 **336,280 B**，即 **+2,584 B 堆**（LVGL 控件） |
| 帧率（实时页） | **fps 26 / loops 26**（两次一致；该页 6 个标签 + 小球按 50 Hz 刷新，比原始页"忙"，但只在打开时消耗） |
| 磁力计 | 实时页显示 **「磁: 有」**（真机有 MMC5603；模拟器显示"无"并自动退化 6 轴） |
| 四页截图 | `real-imu-live/bias/grav/mag.png` |

## 3. 交互验证（模拟器触摸注入 + 真机手指）

模拟器有 UInput 触摸（`phywear tap`），所以按钮路径是**真按过**的：

- **零偏标定**：点「开始采样」→ 按钮变「停止采样」、样本数从 0 涨到 172（截图 `sim-bias-tap-test.png` 左）；
  再点 → 回到「开始采样」，状态显示 **「未标定」** —— 因为模拟器一直在合成摆动，
  **"够不够静"判据如实拒绝，不给数字** ✓（右图）。
- **重力标定**：连点「下一面」，面指示从 `+X 朝下 面:1/6` 变化、六面取满后自动求解；
  模拟器六个面的读数几乎相同 → 对比度判据不过 → 同样如实回「未标定」。
- ⚠️ **注入工具偶尔丢拍**（连点 5 次只中 1 次）：已把按下时长从 150 ms 放宽到 300 ms。
  这是**测试工具**的时序问题，真机手指点击不受影响（零偏页两次点击都在真机/模拟器上生效）。

## 4. 无头入口（板块未定也能用）

```text
phywear cap imu        # 水平仪，第 0 页
phywear cap imubias    # 第 1 页 零偏标定
phywear cap imu6       # 第 2 页 重力标定
phywear cap imumag     # 第 3 页 磁标定
```
AI 侧同样可以 `phywear_open_screen{"screen":"imu"}` 打开（已在 cap 表里）。

## 5. 复现

```bash
# 真机截图（四页）
python3 tools/phywear/pwshot.py shot imu     --out /tmp/shots_imu --label imu     --retries 3
python3 tools/phywear/pwshot.py shot imubias --out /tmp/shots_imu --label imubias --retries 4
python3 tools/phywear/pwshot.py shot imu6    --out /tmp/shots_imu --label imu6    --retries 3
python3 tools/phywear/pwshot.py shot imumag  --out /tmp/shots_imu --label imumag  --retries 3

# 堆对比（两次 boot：root vs imu）
python3 docs/evidence/imu-ui-20260915/repro-heap.py root
python3 docs/evidence/imu-ui-20260915/repro-heap.py imu

# 模拟器按按钮
phywear cap imubias &
phywear tap 105 164        # 点「开始采样」，3s 后再点一次看是否如实拒绝
```

## 6. 已知限制（如实）

1. **精度指标仍无真值**：本 UI 只把算法核心的读数可视化；`≤0.5°` 这类精度说法仍需转台，
   目前只有"AHRS 与加速度计独立解算一致 ~0.5°"这一间接证据（见 `imu-ahrs-20260915`）。
2. **六面法/磁标定的成功路径没在真机走通**：需要人把表按六个面摆好、并全方向转动；
   模拟器里六个面读数相同，只能验证"如实拒绝"这一支。**请用手实测一次**。
3. **SRAM 已超用户额度**：累计 +3,072 B（489,440 → 492,512），超出"不超过 ~2 KB"约 **1 KB**。
   已实测一条 **−45,752 B** 的腾挪方案（关 `CONFIG_ALLSYMS`，代价是 panic 回溯只打地址，
   见 `docs/03 §5.1`），**待用户拍板**。
4. **主页 tile 入口未接**：板块归属未定；接一行即可（`docs/03 §4.9` 有说明）。
