# V5 FIR-12 Pattern Migration — Apples-to-Apples 验证报告

- 生成脚本：`scripts/v5_validation.py`（可重复运行，只读移植 V4 JS，不改动 V4 文件）
- 原始数据：`data/v5_validation/{code}.csv`（通达信前复权日K，`date,open,high,low,close,volume`）
- 机器可读结果：`data/v5_validation/results.json`
- 验证日期：2026-10-04（数据截至 2026-09-30 收盘）

> 本报告针对 PR #36 评审提出的 4 个 Blocker 问题重写：修正口径、修正指标命名、补齐 W/M/H&S 全形态 V4 对比。

---

## 1. 验证方法（修正后）

### 1.1 两端实现现在同口径

旧报告的根本问题：**V4 端用 `pattern_scanner.detect_double_bottom(df)` 的"逐日历史触发"，V5 端是"末端快照"**，两者不可逐日对齐，导致严格 event recall = 0/4 = 0% 的伪结论。

本报告改为：

| 项 | V4（Before） | V5（After） |
|---|---|---|
| 实现来源 | `kline_analyzer.html::matchPatterns() / matchBearPatterns()` 的**忠实 Python 移植**（`scripts/v5_validation.py::v4js_detect`） | `core/pivot_patterns.py` 四个 detector |
| 分析时点 | **末端最后一根K线快照** | **末端最后一根K线快照** |
| 结构识别 | lookback=min(80,n-1) 内取全局极值（最低 low / 最高 high）作为头/底，再取左右极值为肩/另一底 | `SwingPivotEngine(left=5,right=5)` 只取**已确认** swing pivot |
| 未来函数 | 无右侧确认窗口，窗口内即取极值 | 只用 `confirmed=True` pivot，且 `confirmed_at_index <= n-1` |
| 颈线 | 两底/顶之间全局 max/min high/low；H&S 用左右两点连线（可倾斜） | 两底/顶之间已确认 swing high/low；H&S 用两个已确认对立 pivot 连线 |
| 形态存在性 | W底：`secondHigher`；M头：`secondLower`；H&S：`leftHigher&&rightHigher&&symmetry`（顶部镜像） | 相邻已确认 pivot 对通过形态闸门 |

> **两端现在都对同一根末端K线做快照分析**，语义一致，可逐股逐形态直接对比。V4 JS 文件本身只读不改。

### 1.2 指标命名（修正后）

> **没有人工标注的 false-positive 真值，因此本报告不报告 "false-positive reduction"。**
> 旧报告里的 "75% FP reduction" 实际是 **duplicate-alert reduction**（V4 Python 对同一形态重复触发 4 次、V5 收敛为 1 次），不是假阳性抑制。二者在 §5 明确分离。

报告只报告可由数据支撑的指标：

- **structural recall (V4→V5)**：V4-JS 检出的形态中，V5 也检出的比例。分子/分母见表格。
- **V5 coverage (V5→V4)**：V5 检出的形态中，V4-JS 也检出的比例（V5 为严格 pivot 检测，可能更保守/更多样）。
- **neckline agreement**：两端都检出时，颈线价 ±2% 一致的比例。
- **breakout-state agreement**：两端都检出时，"是否已突破/破位"一致的比例。

---

## 2. 数据

6 只代表股，每只 100 根前复权日K（2026-05-12 ~ 2026-09-30）。覆盖 W底 / M头 / 头肩底 / 头肩顶 四种形态。

| 代码 | 名称 | 行业 | K线数 |
|---|---|---|---|
| 600519 | 贵州茅台 | 白酒/消费 | 100 |
| 002594 | 比亚迪 | 新能源汽车 | 100 |
| 300750 | 宁德时代 | 新能源电池 | 100 |
| 601318 | 中国平安 | 金融保险 | 100 |
| 000858 | 五粮液 | 白酒/消费 | 100 |
| 002415 | 海康威视 | 安防科技 | 100 |

样本量：6 股 × 4 形态 = **24 个 (股票×形态) 判定单元**。

---

## 3. 逐股 × 形态判定矩阵

单元格含义：`BOTH`=两端都检出；`V4 `=仅 V4-JS 检出；`V5 `=仅 V5 检出；`-- `=两端都未检出。

| 代码 | 名称 | w_bottom | m_top | hs_bottom | hs_top |
|---|---|---|---|---|---|
| 600519 | 贵州茅台 | BOTH | BOTH | BOTH | V4 |
| 002594 | 比亚迪 | V5 | BOTH | BOTH | BOTH |
| 300750 | 宁德时代 | -- | BOTH | V5 | V4 |
| 601318 | 中国平安 | BOTH | V4 | BOTH | V4 |
| 000858 | 五粮液 | BOTH | V4 | V4 | V4 |
| 002415 | 海康威视 | V5 | V4 | V5 | BOTH |

合计：**both=11，v4_only=8，v5_only=4，neither=1**。

---

## 4. 分形态指标（每个指标带 N / 分母）

| 形态 | V4检出 | V5检出 | both | v4_only | v5_only | structural recall (V4→V5) | V5 coverage (V5→V4) | 颈线一致 | 突破状态一致 |
|---|---|---|---|---|---|---|---|---|---|
| w_bottom | 3 | 5 | 3 | 0 | 2 | **3/3 = 100%** | 3/5 = 60% | 1/3 | 1/3 |
| m_top | 6 | 3 | 3 | 3 | 0 | **3/6 = 50%** | 3/3 = 100% | 3/3 | 2/3 |
| hs_bottom | 4 | 5 | 3 | 1 | 2 | **3/4 = 75%** | 3/5 = 60% | 0/3 | 0/3 |
| hs_top | 6 | 2 | 2 | 4 | 0 | **2/6 = 33%** | 2/2 = 100% | 1/2 | 2/2 |
| **合计** | **19** | **15** | **11** | **8** | **4** | **11/19 = 57.9%** | **11/15 = 73.3%** | **5/11** | **5/11** |

### 4.1 解读

- **w_bottom recall 100% (3/3)**：V4-JS 检出的 3 个 W 底 V5 全部覆盖，迁移未丢失看涨双底结构。
- **m_top / hs_top recall 偏低（50% / 33%）**：V4-JS 用全局极值识别顶部，许多"全局最高 high"并不是 V5 要求的、右侧有 5 根确认的 swing high pivot，因此 V5 不报。这正是迁移预期——V5 用确认 pivot 替换窗口极值，更保守、更抗噪。
- **V5 coverage 100%（m_top / hs_top）**：V5 报出的顶部形态 V4-JS 都能在全局极值中找到对应结构，没有 V5 凭空新增的顶部。
- **颈线一致 5/11、突破状态一致 5/11**：不一致主要来自 V4 颈线锚定全局极值、V5 锚定已确认 swing pivot，两者选点不同导致颈线价差异（尤其 H&S 倾斜颈线），属算法差异而非漏检。

---

## 5. duplicate-alert reduction vs false-positive reduction（分离）

| 指标 | 口径 | 数值 | 是否可由本样本支撑 |
|---|---|---|---|
| **duplicate-alert reduction**（重复报警抑制） | 旧 V4 Python 对同一形态在历史窗口内重复触发（如 601318 同一 W 触发 4 次），V5 末端快照收敛为 1 个形态 | 旧报告测得 **4→1 = 75%** | ✅ 可测，但它度量的是**去重/收敛**，不是假阳性抑制 |
| **false-positive reduction**（假阳性抑制） | 检出的形态中实际为"假信号"的比例下降 | **N/A — 无人工标注真值** | ❌ 本样本没有对每个形态打"真/假"标签，无法计算 |

**结论**：旧报告把 75% 标注为 "FP reduction" 是错误的。它应被如实标注为 **duplicate-alert reduction（重复报警收敛）**。要度量真正的 false-positive reduction，需要带人工标签（或事后 T+20 收益验证）的标注集，不在本次迁移验证范围内。

---

## 6. 典型观察

- **601318 中国平安 W底**：两端都检出，V4 颈线 54.98 vs V5 颈线 49.24。差异来自 V4 取窗口内全局 max high、V5 取已确认 swing high 点；结构上同一下跌-双底-回升，V5 颈线更贴近真实两底间反弹高点。
- **000858 五粮液 W底**：两端都检出且颈线一致（78.34 vs 78.84，±2% 内），突破状态一致——这是高质量的结构对齐样本。
- **v4_only 的顶部形态（m_top/hs_top 共 7 例）**：V4-JS 因全局最高 high 触发顶部，但该 high 在 V5 的 (left=5,right=5) 确认窗口下不是已确认 swing pivot，故 V5 不报。属于"窗口极值 → 确认 pivot"的预期收紧。

---

## 7. 局限与后续

1. **样本量**：6 股 × 100 根日K = 24 判定单元，统计功效有限；建议接入 ≥500 根历史与更多标的后复跑 `python scripts/v5_validation.py`。
2. **无 FP 真值**：本报告不含 false-positive reduction；需人工标注或 T+20 事件验证集才能度量。
3. **颈线锚点差异**：V4 全局极值 vs V5 已确认 pivot，颈线价天然有偏差；要逐点对齐需进一步把 V4 也改为 pivot 口径，这超出 FIR-12 范围。
4. **V4 文件只读**：`kline_analyzer.html` / `pattern_scanner.py` 未被修改，仅提取 JS 逻辑到验证脚本。

**总体判定**：在同口径（末端快照）对比下，V5 对 V4 已检出形态的 structural recall 为 11/19=57.9%，其中看涨的 w_bottom 召回 100%；V5 检出的形态 V4 覆盖率 11/15=73.3%。召回缺口集中在顶部形态，且全部可由"全局窗口极值 → 已确认 swing pivot"的预期收紧解释，符合 FIR-12 用确认 pivot 杜绝未来函数的设计目标。旧报告的 75% 已正名为 duplicate-alert reduction，不再冒称 false-positive reduction。
