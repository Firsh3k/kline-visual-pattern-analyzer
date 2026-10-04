# FIR-13 Empirical Calibration Report

## 概览
- 股票数量: 73
- 信号总数: 8,646
- 日期范围: 2024-05-16 ~ 2026-09-30
- Horizons: T+3, T+5, T+10, T+20, T+40

## 总体胜率
| Horizon | Sample Size | Win Rate | Avg Return | Avg MFE | Avg MAE | Confidence |
|---------|-------------|----------|------------|---------|---------|------------|
| T+3 | 8,610 | 47.4% | 0.4% | 3.4% | -2.5% | empirical |
| T+5 | 8,587 | 48.3% | 0.6% | 4.5% | -3.3% | empirical |
| T+10 | 8,420 | 47.6% | 0.6% | 6.6% | -4.6% | empirical |
| T+20 | 8,319 | 47.7% | 1.3% | 9.7% | -6.5% | empirical |
| T+40 | 7,914 | 49.5% | 2.5% | 15.0% | -8.9% | empirical |

## 按形态分组（T+20）
| Pattern | Sample | Win Rate | Avg Return | Profit Factor | Confidence |
|---------|--------|----------|------------|---------------|------------|
| ma_bull | 437 | 46.7% | 1.8% | 1.50 | empirical |
| ma_bear | 587 | 46.7% | 1.0% | 1.29 | empirical |
| w_bottom | 2,026 | 46.0% | 1.0% | 1.29 | empirical |
| m_top | 1,519 | 48.0% | 1.3% | 1.40 | empirical |
| hs_bottom | 1,235 | 47.4% | 1.2% | 1.32 | empirical |
| hs_top | 1,775 | 48.6% | 1.1% | 1.34 | empirical |
| platform_break | 415 | 48.4% | 1.9% | 1.50 | empirical |
| platform_break_down | 325 | 54.5% | 2.9% | 1.97 | empirical |

## 按大盘环境分组（T+20）
| Regime | Sample | Win Rate | Avg Return | Profit Factor | Confidence |
|--------|--------|----------|------------|---------------|------------|
| bull | 2,847 | 46.0% | 0.3% | 1.09 | empirical |
| bear | 3,827 | 48.6% | 1.5% | 1.42 | empirical |
| range | 1,645 | 48.3% | 2.4% | 1.88 | empirical |

## 交叉分组：形态 × 大盘环境（T+20）
| Pattern | Regime | Sample | Win Rate | Avg Return | Profit Factor | Confidence |
|---------|--------|--------|----------|------------|---------------|------------|
| ma_bull | bull | 207 | 45.4% | 0.9% | 1.24 | empirical |
| ma_bull | bear | 132 | 47.0% | 0.8% | 1.20 | empirical |
| ma_bull | range | 98 | 49.0% | 5.0% | 2.86 | low_confidence |
| ma_bear | bull | 127 | 37.0% | -2.7% | 0.43 | empirical |
| ma_bear | bear | 331 | 55.0% | 3.1% | 2.15 | empirical |
| ma_bear | range | 129 | 34.9% | -0.7% | 0.82 | empirical |
| w_bottom | bull | 750 | 48.4% | 0.9% | 1.26 | empirical |
| w_bottom | bear | 892 | 43.4% | 0.3% | 1.08 | empirical |
| w_bottom | range | 384 | 47.4% | 2.9% | 2.06 | empirical |
| m_top | bull | 459 | 45.1% | -0.1% | 0.98 | empirical |
| m_top | bear | 759 | 50.2% | 2.1% | 1.69 | empirical |
| m_top | range | 301 | 46.8% | 1.1% | 1.43 | empirical |
| hs_bottom | bull | 446 | 46.2% | 0.8% | 1.22 | empirical |
| hs_bottom | bear | 543 | 47.5% | 1.1% | 1.27 | empirical |
| hs_bottom | range | 246 | 49.2% | 2.0% | 1.72 | empirical |
| hs_top | bull | 605 | 46.8% | 0.1% | 1.03 | empirical |
| hs_top | bear | 843 | 48.4% | 1.3% | 1.39 | empirical |
| hs_top | range | 327 | 52.6% | 2.4% | 1.99 | empirical |
| platform_break | bull | 187 | 45.5% | 0.1% | 1.03 | empirical |
| platform_break | bear | 120 | 50.0% | 1.2% | 1.28 | empirical |
| platform_break | range | 108 | 51.9% | 5.7% | 2.84 | empirical |
| platform_break_down | bull | 66 | 39.4% | -0.2% | 0.95 | low_confidence |
| platform_break_down | bear | 207 | 58.5% | 3.4% | 2.22 | empirical |
| platform_break_down | range | 52 | 57.7% | 4.6% | 2.97 | low_confidence |

---

置信度说明：
- `insufficient`: sample_size < 30，不报告概率（N/A）
- `low_confidence`: 30 ≤ sample_size < 100
- `empirical`: sample_size ≥ 100

所有收益率为前向收益率（小数×100 显示为百分比）。