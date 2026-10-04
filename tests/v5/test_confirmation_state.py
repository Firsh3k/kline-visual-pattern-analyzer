"""
P1 / FIR-24: confirmation-state contract 测试

覆盖：
- _EMIT_STAGES 包含 BREAKOUT_DAY / POST_BREAK / RETEST / FAILED_BREAK
- 突破后回踩颈线 → signal_generator 发出 RETEST 信号
- 突破后连续 ≥3 日跌回颈线下 → 发出 FAILED_BREAK 信号
- PRE_BREAK / FORMING 状态不被发出（形态未确认突破，不构成交易信号）

说明：校准层对同一 (stock, pattern) 有 20 自然日去重，因此把 W 底结构 +
突破 + 回踩/失败安排在 generator 的 MIN_START=60 窗口**之前**完成，
使首个被 generator 看到的该形态信号本身就已是 RETEST / FAILED_BREAK。
"""

import os
import sys
from datetime import date, timedelta

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.types import OHLCV, PatternStage  # noqa: E402
from core.calibration.signal_generator import (  # noqa: E402
    CalibrationSignalGenerator,
    _EMIT_STAGES,
    _STAGE_TO_CONFIRMATION,
)
from core.calibration.types import (  # noqa: E402
    ConfirmationState,
    MarketRegime,
)


def make_klines(prices, start=date(2025, 1, 1)):
    out = []
    for i, p in enumerate(prices):
        d = (start + timedelta(days=i)).isoformat()
        out.append(OHLCV(
            date=d, open=float(p), high=round(float(p) * 1.01, 4),
            low=round(float(p) * 0.99, 4), close=float(p), volume=1000000.0,
        ))
    return out


# 公共 W 底结构（看涨）：左底/颈线/右底，neckline≈101
# 前置 20 根温和下行 + W 形态（24 根）
_LEAD = [120 - i * 0.6 for i in range(20)]
_W = [95, 94, 93, 92, 91,
      88,
      90, 92, 94, 96, 98,
      100,
      97, 94, 91, 89.5, 89, 88.6,
      88,
      89, 91, 94, 97, 99]


def _gen_signals(prices):
    klines = make_klines(prices)
    regime = {k.date: MarketRegime.RANGE for k in klines}
    gen = CalibrationSignalGenerator()
    return gen.generate_signals(klines, "000001", regime)


# ---------------------------------------------------------------------------
# _EMIT_STAGES 契约
# ---------------------------------------------------------------------------

class TestEmitStagesContract:
    def test_emit_stages_contains_four_tradable_states(self):
        expected = {PatternStage.BREAKOUT_DAY, PatternStage.POST_BREAK,
                    PatternStage.RETEST, PatternStage.FAILED_BREAK}
        assert _EMIT_STAGES == expected

    def test_emit_stages_excludes_pre_break_and_forming(self):
        assert PatternStage.PRE_BREAK not in _EMIT_STAGES
        assert PatternStage.FORMING not in _EMIT_STAGES

    def test_stage_to_confirmation_mapping_complete(self):
        """所有需发出的 stage 都能映射到 ConfirmationState"""
        for stage in _EMIT_STAGES:
            assert stage in _STAGE_TO_CONFIRMATION
            assert _STAGE_TO_CONFIRMATION[stage] in ConfirmationState


# ---------------------------------------------------------------------------
# RETEST：突破后回踩颈线
# ---------------------------------------------------------------------------

class TestRetestEmission:
    def _prices(self):
        # 突破(103) → 一根回踩颈线带(low≈101) → 收回 → 横盘
        retest_tail = [103, 101, 103, 104, 105]
        flat = [105.0] * 30
        return _LEAD + _W + retest_tail + flat

    def test_retest_signal_emitted(self):
        sigs = _gen_signals(self._prices())
        states = {s.confirmation_state for s in sigs}
        assert ConfirmationState.RETEST in states, \
            f"应发出 RETEST 信号，实际 states={states}"

    def test_retest_is_w_bottom(self):
        sigs = [s for s in _gen_signals(self._prices())
                if s.confirmation_state == ConfirmationState.RETEST]
        assert sigs
        for s in sigs:
            assert s.pattern_type.value == "w_bottom"
            # 回踩后收盘仍在颈线上方
            assert s.entry_price >= s.neckline_price


# ---------------------------------------------------------------------------
# FAILED_BREAK：突破后连续 ≥3 日跌回颈线下
# ---------------------------------------------------------------------------

class TestFailedBreakEmission:
    def _prices(self):
        # 突破(103) → 连续 3 根收盘略低于颈线(101) → 横盘在颈线下方附近
        fail_tail = [103, 100.5, 100.2, 99.9]
        flat = [100.0] * 35
        return _LEAD + _W + fail_tail + flat

    def test_failed_break_signal_emitted(self):
        sigs = _gen_signals(self._prices())
        states = {s.confirmation_state for s in sigs}
        assert ConfirmationState.FAILED_BREAK in states, \
            f"应发出 FAILED_BREAK 信号，实际 states={states}"


# ---------------------------------------------------------------------------
# PRE_BREAK / FORMING 不被发出
# ---------------------------------------------------------------------------

class TestPreBreakNotEmitted:
    def test_unbroken_neckline_emits_no_breakout_signal(self):
        """W 底已成形但未突破颈线（stage=PRE_BREAK/FORMING）→ 不发出信号"""
        # 突破段缺失：右底之后只回升到 99（未过颈线 101*1.01=102.01），横盘
        no_breakout_tail = [99.0] * 40
        prices = _LEAD + _W + no_breakout_tail
        sigs = _gen_signals(prices)
        states = {s.confirmation_state for s in sigs}
        # 不得发出任何突破后/回踩/失败信号；尤其不能有 PRE_BREAK
        assert ConfirmationState.PRE_BREAK not in states
        # 内联 MA/platform 也不应在这个横盘序列里触发（保险起见，至少断言无 w_bottom 突破信号）
        assert not any(s.pattern_type.value == "w_bottom" for s in sigs), \
            "未突破颈线不应发出 w_bottom 信号"
