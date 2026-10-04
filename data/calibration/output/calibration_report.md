# FIR-13 Empirical Calibration Report

## 概览
- 股票数量: 73
- 信号总数: 9,272
- 日期范围: 2024-05-16 ~ 2026-09-30
- Horizons: T+3, T+5, T+10, T+20, T+40

## Outcome 方向定义
- **看涨形态** (ma_bull, w_bottom, hs_bottom, platform_break)：价格上涨 = 赢，`directional_profit = forward_return`
- **看跌形态** (ma_bear, m_top, hs_top, platform_break_down)：价格下跌 = 赢，`directional_profit = -forward_return`
- `avg_return` = 原始前向收益率（价格实际涨跌方向，看涨为正/看跌为负均可能）
- `avg_directional_profit` = 按形态方向调整后的收益（正数表示该形态方向盈利）
- `win_rate` 基于方向感知的 win（看涨=涨，看跌=跌）
- `profit_factor` 基于 directional_profit 计算

## 总体胜率
| Horizon | Sample Size | Win Rate | Avg Return | Avg Dir Profit | Avg MFE | Avg MAE | Confidence |
|---------|-------------|----------|------------|----------------|---------|---------|------------|
| T+3 | 9,243 | 50.4% | 0.3% | 0.2% | 3.1% | -2.7% | empirical |
| T+5 | 9,219 | 49.9% | 0.4% | 0.3% | 4.1% | -3.5% | empirical |
| T+10 | 9,030 | 49.9% | 0.6% | -0.0% | 5.7% | -5.3% | empirical |
| T+20 | 8,940 | 49.4% | 1.2% | -0.0% | 8.2% | -7.8% | empirical |
| T+40 | 8,508 | 47.9% | 2.4% | -0.5% | 11.7% | -11.9% | empirical |

## 按形态分组（T+20）
| Pattern | Direction | Sample | Win Rate | Avg Return | Avg Dir Profit | Profit Factor | Confidence |
|---------|-----------|--------|----------|------------|----------------|---------------|------------|
| ma_bull | 看涨 | 437 | 46.7% | 1.8% | 1.8% | 1.50 | empirical |
| ma_bear | 看跌 | 587 | 53.0% | 1.0% | -1.0% | 0.77 | empirical |
| w_bottom | 看涨 | 2,211 | 46.8% | 1.2% | 1.2% | 1.34 | empirical |
| m_top | 看跌 | 1,799 | 51.5% | 1.2% | -1.2% | 0.73 | empirical |
| hs_bottom | 看涨 | 1,306 | 47.2% | 0.8% | 0.8% | 1.23 | empirical |
| hs_top | 看跌 | 1,860 | 52.3% | 1.0% | -1.0% | 0.76 | empirical |
| platform_break | 看涨 | 415 | 48.4% | 1.9% | 1.9% | 1.50 | empirical |
| platform_break_down | 看跌 | 325 | 45.2% | 2.9% | -2.9% | 0.51 | empirical |

## 按大盘环境分组（T+20）
| Regime | Sample | Win Rate | Avg Return | Profit Factor | Confidence |
|--------|--------|----------|------------|---------------|------------|
| bull | 3,045 | 50.2% | 0.3% | 1.13 | empirical |
| bear | 4,152 | 48.0% | 1.4% | 0.83 | empirical |
| range | 1,743 | 51.1% | 2.2% | 1.29 | empirical |

## 交叉分组：形态 × 大盘环境（T+20）
| Pattern | Regime | Sample | Win Rate | Avg Return | Profit Factor | Confidence |
|---------|--------|--------|----------|------------|---------------|------------|
| ma_bull | bull | 207 | 45.4% | 0.9% | 1.24 | empirical |
| ma_bull | bear | 132 | 47.0% | 0.8% | 1.20 | empirical |
| ma_bull | range | 98 | 49.0% | 5.0% | 2.86 | low_confidence |
| ma_bear | bull | 127 | 63.0% | -2.7% | 2.32 | empirical |
| ma_bear | bear | 331 | 44.4% | 3.1% | 0.47 | empirical |
| ma_bear | range | 129 | 65.1% | -0.7% | 1.21 | empirical |
| w_bottom | bull | 821 | 48.2% | 1.1% | 1.34 | empirical |
| w_bottom | bear | 985 | 44.6% | 0.7% | 1.17 | empirical |
| w_bottom | range | 405 | 49.4% | 2.5% | 1.95 | empirical |
| m_top | bull | 552 | 52.4% | 0.1% | 0.98 | empirical |
| m_top | bear | 904 | 50.7% | 1.9% | 0.62 | empirical |
| m_top | range | 343 | 52.2% | 0.8% | 0.76 | empirical |
| hs_bottom | bull | 467 | 46.3% | 0.2% | 1.04 | empirical |
| hs_bottom | bear | 579 | 46.6% | 0.6% | 1.16 | empirical |
| hs_bottom | range | 260 | 50.4% | 2.6% | 2.01 | empirical |
| hs_top | bull | 618 | 53.4% | 0.1% | 0.96 | empirical |
| hs_top | bear | 894 | 52.7% | 1.3% | 0.73 | empirical |
| hs_top | range | 348 | 49.1% | 1.9% | 0.56 | empirical |
| platform_break | bull | 187 | 45.5% | 0.1% | 1.03 | empirical |
| platform_break | bear | 120 | 50.0% | 1.2% | 1.28 | empirical |
| platform_break | range | 108 | 51.9% | 5.7% | 2.84 | empirical |
| platform_break_down | bull | 66 | 59.1% | -0.2% | 1.05 | low_confidence |
| platform_break_down | bear | 207 | 41.5% | 3.4% | 0.45 | empirical |
| platform_break_down | range | 52 | 42.3% | 4.6% | 0.34 | low_confidence |

---

置信度说明：
- `insufficient`: sample_size < 30，不报告概率（N/A）
- `low_confidence`: 30 ≤ sample_size < 100
- `empirical`: sample_size ≥ 100

收益率说明：
- `avg_return` = 原始前向收益率 (close_horizon - entry) / entry，反映价格实际涨跌
- `avg_directional_profit` = 按形态方向调整后的收益：看涨=forward_return，看跌=-forward_return；正数表示该形态方向盈利
- `win_rate` 基于方向感知的 win：看涨=价格上涨，看跌=价格下跌
- `profit_factor` = sum(winning directional_profits) / |sum(losing directional_profits)|
- 所有收益率内部存储为小数（0.05 = 5%），显示为百分比