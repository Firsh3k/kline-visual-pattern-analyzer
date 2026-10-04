"""
FIR-13: EmpiricalCalibrator 单元测试

覆盖：
1. empirical / low_confidence / insufficient 三档置信度
2. 胜率 / 盈亏比 / MFE/MAE 计算
3. 单维度分组（pattern / regime）与交叉分组
4. calibrate() 完整流程
"""

import os
import sys

import pytest

# 确保项目根在 sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.calibration.types import (
    PatternType,
    ConfirmationState,
    MarketRegime,
    ConfidenceLabel,
    SignalOccurrence,
    HorizonBacktest,
    DEFAULT_HORIZONS,
)
from core.calibration.calibrator import EmpiricalCalibrator


# ---------------------------------------------------------------------------
# Fixture 构造辅助
# ---------------------------------------------------------------------------

def make_signal(
    idx: int,
    pattern: PatternType = PatternType.MA_BULL,
    regime: MarketRegime = MarketRegime.BULL,
    state: ConfirmationState = ConfirmationState.BREAKOUT_DAY,
) -> SignalOccurrence:
    return SignalOccurrence(
        signal_id=f"s{idx}",
        stock_code="600000",
        signal_date="2024-01-01",
        pattern_type=pattern,
        entry_price=10.0,
        confirmation_state=state,
        market_regime=regime,
    )


def make_backtest(
    signal_id: str,
    horizon: int = 20,
    ret: float = 0.05,
    sufficient: bool = True,
    mfe: float | None = None,
    mae: float | None = None,
) -> HorizonBacktest:
    return HorizonBacktest(
        signal_id=signal_id,
        horizon=horizon,
        forward_return=ret,
        mfe=mfe if mfe is not None else abs(ret),
        mae=mae if mae is not None else -abs(ret),
        win=(ret > 0),
        sufficient_data=sufficient,
    )


def build_backtests_for_returns(
    returns: list[float],
    horizon: int = 20,
    start_idx: int = 0,
) -> list[HorizonBacktest]:
    """给定一组收益率，生成对应数量的 sufficient=True 回测记录"""
    out = []
    for i, r in enumerate(returns):
        out.append(make_backtest(f"s{start_idx + i}", horizon=horizon, ret=r, sufficient=True))
    return out


# ---------------------------------------------------------------------------
# 1. empirical（>=100）
# ---------------------------------------------------------------------------

def test_compute_metric_empirical():
    """100+ 样本 → confidence=empirical，所有指标计算正确"""
    calibrator = EmpiricalCalibrator()
    # 70 个 +0.02，50 个 -0.01，共 120 个
    returns = [0.02] * 70 + [-0.01] * 50
    backtests = build_backtests_for_returns(returns, horizon=20)

    metric = calibrator._compute_metric(backtests, horizon=20, total_signals=len(backtests))

    assert metric.sample_size == 120
    assert metric.confidence_label == ConfidenceLabel.EMPIRICAL
    # 胜率 = 70/120
    assert metric.win_rate == pytest.approx(70 / 120)
    # 平均收益 = (70*0.02 + 50*-0.01)/120 = 0.9/120
    assert metric.avg_return == pytest.approx((1.4 - 0.5) / 120)
    # 盈亏比 = 1.4 / 0.5
    assert metric.profit_factor == pytest.approx(1.4 / 0.5)
    assert metric.max_return == pytest.approx(0.02)
    assert metric.min_return == pytest.approx(-0.01)
    assert metric.std_return is not None


# ---------------------------------------------------------------------------
# 2. low_confidence（30-99）
# ---------------------------------------------------------------------------

def test_compute_metric_low_confidence():
    """30-99 样本 → confidence=low_confidence，指标仍计算"""
    calibrator = EmpiricalCalibrator()
    returns = [0.01] * 30 + [-0.01] * 20  # 50 个
    backtests = build_backtests_for_returns(returns, horizon=10)

    metric = calibrator._compute_metric(backtests, horizon=10, total_signals=50)

    assert metric.sample_size == 50
    assert metric.confidence_label == ConfidenceLabel.LOW_CONFIDENCE
    # 指标已计算（非 None）
    assert metric.win_rate is not None
    assert metric.avg_return is not None
    assert metric.median_return is not None


# ---------------------------------------------------------------------------
# 3. insufficient（<30）
# ---------------------------------------------------------------------------

def test_compute_metric_insufficient():
    """<30 样本 → confidence=insufficient，所有概率指标为 None"""
    calibrator = EmpiricalCalibrator()
    returns = [0.01] * 20  # 20 个
    backtests = build_backtests_for_returns(returns, horizon=5)

    metric = calibrator._compute_metric(backtests, horizon=5, total_signals=20)

    assert metric.sample_size == 20
    assert metric.confidence_label == ConfidenceLabel.INSUFFICIENT
    # 所有概率指标必须为 None
    assert metric.win_rate is None
    assert metric.avg_return is None
    assert metric.median_return is None
    assert metric.avg_mfe is None
    assert metric.avg_mae is None
    assert metric.profit_factor is None
    assert metric.std_return is None
    assert metric.max_return is None
    assert metric.min_return is None


# ---------------------------------------------------------------------------
# 4. 胜率计算
# ---------------------------------------------------------------------------

def test_win_rate_calculation():
    """验证胜率 = win_count / sample_size"""
    calibrator = EmpiricalCalibrator(win_threshold=0.0)
    # 33 盈 / 17 亏，共 50
    returns = [0.03] * 33 + [-0.02] * 17
    backtests = build_backtests_for_returns(returns, horizon=3)

    metric = calibrator._compute_metric(backtests, horizon=3, total_signals=50)

    assert metric.win_rate == pytest.approx(33 / 50)
    assert metric.sample_size == 50


# ---------------------------------------------------------------------------
# 5. 盈亏比
# ---------------------------------------------------------------------------

def test_profit_factor():
    """验证盈亏比计算正确；全盈利时 profit_factor=None"""
    calibrator = EmpiricalCalibrator()

    # 有盈有亏：15*+0.1 / 15*-0.05 → 1.5 / 0.75 = 2.0
    mixed = build_backtests_for_returns([0.1] * 15 + [-0.05] * 15, horizon=20)
    m1 = calibrator._compute_metric(mixed, horizon=20, total_signals=30)
    assert m1.profit_factor == pytest.approx(1.5 / 0.75)

    # 全盈利（无亏损）→ profit_factor = None
    all_win = build_backtests_for_returns([0.05] * 30, horizon=20)
    m2 = calibrator._compute_metric(all_win, horizon=20, total_signals=30)
    assert m2.profit_factor is None


# ---------------------------------------------------------------------------
# 6. 按 pattern_type 分组
# ---------------------------------------------------------------------------

def test_group_by_pattern():
    """验证按 pattern_type 分组正确"""
    signals = [
        make_signal(0, pattern=PatternType.MA_BULL),
        make_signal(1, pattern=PatternType.MA_BULL),
        make_signal(2, pattern=PatternType.MA_BEAR),
        make_signal(3, pattern=PatternType.W_BOTTOM),
    ]
    # 每个信号一个 T+20 回测
    backtests = [make_backtest(s.signal_id, horizon=20, ret=0.01, sufficient=True) for s in signals]

    calibrator = EmpiricalCalibrator()
    g_signals, g_backtests = calibrator._group_signals(
        signals, backtests, pattern_type=PatternType.MA_BULL.value
    )
    assert len(g_signals) == 2
    assert all(s.pattern_type == PatternType.MA_BULL for s in g_signals)
    assert len(g_backtests) == 2

    # "all" 不筛选
    all_signals, all_backtests = calibrator._group_signals(signals, backtests, "all", "all", "all")
    assert len(all_signals) == 4


# ---------------------------------------------------------------------------
# 7. 按 market_regime 分组
# ---------------------------------------------------------------------------

def test_group_by_regime():
    """验证按 market_regime 分组正确"""
    signals = [
        make_signal(0, regime=MarketRegime.BULL),
        make_signal(1, regime=MarketRegime.BULL),
        make_signal(2, regime=MarketRegime.BEAR),
        make_signal(3, regime=MarketRegime.RANGE),
    ]
    backtests = [make_backtest(s.signal_id, horizon=20, ret=0.01, sufficient=True) for s in signals]

    calibrator = EmpiricalCalibrator()
    g_signals, _ = calibrator._group_signals(
        signals, backtests, market_regime=MarketRegime.BULL.value
    )
    assert len(g_signals) == 2
    assert all(s.market_regime == MarketRegime.BULL for s in g_signals)


# ---------------------------------------------------------------------------
# 8. 交叉分组 pattern × regime
# ---------------------------------------------------------------------------

def test_cross_grouping():
    """验证 pattern × regime 交叉分组"""
    signals = [
        make_signal(0, pattern=PatternType.MA_BULL, regime=MarketRegime.BULL),
        make_signal(1, pattern=PatternType.MA_BULL, regime=MarketRegime.BEAR),
        make_signal(2, pattern=PatternType.MA_BEAR, regime=MarketRegime.BULL),
        make_signal(3, pattern=PatternType.MA_BULL, regime=MarketRegime.BULL),
    ]
    backtests = [make_backtest(s.signal_id, horizon=20, ret=0.01, sufficient=True) for s in signals]

    calibrator = EmpiricalCalibrator()
    # MA_BULL × BULL → 2 个
    g_signals, g_backtests = calibrator._group_signals(
        signals, backtests,
        pattern_type=PatternType.MA_BULL.value,
        market_regime=MarketRegime.BULL.value,
    )
    assert len(g_signals) == 2
    assert all(s.pattern_type == PatternType.MA_BULL for s in g_signals)
    assert all(s.market_regime == MarketRegime.BULL for s in g_signals)
    assert len(g_backtests) == 2


# ---------------------------------------------------------------------------
# 9. calibrate() 完整流程
# ---------------------------------------------------------------------------

def test_calibrate_full():
    """构造完整 signals + backtests，验证 calibrate() 返回所有分组"""
    # 构造足量信号：覆盖 2 种 pattern × 2 种 regime × 2 种 state
    signals = []
    backtests = []
    idx = 0
    for pattern in (PatternType.MA_BULL, PatternType.MA_BEAR):
        for regime in (MarketRegime.BULL, MarketRegime.BEAR):
            for state in (ConfirmationState.BREAKOUT_DAY, ConfirmationState.RETEST):
                for _ in range(40):  # 每格 40 个信号
                    s = make_signal(idx, pattern=pattern, regime=regime, state=state)
                    signals.append(s)
                    for h in DEFAULT_HORIZONS:
                        backtests.append(
                            make_backtest(s.signal_id, horizon=h, ret=0.01, sufficient=True)
                        )
                    idx += 1

    calibrator = EmpiricalCalibrator()
    summary = calibrator.calibrate(signals, backtests, horizons=list(DEFAULT_HORIZONS))

    # 总信号数
    assert summary.total_signals == idx
    assert summary.stock_count == 1

    # 总体分组存在
    overall = summary.find_group("all", "all", "all")
    assert overall is not None
    # 每个 horizon 都有 metric
    for h in DEFAULT_HORIZONS:
        assert h in overall.metrics_by_horizon
    # 总体样本量足够 → empirical
    assert overall.metrics_by_horizon[20].confidence_label == ConfidenceLabel.EMPIRICAL

    # 按 pattern 分组存在
    assert summary.find_group(PatternType.MA_BULL.value, "all", "all") is not None
    # 按 regime 分组存在
    assert summary.find_group("all", MarketRegime.BULL.value, "all") is not None
    # 按 state 分组存在
    assert summary.find_group("all", "all", ConfirmationState.BREAKOUT_DAY.value) is not None
    # 交叉分组存在
    cross = summary.find_group(PatternType.MA_BULL.value, MarketRegime.BULL.value, "all")
    assert cross is not None
    cross2 = summary.find_group(PatternType.MA_BULL.value, "all", ConfirmationState.RETEST.value)
    assert cross2 is not None

    # 分组总数：1 + 8 + 3 + 5 + 8*3 + 8*5 = 1+8+3+5+24+40 = 81
    assert len(summary.groups) == 81


# ---------------------------------------------------------------------------
# 10. MFE/MAE 均值方向
# ---------------------------------------------------------------------------

def test_mfe_mae_averages():
    """验证 avg_mfe >= 0, avg_mae <= 0"""
    calibrator = EmpiricalCalibrator()
    # 混合正负收益
    returns = [0.05, -0.03, 0.02, -0.01, 0.08, -0.04] * 10  # 60 个
    backtests = build_backtests_for_returns(returns, horizon=20)

    metric = calibrator._compute_metric(backtests, horizon=20, total_signals=60)

    assert metric.sample_size == 60
    assert metric.avg_mfe is not None and metric.avg_mfe >= 0
    assert metric.avg_mae is not None and metric.avg_mae <= 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
