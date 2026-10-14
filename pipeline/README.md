# 形态计算管线（V5 core）

后台算法核心统一为 Python `core/`（V5）。前端只读取这里输出的 JSON 并渲染，不做任何形态判断。

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
- `evidence`：机器可读的判定证据，例如平台形态的 `broke` / `broke_today` / `volume_confirmed`。平台“当日首次越过突破线”记为 `breakout_day`。W/M/头肩暂为空，见 FIR-36。

## V4 迁移说明

- 数值与 V4 完全一致：`tests/v5/test_v4_migration_parity.py` 以冻结的 V4 副本（`tests/v5/oracle/`）为对照，
  对每个数据集的每个时间点逐项比对指标、大盘环境和形态评分/阶段（浮点完全相等）。
- 唯一有意的行为变化：均线形态在“未形成排列”、平台形态在“振幅过大（非平台）”时不再返回结果
  （V4 此时仍给 20~30 分）。与 V5 识别器“结构不存在就不报告”的约定一致。
- 新增信息：每个关键点带 K 线日期；平台形态给出平台区间起止日期。
- V4 已知问题（原样保留，未修改）：平台“突破后回踩 / 破位后回抽”两个阶段在 V4 判定顺序下永远不可达。

## 输出

| 文件 | 内容 |
|---|---|
| `index.json` | 运行信息、大盘环境、每只股票摘要（每种形态最近一次识别结果） |
| `stocks/<代码>.json` | 分析窗口内的 K 线原样 + 摆动点 + 8 种形态全部结果 + 指标 |

## 运行

```bash
# 本地 CSV（列：date,open,high,low,close,volume）
python3 pipeline/run_daily.py --csv-dir <目录> --index-csv <上证CSV> \
    --provenance-json <来源说明.json> --expect-date 2026-10-13 --out site/data
# 测试
python3 -m pytest tests/pipeline tests/v5 -k "not Integration"
```
