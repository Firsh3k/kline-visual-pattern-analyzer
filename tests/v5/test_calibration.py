"""
FIR-13: Empirical Calibration — Fixed Horizon Backtester 单元测试

测试 FixedHorizonBacktester 的核心回测逻辑：
- forward_return / MFE / MAE 计算正确性
- 数据不足时的处理
- win 标志判定
- 日期精确匹配
- 入场价使用信号日收盘价
- 确定性（相同输入→相同输出）
"""

import pytest

from core.types import OHLCV
from core.calibration.types import (
    SignalOccurrence,
    HorizonBacktest,
    CalibrationDataset,
    DEFAULT_HORIZONS,
    PatternType,
    ConfirmationState,
    MarketRegime,
)
from core.calibration.backtest_engine import FixedHorizonBacktester


# ===========================================================================
# 测试辅助函数
# ===========================================================================


def _make_klines(
    dates,
    closes,
    highs=None,
    lows=None,
    opens=None,
    volumes=None,
):
    """构造测试用 OHLCV 列表

    Args:
        dates: 日期字符串列表
        closes: 收盘价列表
        highs: 最高价列表（可选，默认 close * 1.01）
        lows: 最低价列表（可选，默认 close * 0.99）
        opens: 开盘价列表（可选，默认 close * 0.995）
        volumes: 成交量列表（可选，默认 1000000）

    Returns:
        List[OHLCV]
    """
    n = len(dates)
    if highs is None:
        highs = [round(c * 1.01, 4) for c in closes]
    if lows is None:
        lows = [round(c * 0.99, 4) for c in closes]
    if opens is None:
        opens = [round(c * 0.995, 4) for c in closes]
    if volumes is None:
        volumes = [1000000.0] * n

    klines = []
    for i in range(n):
        klines.append(OHLCV(
            date=dates[i],
            open=opens[i],
            high=highs[i],
            low=lows[i],
            close=closes[i],
            volume=volumes[i],
        ))
    return klines


def _make_signal(
    signal_id="test_001",
    stock_code="600519",
    signal_date="2026-01-03",
    entry_price=10.0,
    pattern_type=PatternType.MA_BULL,
    confirmation_state=ConfirmationState.BREAKOUT_DAY,
    market_regime=MarketRegime.BULL,
):
    """构造测试用 SignalOccurrence"""
    return SignalOccurrence(
        signal_id=signal_id,
        stock_code=stock_code,
        signal_date=signal_date,
        pattern_type=pattern_type,
        entry_price=entry_price,
        confirmation_state=confirmation_state,
        market_regime=market_regime,
    )


# ===========================================================================
# 测试类
# ===========================================================================


class TestFixedHorizonBacktester:
    """FixedHorizonBacktester 单元测试"""

    # ------------------------------------------------------------------
    # Fixture: 标准测试K线（信号日 idx=2，后续有足够数据）
    # ------------------------------------------------------------------

    @pytest.fixture
    def standard_klines(self):
        """标准测试K线序列

        idx 0: 2026-01-01, close=9.8
        idx 1: 2026-01-02, close=9.9
        idx 2: 2026-01-03, close=10.0  ← signal day
        idx 3: 2026-01-06, close=10.2, high=10.3, low=9.9
        idx 4: 2026-01-07, close=10.5, high=10.6, low=10.1
        idx 5: 2026-01-08, close=10.4, high=10.8, low=10.0
        idx 6: 2026-01-09, close=10.6, high=11.0, low=10.2
        idx 7: 2026-01-10, close=10.8, high=11.2, low=10.4
        """
        dates = [
            "2026-01-01", "2026-01-02", "2026-01-03",
            "2026-01-06", "2026-01-07", "2026-01-08",
            "2026-01-09", "2026-01-10",
        ]
        closes = [9.8, 9.9, 10.0, 10.2, 10.5, 10.4, 10.6, 10.8]
        highs = [9.9, 10.0, 10.1, 10.3, 10.6, 10.8, 11.0, 11.2]
        lows = [9.7, 9.8, 9.9, 9.9, 10.1, 10.0, 10.2, 10.4]
        return _make_klines(dates, closes, highs=highs, lows=lows)

    @pytest.fixture
    def long_klines(self):
        """长K线序列（50根），支持 T+40 回测

        信号日固定在 idx=10，entry_price=10.0
        """
        dates = []
        closes = []
        highs = []
        lows = []
        base_date = "2026-01-05"
        for i in range(50):
            # 简单的缓慢上涨 + 波动
            date = f"2026-{1 + i // 30:02d}-{1 + (i % 28):02d}"
            close = 10.0 + i * 0.05
            high = close + 0.15
            low = close - 0.15
            dates.append(date)
            closes.append(round(close, 4))
            highs.append(round(high, 4))
            lows.append(round(low, 4))
        return _make_klines(dates, closes, highs=highs, lows=lows)

    # ------------------------------------------------------------------
    # 1. test_basic_backtest_t3
    # ------------------------------------------------------------------

    def test_basic_backtest_t3(self, standard_klines):
        """验证 T+3 回测的 forward_return 计算正确"""
        signal = _make_signal(
            signal_date="2026-01-03",  # idx=2
            entry_price=10.0,
        )
        bt = FixedHorizonBacktester(win_threshold=0.0)
        result = bt.backtest_signal(signal, standard_klines, horizon=3)

        # T+3 window = idx 3,4,5 (3 bars)
        # final close = 10.4 (idx 5)
        # forward_return = (10.4 - 10.0) / 10.0 = 0.04
        assert result.signal_id == "test_001"
        assert result.horizon == 3
        assert result.sufficient_data is True
        assert result.actual_bars == 3
        assert result.final_price == 10.4
        assert result.forward_return == pytest.approx(0.04, abs=1e-6)

    # ------------------------------------------------------------------
    # 2. test_mfe_mae_calculation
    # ------------------------------------------------------------------

    def test_mfe_mae_calculation(self, standard_klines):
        """验证 MFE（最大有利波动）和 MAE（最大不利波动）计算正确"""
        signal = _make_signal(
            signal_date="2026-01-03",  # idx=2
            entry_price=10.0,
        )
        bt = FixedHorizonBacktester(win_threshold=0.0)
        result = bt.backtest_signal(signal, standard_klines, horizon=3)

        # T+3 window = idx 3,4,5
        # highs: 10.3, 10.6, 10.8
        # lows:  9.9, 10.1, 10.0
        # MFE = max((10.3-10)/10, (10.6-10)/10, (10.8-10)/10) = max(0.03, 0.06, 0.08) = 0.08
        # MAE = min((9.9-10)/10, (10.1-10)/10, (10.0-10)/10) = min(-0.01, 0.01, 0.0) = -0.01
        assert result.mfe == pytest.approx(0.08, abs=1e-6)
        assert result.mae == pytest.approx(-0.01, abs=1e-6)
        # MFE 必须 >= 0, MAE 必须 <= 0
        assert result.mfe >= 0.0
        assert result.mae <= 0.0

    # ------------------------------------------------------------------
    # 3. test_insufficient_data
    # ------------------------------------------------------------------

    def test_insufficient_data(self):
        """信号日之后不足 horizon 根K线时，sufficient_data=False，收益字段=None"""
        # 总共只有 5 根K线，信号日在 idx=3
        # horizon=5，但只有 idx=4 这 1 根未来K线
        dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-06", "2026-01-07"]
        closes = [9.8, 9.9, 10.0, 10.1, 10.2]
        klines = _make_klines(dates, closes)

        signal = _make_signal(
            signal_date="2026-01-06",  # idx=3
            entry_price=10.1,
        )
        bt = FixedHorizonBacktester(win_threshold=0.0)
        result = bt.backtest_signal(signal, klines, horizon=5)

        assert result.sufficient_data is False
        assert result.actual_bars == 1  # 只有 idx=4 这一根
        assert result.forward_return is None
        assert result.mfe is None
        assert result.mae is None
        assert result.win is None
        assert result.final_price is None
        assert result.horizon == 5

    # ------------------------------------------------------------------
    # 4. test_all_horizons
    # ------------------------------------------------------------------

    def test_all_horizons(self, long_klines):
        """验证 backtest_all_horizons 返回正确数量的结果"""
        # 信号日在 idx=10
        signal = _make_signal(
            signal_date=long_klines[10].date,
            entry_price=10.0,
        )
        bt = FixedHorizonBacktester(win_threshold=0.0)
        results = bt.backtest_all_horizons(signal, long_klines)

        # 默认 horizons = (3, 5, 10, 20, 40) → 5 个结果
        assert len(results) == len(DEFAULT_HORIZONS)
        horizons_returned = [r.horizon for r in results]
        assert horizons_returned == list(DEFAULT_HORIZONS)

        # 所有结果都应该有足够数据（50根K线，信号在idx=10，后面有39根）
        # T+40 需要40根未来K线，但只有39根 → T+40 数据不足
        # T+3,5,10,20 应该都足够
        for r in results:
            if r.horizon <= 20:
                assert r.sufficient_data is True, f"horizon={r.horizon} should have sufficient data"
            else:
                # horizon=40, only 39 bars after signal → insufficient
                assert r.sufficient_data is False, f"horizon={r.horizon} should have insufficient data"
                assert r.forward_return is None

    # ------------------------------------------------------------------
    # 5. test_win_flag
    # ------------------------------------------------------------------

    def test_win_flag(self):
        """验证 win 标志基于 win_threshold 判断"""
        # 构造一个 forward_return = 0.05 的场景
        dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-06", "2026-01-07"]
        closes = [9.8, 9.9, 10.0, 10.2, 10.5]
        klines = _make_klines(dates, closes)

        signal = _make_signal(
            signal_date="2026-01-03",  # idx=2, close=10.0
            entry_price=10.0,
        )

        # Case A: return=0.05 > threshold=0.0 → win=True
        bt_a = FixedHorizonBacktester(win_threshold=0.0)
        r_a = bt_a.backtest_signal(signal, klines, horizon=2)
        assert r_a.forward_return == pytest.approx(0.05, abs=1e-6)
        assert r_a.win is True

        # Case B: return=0.05, threshold=0.1 → win=False (0.05 not > 0.1)
        bt_b = FixedHorizonBacktester(win_threshold=0.1)
        r_b = bt_b.backtest_signal(signal, klines, horizon=2)
        assert r_b.win is False

        # Case C: 负收益场景, threshold=0.0 → win=False
        dates_c = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-06", "2026-01-07"]
        closes_c = [9.8, 9.9, 10.0, 9.8, 9.5]
        klines_c = _make_klines(dates_c, closes_c)
        r_c = bt_a.backtest_signal(signal, klines_c, horizon=2)
        assert r_c.forward_return == pytest.approx(-0.05, abs=1e-6)
        assert r_c.win is False

        # Case D: 恰好等于 threshold → win=False（严格 >，不是 >=）
        # forward_return = 0.0, threshold = 0.0 → win=False
        dates_d = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-06", "2026-01-07"]
        closes_d = [9.8, 9.9, 10.0, 10.0, 10.0]
        klines_d = _make_klines(dates_d, closes_d)
        r_d = bt_a.backtest_signal(signal, klines_d, horizon=2)
        assert r_d.forward_return == pytest.approx(0.0, abs=1e-6)
        assert r_d.win is False  # 0.0 > 0.0 是 False

    # ------------------------------------------------------------------
    # 6. test_exact_date_matching
    # ------------------------------------------------------------------

    def test_exact_date_matching(self, standard_klines):
        """验证 signal_date 精确匹配K线日期"""
        # 正确日期 → 正常工作
        signal_ok = _make_signal(signal_date="2026-01-03")
        bt = FixedHorizonBacktester()
        result = bt.backtest_signal(signal_ok, standard_klines, horizon=3)
        assert result.sufficient_data is True

        # 不存在的日期 → ValueError
        signal_bad = _make_signal(signal_date="2026-01-04")  # 周末，不在klines中
        with pytest.raises(ValueError, match="not found in klines"):
            bt.backtest_signal(signal_bad, standard_klines, horizon=3)

    # ------------------------------------------------------------------
    # 7. test_entry_price_uses_signal_close
    # ------------------------------------------------------------------

    def test_entry_price_uses_signal_close(self, standard_klines):
        """验证入场价使用信号日收盘价（signal.entry_price）"""
        # signal.entry_price = 10.0（即 idx=2 的 close）
        signal = _make_signal(
            signal_date="2026-01-03",  # idx=2, close=10.0
            entry_price=10.0,
        )
        bt = FixedHorizonBacktester(win_threshold=0.0)
        result = bt.backtest_signal(signal, standard_klines, horizon=3)

        # forward_return 应该基于 entry_price=10.0 计算
        # final_price = 10.4, return = (10.4-10.0)/10.0 = 0.04
        assert result.final_price == 10.4
        assert result.forward_return == pytest.approx(0.04, abs=1e-6)

        # 验证：如果用错误的入场价（比如 9.9），结果会不同
        signal_wrong_entry = _make_signal(
            signal_date="2026-01-03",
            entry_price=9.9,  # 错误的入场价
        )
        result_wrong = bt.backtest_signal(signal_wrong_entry, standard_klines, horizon=3)
        # (10.4 - 9.9) / 9.9 ≈ 0.0505
        assert result_wrong.forward_return == pytest.approx(0.5 / 9.9, abs=1e-4)
        assert result_wrong.forward_return != result.forward_return

    # ------------------------------------------------------------------
    # 8. test_backtest_with_fixture — 确定性
    # ------------------------------------------------------------------

    def test_backtest_with_fixture(self, standard_klines):
        """用固定 fixture 数据验证可复现性（相同输入→相同输出）"""
        signal = _make_signal(
            signal_date="2026-01-03",
            entry_price=10.0,
        )
        bt = FixedHorizonBacktester(win_threshold=0.0)

        # 跑两次
        result1 = bt.backtest_signal(signal, standard_klines, horizon=3)
        result2 = bt.backtest_signal(signal, standard_klines, horizon=3)

        # 所有字段必须完全一致
        assert result1.signal_id == result2.signal_id
        assert result1.horizon == result2.horizon
        assert result1.forward_return == result2.forward_return
        assert result1.mfe == result2.mfe
        assert result1.mae == result2.mae
        assert result1.win == result2.win
        assert result1.sufficient_data == result2.sufficient_data
        assert result1.actual_bars == result2.actual_bars
        assert result1.final_price == result2.final_price

        # 也验证 to_dict 序列化一致性
        assert result1.to_dict() == result2.to_dict()

    # ------------------------------------------------------------------
    # 额外测试：backtest_dataset 集成
    # ------------------------------------------------------------------

    def test_backtest_dataset(self, standard_klines):
        """验证 backtest_dataset 对整个数据集回测"""
        signal = _make_signal(
            signal_date="2026-01-03",
            entry_price=10.0,
        )
        dataset = CalibrationDataset(
            horizons=[3, 5],
            signals=[signal],
        )
        klines_by_code = {"600519": standard_klines}

        bt = FixedHorizonBacktester()
        results = bt.backtest_dataset(dataset, klines_by_code)

        # 1 个信号 × 2 个 horizon = 2 条结果
        assert len(results) == 2
        assert [r.horizon for r in results] == [3, 5]
        # T+3: 有足够数据（8根K线，信号在idx=2，后面有5根）
        assert results[0].sufficient_data is True
        # T+5: 需要5根未来K线，但只有 idx=3,4,5,6,7 = 5根 → 刚好够
        assert results[1].sufficient_data is True

    def test_backtest_dataset_missing_stock(self):
        """验证 backtest_dataset 中股票无数据时的处理"""
        signal = _make_signal(
            signal_date="2026-01-03",
            entry_price=10.0,
        )
        dataset = CalibrationDataset(
            horizons=[3, 5],
            signals=[signal],
        )
        # klines_by_code 不包含该股票
        klines_by_code = {"999999": []}

        bt = FixedHorizonBacktester()
        results = bt.backtest_dataset(dataset, klines_by_code)

        assert len(results) == 2
        for r in results:
            assert r.sufficient_data is False
            assert r.forward_return is None
            assert r.actual_bars == 0
