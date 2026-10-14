# 形态计算管线（V5 core）

后台算法核心统一为 Python `core/`（V5）。前端只读取这里输出的 JSON 并渲染，不做任何形态判断。

**单一实现（FIR-38）**：本管线、Python 分析 API、自选股扫描、市场扫描都调用同一个 `core.analysis.analyze()`
（校验 → asOf 截断 → 250 根窗口 → 8 种形态 + 指标），同一输入得到逐字节相同的结果与 `data_hash`。

## 数据合同（schema 3.0）

| 字段 | 含义 |
|---|---|
| `input.data_hash` | 窗口内规范化 K 线的 sha256 |
| `input.adjustment` / `input.provenance` | 复权方式（qfq / none；离线 CSV 未声明时为 unknown）与来源 |
| `session_state` | `close_confirmed` 已收盘确认 / `intraday` 盘中（最后一根未收盘）/ `unknown` 日历未核实 |
| `calendar` | `status` 仅在权威交易日历覆盖时为 `verified`；`expected_trade_date`、`stale`、`calendar_id` |
| `core.result_sha256` | 计算结果的 sha256 |

输入校验（任何一处不合格整只股票拒绝，不修补、不去重）：日期格式与存在性、严格递增（重复 / 乱序拒绝）、
OHLC 为有限正数且满足高低不变量、成交量为有限非负数、最后一根不晚于当前日期。

失败后旧结果：若本次失败而输出目录中有上次成功的同版本结果，文件保留，`index.json` 中该股
`status = "stale_previous"`、`stale = true` 并附错误；否则删除旧文件，只记入 `errors`。

## 计算内容

| 类别 | 内容 | 实现 |
|---|---|---|
| 摆动点结构形态 | W底、M头、头肩底、头肩顶 | `core/pivot_patterns.py` |
| 均线 / 平台形态 | 均线多头、均线空头、平台突破、平台破位 | `core/ma_platform_patterns.py`（由 V4 迁移） |
| 技术指标 | MA5/10/20/60、MACD、RSI14、KDJ、BOLL、ATR、量价状态、2×ATR 止损位 | `core/indicators.py`（由 V4 迁移） |
| 大盘环境 | 上证指数评分、阶段、周/月线趋势 | `core/market_regime.py`（由 V4 迁移） |

V4 的综合“推荐度”评分不再计算（PO 决策 D4）。

每个形态结果额外带两个字段（PO 决策 D1 / D3）：

- `category`：`price_pattern`（价格形态：W底 / M头 / 头肩 / 平台）或 `trend_state`（趋势状态：均线多头 / 空头），UI 与 API 据此分开标注。
- `evidence`：机器可读的判定证据，例如平台形态的 `broke` / `broke_today` / `volume_confirmed`。平台“当日首次越过突破线”记为 `breakout_day`。
- W底 / M头 / 头肩（FIR-36）：`breakout_date` / `breakout_close` / `bars_since_breakout`、`back_inside_neckline` + `days_back_inside`（突破后收盘回到颈线内的连续天数；满 3 天阶段才变为 `failed_break`）、`close_beyond_breakout_line`、`volume_confirmed`。`breakout_price` = 突破日收盘价（未突破为空），不再随当前价变化。
- 颈线突破日（FIR-39）：当日收盘越过判定线（颈线 × mult）且前一日未越过；头肩倍数与 W/M 一致（1.01 / 0.99），右肩极值点须在颈线内侧。

## V4 迁移说明

- 数值与 V4 完全一致：`tests/v5/test_v4_migration_parity.py` 以冻结的 V4 副本（`tests/v5/oracle/`）为对照，
  对每个数据集的每个时间点逐项比对指标、大盘环境和形态评分/阶段（浮点完全相等）。
- 唯一有意的行为变化：均线形态在“未形成排列”、平台形态在“振幅过大（非平台）”时不再返回结果
  （V4 此时仍给 20~30 分）。与 V5 识别器“结构不存在就不报告”的约定一致。
- 新增信息：每个关键点带 K 线日期；平台形态给出平台区间起止日期。
- 平台“突破后回踩 / 破位后回抽”在 V4 判定顺序下永远不可达；V5 已按 FIR-35（PO 决定）调整顺序使其可达：
  此前 9 个交易日内曾收盘越过上沿 2%（下沿 2%），今日回到上沿（下沿）±3% 区间，且今日不是首次越过日 → `retest`。
  阈值与分数公式不变；`evidence.retest` 为新判定，`evidence.v4_stage` 保留 V4 原顺序的结论以便追溯。

## 输出

| 文件 | 内容 |
|---|---|
| `index.json` | 运行信息、大盘环境、每只股票摘要（每种形态最近一次识别结果） |
| `stocks/<代码>.json` | 分析窗口内的 K 线原样 + 摆动点 + 8 种形态全部结果 + 指标（含口径说明与对照值） |

指标口径（FIR-37，PO 决定保留现有口径并注明）：`indicators.conventions` 给出每个指标的计算口径；
`indicators.reference` 给出行情软件常用口径的对照值（Wilder RSI14、Wilder ATR14、样本标准差 BOLL20），
仅供展示对照，不参与任何形态、信号或评分。

## 运行

```bash
# 本地 CSV（列：date,open,high,low,close,volume）
python3 pipeline/run_daily.py --csv-dir <目录> --index-csv <上证CSV> \
    --provenance-json <来源说明.json> --out site/data [--expect-date 2026-10-13] [--now 2026-10-13T16:00:00+08:00]
# --calendar 已移除：日历状态由权威交易日历推导，不再默认 verified
# 测试
python3 -m pytest tests/pipeline tests/v5 -k "not Integration"
```
