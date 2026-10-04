"""
FIR-13 / P0-1: 看跌形态 outcome semantics 修复测试

验证看跌形态（ma_bear / m_top / hs_top / platform_break_down）的回测与统计
方向正确 —— 价格下跌 = 盈利 = win；同时保证看涨形态行为不变（回归）。

覆盖：
1. 看跌信号后价格下跌 → win=True, directional_profit>0, MFE>0, MAE<0
2. 看跌信号后价格上涨 → win=False, directional_profit<0
3. 看涨信号后价格上涨 → win=True, directional_profit>0（回归）
4. calibrator 对看跌分组：win_rate 基于方向感知 win，profit_factor 基于 directional_profit
5. 30+ 看跌样本（半跌半涨）→ win_rate≈0.5, avg_directional_profit≈0, avg_return≈0
6. HorizonBacktest / CalibrationMetric 新字段序列化 round-trip
"""

import os
import sys

import pytest

# 确保项目根在 sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.types import OHLCV
from core.calibration.types import (
    PatternType,
    ConfirmationState,
    MarketRegime,
    ConfidenceLabel,
    SignalOccurrence,
    HorizonBacktest,
    CalibrationMetric,
    CalibrationGroup,
)
from core.calibration.backtest_engine import FixedHorizonBacktester
from core.calibration.calibrator import EmpiricalCalibrator


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------

ENTRY_PRICE = 10.0
SIGNAL_DATE = "2024-01-01"


def make_signal(pattern: PatternType, signal_id: str = "sig1") -> SignalOccurrence:
    """构造一个信号，entry_price 固定为 ENTRY_PRICE。"""
    return SignalOccurrence(
        signal_id=signal_id,
        stock_code="600000",
        signal_date=SIGNAL_DATE,
        pattern_type=pattern,
        entry_price=ENTRY_PRICE,
        confirmation_state=ConfirmationState.BREAKOUT_DAY,
        market_regime=MarketRegime.BULL,
    )


def make_klines(after_bars: list[tuple[float, float, float, float]]) -> list[OHLCV]:
    """构造 klines：第 0 根为信号日（close=ENTRY_PRICE），其后为指定 (open, high, low, close)。"""
    bars = [OHLCV(
        date=SIGNAL_DATE, open=ENTRY_PRICE, high=ENTRY_PRICE,
        low=ENTRY_PRICE, close=ENTRY_PRICE, volume=1.0,
    )]
    for i, (o, h, l, c) in enumerate(after_bars):
        bars.append(OHLCV(
            date=f"2024-01-{i + 2:02d}", open=o, high=h, low=l, close=c, volume=1.0,
        ))
    return bars


# ---------------------------------------------------------------------------
# 1. 看跌信号后价格下跌 → win=True, directional_profit>0, MFE>0, MAE<0
# ---------------------------------------------------------------------------

def test_bearish_price_down_is_win():
    """看跌形态 + 价格下跌 10%：下跌算赢。"""
    # 4 根后续K线：收盘跌到 9.0（-10%），期间最低到 8.9（-11%），最高到 10.1（+1%）
    after = [
        (9.8, 10.1, 9.5, 9.6),
        (9.6, 9.7, 9.2, 9.3),
        (9.3, 9.4, 8.9, 9.0),
        (9.0, 9.1, 8.95, 9.0),
    ]
    klines = make_klines(after)
    signal = make_signal(PatternType.MA_BEAR, signal_id="bear_down")
    bt = FixedHorizonBacktester(win_threshold=0.0).backtest_signal(signal, klines, horizon=4)

    assert bt.sufficient_data is True
    assert bt.direction == "bearish"
    # forward_return = (9.0 - 10.0)/10 = -0.10
    assert bt.forward_return == pytest.approx(-0.10)
    # 看跌：directional_profit = -forward_return = +0.10
    assert bt.directional_profit == pytest.approx(0.10)
    assert bt.directional_profit > 0
    # win：价格下跌超过阈值（0）→ True
    assert bt.win is True
    # MFE：最大下跌幅度 = 11%，正数
    assert bt.mfe == pytest.approx(0.11)
    assert bt.mfe > 0
    # MAE：最大上涨幅度 = 1%，取负 = -0.01
    assert bt.mae == pytest.approx(-0.01)
    assert bt.mae < 0


# ---------------------------------------------------------------------------
# 2. 看跌信号后价格上涨 → win=False, directional_profit<0
# ---------------------------------------------------------------------------

def test_bearish_price_up_is_loss():
    """看跌形态 + 价格上涨 10%：上涨算输。"""
    after = [
        (10.2, 10.5, 10.1, 10.4),
        (10.4, 10.8, 10.3, 10.6),
        (10.6, 11.0, 10.5, 10.9),
        (10.9, 11.1, 10.8, 11.0),
    ]
    klines = make_klines(after)
    signal = make_signal(PatternType.M_TOP, signal_id="bear_up")
    bt = FixedHorizonBacktester(win_threshold=0.0).backtest_signal(signal, klines, horizon=4)

    assert bt.sufficient_data is True
    assert bt.direction == "bearish"
    # forward_return = (11.0 - 10.0)/10 = +0.10
    assert bt.forward_return == pytest.approx(0.10)
    # directional_profit = -0.10 < 0
    assert bt.directional_profit == pytest.approx(-0.10)
    assert bt.directional_profit < 0
    # win：上涨不算赢 → False
    assert bt.win is False
    # MFE：最大下跌（这里几乎没跌），取负数后转正；MAE：最大上涨幅度取负
    assert bt.mae is not None and bt.mae < 0


# ---------------------------------------------------------------------------
# 3. 看涨信号后价格上涨 → win=True, directional_profit>0（回归）
# ---------------------------------------------------------------------------

def test_bullish_price_up_is_win_regression():
    """看涨形态 + 价格上涨 10%：保持原有逻辑不变。"""
    after = [
        (10.2, 10.5, 9.95, 10.4),
        (10.4, 10.8, 10.3, 10.6),
        (10.6, 11.0, 10.5, 10.9),
        (10.9, 11.1, 10.8, 11.0),
    ]
    klines = make_klines(after)
    signal = make_signal(PatternType.MA_BULL, signal_id="bull_up")
    bt = FixedHorizonBacktester(win_threshold=0.0).backtest_signal(signal, klines, horizon=4)

    assert bt.sufficient_data is True
    assert bt.direction == "bullish"
    assert bt.forward_return == pytest.approx(0.10)
    # 看涨：directional_profit = forward_return = +0.10
    assert bt.directional_profit == pytest.approx(0.10)
    assert bt.directional_profit > 0
    assert bt.win is True
    # MFE：最大上涨幅度（11%），正数
    assert bt.mfe == pytest.approx(0.11)
    assert bt.mfe > 0
    # MAE：期间最低到 9.95（-0.5%），负数
    assert bt.mae == pytest.approx(-0.005)
    assert bt.mae < 0


def test_bullish_price_down_is_loss_regression():
    """看涨形态 + 价格下跌 10%：原有逻辑保持不变（回归）。"""
    after = [
        (9.8, 10.1, 9.5, 9.6),
        (9.6, 9.7, 9.2, 9.3),
        (9.3, 9.4, 8.9, 9.0),
        (9.0, 9.1, 8.95, 9.0),
    ]
    klines = make_klines(after)
    signal = make_signal(PatternType.W_BOTTOM, signal_id="bull_down")
    bt = FixedHorizonBacktester(win_threshold=0.0).backtest_signal(signal, klines, horizon=4)

    assert bt.direction == "bullish"
    assert bt.forward_return == pytest.approx(-0.10)
    assert bt.directional_profit == pytest.approx(-0.10)
    assert bt.win is False
    assert bt.mfe is not None and bt.mfe >= 0
    assert bt.mae == pytest.approx(-0.11)


# ---------------------------------------------------------------------------
# 辅助：构造看跌方向的 HorizonBacktest（半跌半涨）
# ---------------------------------------------------------------------------

def bearish_backtest(signal_id: str, price_up: bool, horizon: int = 20,
                     mag: float = 0.05) -> HorizonBacktest:
    """构造一个方向感知为 bearish 的回测记录。

    price_up=False → 价格下跌 mag（看跌盈利）：win=True, dp=+mag
    price_up=True  → 价格上涨 mag（看跌亏损）：win=False, dp=-mag
    """
    fwd = mag if price_up else -mag
    return HorizonBacktest(
        signal_id=signal_id,
        horizon=horizon,
        forward_return=fwd,
        mfe=mag,                 # 方向感知 MFE：正数=有利
        mae=-mag,                # 方向感知 MAE：负数=不利
        win=(not price_up),      # 下跌才赢
        sufficient_data=True,
        direction="bearish",
        directional_profit=(-fwd),   # = +mag if down, -mag if up
    )


# ---------------------------------------------------------------------------
# 4. calibrator 对看跌分组：win_rate/profit_factor 基于方向感知量
# ---------------------------------------------------------------------------

def test_calibrator_bearish_group_directional():
    """看跌分组：33 跌（赢）/ 17 涨（输）→ win_rate=33/50，profit_factor 基于 dp。"""
    calibrator = EmpiricalCalibrator(win_threshold=0.0)
    backtests = (
        [bearish_backtest(f"d{i}", price_up=False) for i in range(33)] +
        [bearish_backtest(f"u{i}", price_up=True) for i in range(17)]
    )
    metric = calibrator._compute_metric(backtests, horizon=20, total_signals=50)

    assert metric.sample_size == 50
    # win_rate 基于方向感知 win：33 个下跌样本赢
    assert metric.win_rate == pytest.approx(33 / 50)
    # directional_profit：33*+0.05 + 17*-0.05
    assert metric.avg_directional_profit == pytest.approx((33 * 0.05 - 17 * 0.05) / 50)
    # raw avg_return：33*-0.05 + 17*+0.05（价格实际涨跌）
    assert metric.avg_return == pytest.approx((-33 * 0.05 + 17 * 0.05) / 50)
    # profit_factor：gross profit = 33*0.05=1.65；gross loss = 17*0.05=0.85
    assert metric.profit_factor == pytest.approx(1.65 / 0.85)


# ---------------------------------------------------------------------------
# 5. 30+ 看跌样本（半跌半涨）→ win_rate≈0.5, avg_dp≈0, avg_return≈0
# ---------------------------------------------------------------------------

def test_bearish_half_down_half_up():
    """30+ 看跌样本一半跌一半涨：方向中性时各统计量对称归零。"""
    calibrator = EmpiricalCalibrator(win_threshold=0.0)
    n_each = 20  # 共 40 个
    backtests = (
        [bearish_backtest(f"d{i}", price_up=False) for i in range(n_each)] +
        [bearish_backtest(f"u{i}", price_up=True) for i in range(n_each)]
    )
    metric = calibrator._compute_metric(backtests, horizon=20, total_signals=40)

    assert metric.sample_size == 40
    assert metric.confidence_label == ConfidenceLabel.LOW_CONFIDENCE  # 30<=40<100
    assert metric.win_rate == pytest.approx(0.5)
    assert metric.avg_directional_profit == pytest.approx(0.0, abs=1e-9)
    assert metric.avg_return == pytest.approx(0.0, abs=1e-9)
    # 涨跌对称 → profit_factor = gross_profit/gross_loss = 1.0
    assert metric.profit_factor == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 6. 序列化 / 反序列化 round-trip
# ---------------------------------------------------------------------------

def test_horizon_backtest_roundtrip():
    """HorizonBacktest 新字段 direction/directional_profit 序列化往返。"""
    original = HorizonBacktest(
        signal_id="rt1",
        horizon=10,
        forward_return=-0.08,
        mfe=0.09,
        mae=-0.02,
        win=True,
        sufficient_data=True,
        actual_bars=10,
        final_price=9.2,
        direction="bearish",
        directional_profit=0.08,
    )
    d = original.to_dict()
    restored = HorizonBacktest.from_dict(d)
    assert restored.direction == "bearish"
    assert restored.directional_profit == pytest.approx(0.08)
    assert restored.win is True
    assert restored.to_dict() == original.to_dict()


def test_horizon_backtest_from_dict_backward_compat():
    """旧数据（无 direction/directional_profit）反序列化使用默认值，不报错。"""
    old = {
        'signal_id': 'old1',
        'horizon': 5,
        'forward_return': 0.03,
        'mfe': 0.04,
        'mae': -0.01,
        'win': True,
        'sufficient_data': True,
        'actual_bars': 5,
        'final_price': 10.3,
    }
    b = HorizonBacktest.from_dict(old)
    assert b.direction == "bullish"          # 默认值
    assert b.directional_profit is None       # 默认值


def test_calibration_metric_roundtrip():
    """CalibrationMetric 新字段 avg_directional_profit 序列化往返。"""
    from core.calibration.types import ConfidenceLabel as CL
    original = CalibrationMetric(
        horizon=20,
        sample_size=50,
        total_signals=50,
        confidence_label=CL.LOW_CONFIDENCE,
        win_rate=0.6,
        avg_return=-0.01,
        median_return=-0.01,
        avg_mfe=0.05,
        avg_mae=-0.03,
        profit_factor=1.5,
        std_return=0.04,
        max_return=0.05,
        min_return=-0.06,
        avg_directional_profit=0.01,
    )
    d = original.to_dict()
    assert d['avg_directional_profit'] == pytest.approx(0.01)
    restored = CalibrationMetric.from_dict(d)
    assert restored.avg_directional_profit == pytest.approx(0.01)
    assert restored.to_dict() == original.to_dict()


def test_calibration_metric_from_dict_backward_compat():
    """旧 CalibrationMetric 数据（无 avg_directional_profit）反序列化 → None。"""
    old = {
        'horizon': 20,
        'sample_size': 50,
        'total_signals': 50,
        'confidence_label': 'low_confidence',
        'win_rate': 0.6,
        'avg_return': -0.01,
        'median_return': -0.01,
        'avg_mfe': 0.05,
        'avg_mae': -0.03,
        'profit_factor': 1.5,
        'std_return': 0.04,
        'max_return': 0.05,
        'min_return': -0.06,
    }
    m = CalibrationMetric.from_dict(old)
    assert m.avg_directional_profit is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
