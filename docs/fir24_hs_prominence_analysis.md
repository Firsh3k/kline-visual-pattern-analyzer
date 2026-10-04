# FIR-24 H&S Guardrails — 头肩突出度与颈线外推分析

> 分支：`v5/fir-24-hs-guardrails`
> 触发：FIR-12 质量审计发现 002415 海康威视出现弱/假 `hs_bottom` 匹配。
> 复现脚本：`scripts/fir24_hs_prominence_analysis.py`

## 1. 问题

002415（2024-02-07 ~ 2026-09-30，640 bars，qfq）上一个弱 H&S 底候选：

| 结构点 | index | 价格 |
|---|---|---|
| 左肩 ls | 555 (2026-06-02) | 29.45 |
| 头 head | 562 (2026-06-11) | 29.21 |
| 右肩 rs | 587 (2026-07-17) | 32.27 |
| 左颈线点 | 558 | 30.98 |
| 右颈线点 | 575 | 35.61 |

- **头只比左肩低 0.82%**：(29.45−29.21)/29.45 ≈ 0.82%。头部几乎不突出，实质是 W/双底结构而非 H&S。
- **倾斜颈线外推失控**：slope = (35.61−30.98)/(575−558) = 0.2724；外推到末根 bar（index 639）颈线值 = 30.98 + 0.2724×81 ≈ **53.04**，而当前收盘仅 32.46。颈线已漂移到结构之外的无意义水平。

## 2. 方法

`scripts/fir24_hs_prominence_analysis.py` 在全部缓存 qfq 数据
（`data/calibration/*_qfq.json`，约 74 只股票）上，用 canonical
`HSBottomDetector` / `HSTopDetector` 运行。**统计时临时中和本 guardrail 阈值**
（`MIN_HS_HEAD_PROMINENCE=0`、`MAX_NECKLINE_EXTRAPOLATION_RATIO=1e9`），
以收集「通过既有结构闸门、但尚未加新 guardrail」的全集候选，避免循环统计。

对每个候选计算：

```
head_prominence = |head_price - avg_shoulder_price| / avg_shoulder_price
avg_shoulder_price = (ls.price + rs.price) / 2
```

共收集 **n=738** 个 `nl_now>0` 的 H&S 候选（hs_bottom n=355，hs_top n=383）。

## 3. head_prominence 分布

| 分位 | all_hs (n=738) | hs_bottom (n=355) | hs_top (n=383) |
|---|---|---|---|
| min | 0.33% | 0.35% | 0.33% |
| P10 | 1.47% | 1.37% | 1.55% |
| P25 | **2.71%** | 2.63% | 2.85% |
| P50 (中位数) | **5.29%** | 4.90% | 5.77% |
| mean | 6.94% | 5.83% | 7.97% |
| P75 | 8.71% | 7.95% | 10.31% |
| P90 | 15.01% | 11.09% | 18.71% |
| max | 47.47% | 23.72% | 47.47% |
| std | 6.03% | 4.28% | 7.14% |

## 4. 阈值选择（guardrail 1a）

按任务规则：`max(P25, 中位数/2, 下限 3%)`

- P25 = 2.71%
- 中位数/2 = 5.29%/2 = 2.65%
- 下限 = 3.00%

**→ `MIN_HS_HEAD_PROMINENCE = 0.03`（3%）**

该阈值拒绝「平头」候选。例：002415 上另一候选（head=28.26, ls=28.65, rs=29.45）
prominence = 2.72% < 3%，被拒——它本质是双底而非 H&S。

## 5. 颈线外推比率分布（guardrail 1b）

定义（统一为「颈线相对头部的偏离倍数」）：

- 头肩底：`ratio = nl_now / head_price`（颈线应在头部上方）
- 头肩顶：`ratio = head_price / nl_now`（颈线应在头部下方）

| 分位 | 比率 |
|---|---|
| P10 | 0.38 |
| P25 | 0.69 |
| P50 | **1.12** |
| P75 | 1.93 |
| P90 | 3.37 |
| P95 | 5.74 |
| P99 | 12.55 |
| max | 69.9 |

- 标准健康 fixture 的外推比率 ≈ **1.19**（颈线仅比头部高 ~19%）。
- 中位数 1.12 说明健康 H&S 的颈线通常仅比头部高/低 ~12%。
- P90=3.37、max=69.9 的长尾全是倾斜颈线斜率过陡、外推失控的退化结构。

**→ `MAX_NECKLINE_EXTRAPOLATION_RATIO = 1.5`**

取 1.5 的依据：健康结构集中在 ~1.1–1.3；超过 1.5 即外推颈线已比头部偏离 50% 以上，
不再是结构附近的有效支撑/阻力。水平颈线（slope==0）外推为常数，不受此限制。

## 6. 002415 弱案例如何被拒绝

| 候选 | prominence | nl_now/head | 拒绝原因 |
|---|---|---|---|
| head=22.31 … nl=240.52 | 8.79% | 10.78x | 1b 外推失控 |
| head=25.11 … nl=43.65 | 9.48% | 1.74x | 1b 外推失控 |
| head=28.26 … nl=52.04 | 2.72% | 1.84x | 1a 平头 + 1b 外推 |
| **head=29.21 … nl=53.04（目标弱候选）** | **5.35%** | **1.82x** | **1b 外推失控（1.82 > 1.5）** |

目标弱候选 prominence=5.35%（接近中位数，无法用 1a 拒绝，否则会砍掉一半样本），
但其倾斜颈线外推比率 1.82 > 1.5，由 **guardrail 1b** 拒绝。加 guardrail 后，
002415 的 `hs_bottom` 候选数由 5 → 0。

## 7. 影响

- W Bottom / M Top：不经过任何 H&S guardrail，行为完全不变。
- 标准 H&S fixture（`tests/v5/test_pivot_patterns.py`）：外推比率 1.19、
  prominence ≫ 3%，全部仍绿色。
- 只拒绝「平头」与「颈线外推失控」的弱/假 H&S，不引入 look-ahead，
  不引入 raw-extrema fallback。
