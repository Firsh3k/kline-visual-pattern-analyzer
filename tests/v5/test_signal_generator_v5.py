"""
FIR-24: signal_generator V5 改造测试

覆盖：
- Phase 2: W/M/H&S 走 canonical detector（不再有 legacy 方法）
- Phase 3: bullish/bearish 方向一致性语义
- Phase 4: 事件去重（同 stock+pattern 在 20 自然日内只留最早）
- Phase 5: SignalOccurrence provenance/quality manifest 字段
"""

import os
import sys
from datetime import date, timedelta

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.types import OHLCV  # noqa: E402
from core.calibration.types import (  # noqa: E402
    SignalOccurrence,
    PatternType,
    ConfirmationState,
    MarketRegime,
)
from core.calibration.signal_generator import CalibrationSignalGenerator  # noqa: E402


def _sig(
    pattern_type=PatternType.W_BOTTOM,
    signal_date="2026-01-10",
    entry_price=100.0,
    neckline_price=98.0,
    stock_code="600519",
    score=80,
):
    return SignalOccurrence(
        signal_id=f"{stock_code}_{signal_date}_{pattern_type.value}_0",
        stock_code=stock_code,
        signal_date=signal_date,
        pattern_type=pattern_type,
        entry_price=entry_price,
        confirmation_state=ConfirmationState.BREAKOUT_DAY,
        market_regime=MarketRegime.RANGE,
        pattern_score=score,
        neckline_price=neckline_price,
    )


# ===========================================================================
# Phase 3: 方向一致性
# ===========================================================================

class TestDirectionalSemantics:
    """bullish 形态只在站上关键位触发；bearish 形态只在跌破关键位触发"""

    def setup_method(self):
        self.gen = CalibrationSignalGenerator()

    def test_bullish_pattern_not_triggered_in_downtrend(self):
        """看涨形态：entry 远低于颈线 → 被过滤"""
        sig = _sig(PatternType.W_BOTTOM, entry_price=90.0, neckline_price=98.0)
        # 90 < 98*0.98=96.04 → 不满足
        out = self.gen._filter_directional_consistency([sig])
        assert out == []

    def test_bearish_pattern_not_triggered_in_uptrend(self):
        """看跌形态：entry 远高于颈线 → 被过滤"""
        sig = _sig(PatternType.M_TOP, entry_price=110.0, neckline_price=100.0)
        # 110 > 100*1.02=102 → 不满足
        out = self.gen._filter_directional_consistency([sig])
        assert out == []

    def test_bullish_pattern_triggered_after_breakout(self):
        """看涨形态：entry 站上颈线（>= nl*0.98）→ 保留"""
        sig = _sig(PatternType.W_BOTTOM, entry_price=99.0, neckline_price=98.0)
        out = self.gen._filter_directional_consistency([sig])
        assert len(out) == 1
        # platform_break: entry >= neckline
        pb = _sig(PatternType.PLATFORM_BREAK, entry_price=101.0, neckline_price=100.0)
        assert len(self.gen._filter_directional_consistency([pb])) == 1

    def test_bearish_pattern_triggered_after_breakdown(self):
        """看跌形态：entry 跌破颈线（<= nl*1.02）→ 保留"""
        sig = _sig(PatternType.HS_TOP, entry_price=99.0, neckline_price=100.0)
        out = self.gen._filter_directional_consistency([sig])
        assert len(out) == 1
        pbd = _sig(PatternType.PLATFORM_BREAK_DOWN, entry_price=99.0, neckline_price=100.0)
        assert len(self.gen._filter_directional_consistency([pbd])) == 1


# ===========================================================================
# Phase 4: 事件去重
# ===========================================================================

class TestDedup:
    """同 (stock, pattern_type) 在 DEDUP_WINDOW=20 自然日内只保留最早信号"""

    def setup_method(self):
        self.gen = CalibrationSignalGenerator()

    def test_dedup_same_pattern_within_window(self):
        """同股票同形态、间隔 <=20 天 → 只留第一个"""
        s1 = _sig(signal_date="2026-01-10")
        s2 = _sig(signal_date="2026-01-20")  # 10 天后
        out = self.gen._deduplicate_signals([s1, s2])
        assert len(out) == 1
        assert out[0].signal_date == "2026-01-10"

    def test_dedup_keeps_first_signal(self):
        """乱序输入也保留最早日期（窗口内）"""
        s2 = _sig(signal_date="2026-01-18")  # 13 天后
        s1 = _sig(signal_date="2026-01-05")
        out = self.gen._deduplicate_signals([s2, s1])
        assert len(out) == 1
        assert out[0].signal_date == "2026-01-05"

    def test_dedup_different_patterns_not_affected(self):
        """同股票不同形态 → 都保留"""
        a = _sig(PatternType.W_BOTTOM, signal_date="2026-01-10")
        b = _sig(PatternType.M_TOP, signal_date="2026-01-15")
        out = self.gen._deduplicate_signals([a, b])
        assert len(out) == 2

    def test_dedup_different_stocks_not_affected(self):
        """不同股票同形态 → 都保留"""
        a = _sig(stock_code="600519", signal_date="2026-01-10")
        b = _sig(stock_code="000001", signal_date="2026-01-12")
        out = self.gen._deduplicate_signals([a, b])
        assert len(out) == 2

    def test_dedup_signals_outside_window_kept(self):
        """间隔 >20 天 → 都保留"""
        a = _sig(signal_date="2026-01-10")
        b = _sig(signal_date="2026-02-15")  # 36 天后
        out = self.gen._deduplicate_signals([a, b])
        assert len(out) == 2


# ===========================================================================
# Phase 5: provenance / quality manifest
# ===========================================================================

class TestProvenance:
    """SignalOccurrence provenance 字段"""

    def test_signal_occurrence_provenance_fields(self):
        """provenance 字段有默认值且可设置"""
        s = SignalOccurrence(
            signal_id="x", stock_code="1", signal_date="2026-01-01",
            pattern_type=PatternType.MA_BULL, entry_price=1.0,
            confirmation_state=ConfirmationState.BREAKOUT_DAY,
            market_regime=MarketRegime.RANGE,
        )
        assert s.data_source == ""
        assert s.adjustment == ""
        assert s.data_quality == "OK"
        assert s.guardrail_flags == []

    def test_signal_occurrence_symmetric_dict(self):
        """to_dict / from_dict 往返对称"""
        s = SignalOccurrence(
            signal_id="x", stock_code="600519", signal_date="2026-01-01",
            pattern_type=PatternType.HS_BOTTOM, entry_price=32.4,
            confirmation_state=ConfirmationState.POST_BREAK,
            market_regime=MarketRegime.BULL,
            pattern_score=90,
            data_source="multi_source", adjustment="qfq", data_quality="OK",
            detector_version="canonical_v5.2.0", pattern_confidence="high",
            guardrail_flags=["head_prominence_borderline"],
        )
        d = s.to_dict()
        s2 = SignalOccurrence.from_dict(d)
        assert s2.to_dict() == d
        assert s2.data_source == "multi_source"
        assert s2.detector_version == "canonical_v5.2.0"
        assert s2.guardrail_flags == ["head_prominence_borderline"]

    def test_provenance_fields_populated_in_generator(self):
        """generator 产出的信号应填充 detector_version / confidence / data_source"""
        # 构造单调上升序列触发 ma_bull（builtin_v1）
        closes = [100.0 + i * 0.3 for i in range(90)]
        klines = []
        base = date(2024, 1, 2)
        for i, c in enumerate(closes):
            d = (base + timedelta(days=i)).isoformat()
            klines.append(OHLCV(date=d, open=c - 0.1, high=c + 0.2,
                               low=c - 0.2, close=c, volume=2000.0))
        gen = CalibrationSignalGenerator()
        sigs = gen.generate_signals(klines, "600519", {},
                                    data_source="multi_source", adjustment="qfq")
        assert sigs, "上升趋势应有信号"
        for s in sigs:
            assert s.data_source == "multi_source"
            assert s.adjustment == "qfq"
            assert s.data_quality == "OK"
            assert s.detector_version  # 非空
            assert s.pattern_confidence in ("high", "medium", "low")
