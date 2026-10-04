# FIR-13 Empirical Calibration Report

## 概览
- 股票数量: 73
- 信号总数: 11,264
- 日期范围: 2024-05-16 ~ 2026-09-30
- Horizons: T+3, T+5, T+10, T+20, T+40

## 总体胜率
| Horizon | Sample Size | Win Rate | Avg Return | Avg MFE | Avg MAE | Confidence |
|---------|-------------|----------|------------|---------|---------|------------|
| T+3 | 11,201 | 46.5% | 0.2% | 3.5% | -2.7% | empirical |
| T+5 | 11,165 | 46.9% | 0.4% | 4.7% | -3.5% | empirical |
| T+10 | 11,082 | 46.2% | 0.6% | 6.9% | -4.9% | empirical |
| T+20 | 10,925 | 47.6% | 1.3% | 10.2% | -6.8% | empirical |
| T+40 | 10,732 | 48.9% | 2.0% | 15.0% | -9.4% | empirical |

## 按形态分组（T+20）
| Pattern | Sample | Win Rate | Avg Return | Profit Factor | Confidence |
|---------|--------|----------|------------|---------------|------------|
| ma_bull | 1,037 | 47.6% | 1.7% | 1.47 | empirical |
| ma_bear | 1,712 | 49.1% | 1.2% | 1.38 | empirical |
| w_bottom | 2,557 | 48.1% | 1.5% | 1.41 | empirical |
| m_top | 2,038 | 44.6% | 0.2% | 1.07 | empirical |
| hs_bottom | 1,318 | 45.4% | 1.1% | 1.34 | empirical |
| hs_top | 1,230 | 48.9% | 1.3% | 1.44 | empirical |
| platform_break | 616 | 45.6% | 1.4% | 1.34 | empirical |
| platform_break_down | 417 | 57.8% | 3.8% | 2.40 | empirical |

## 按大盘环境分组（T+20）
| Regime | Sample | Win Rate | Avg Return | Profit Factor | Confidence |
|--------|--------|----------|------------|---------------|------------|
| bull | 3,696 | 47.8% | 1.3% | 1.37 | empirical |
| bear | 4,922 | 48.6% | 1.1% | 1.32 | empirical |
| range | 2,307 | 45.0% | 1.6% | 1.51 | empirical |

## 交叉分组：形态 × 大盘环境（T+20）
| Pattern | Regime | Sample | Win Rate | Avg Return | Profit Factor | Confidence |
|---------|--------|--------|----------|------------|---------------|------------|
| ma_bull | bull | 487 | 48.7% | 1.5% | 1.43 | empirical |
| ma_bull | bear | 311 | 45.0% | -0.1% | 0.98 | empirical |
| ma_bull | range | 239 | 49.0% | 4.7% | 2.67 | empirical |
| ma_bear | bull | 321 | 35.2% | -2.7% | 0.44 | empirical |
| ma_bear | bear | 1,001 | 54.4% | 2.7% | 1.97 | empirical |
| ma_bear | range | 390 | 46.9% | 0.8% | 1.25 | empirical |
| w_bottom | bull | 1,134 | 57.6% | 4.0% | 2.45 | empirical |
| w_bottom | bear | 867 | 37.7% | -1.8% | 0.64 | empirical |
| w_bottom | range | 556 | 45.1% | 1.6% | 1.50 | empirical |
| m_top | bull | 348 | 34.5% | -2.7% | 0.43 | empirical |
| m_top | bear | 1,308 | 49.3% | 1.3% | 1.44 | empirical |
| m_top | range | 382 | 37.4% | -0.7% | 0.80 | empirical |
| hs_bottom | bull | 688 | 47.5% | 2.4% | 1.76 | empirical |
| hs_bottom | bear | 328 | 45.1% | -0.5% | 0.88 | empirical |
| hs_bottom | range | 302 | 40.7% | 0.1% | 1.04 | empirical |
| hs_top | bull | 330 | 46.7% | -0.5% | 0.86 | empirical |
| hs_top | bear | 671 | 50.8% | 2.1% | 1.75 | empirical |
| hs_top | range | 229 | 46.7% | 1.4% | 1.60 | empirical |
| platform_break | bull | 318 | 42.1% | -0.4% | 0.92 | empirical |
| platform_break | bear | 155 | 47.1% | 0.7% | 1.15 | empirical |
| platform_break | range | 143 | 51.7% | 6.1% | 2.87 | empirical |
| platform_break_down | bull | 70 | 38.6% | -0.5% | 0.89 | low_confidence |
| platform_break_down | bear | 281 | 61.9% | 4.5% | 2.79 | empirical |
| platform_break_down | range | 66 | 60.6% | 5.5% | 3.58 | low_confidence |

---

置信度说明：
- `insufficient`: sample_size < 30，不报告概率（N/A）
- `low_confidence`: 30 ≤ sample_size < 100
- `empirical`: sample_size ≥ 100

所有收益率为前向收益率（小数×100 显示为百分比）。