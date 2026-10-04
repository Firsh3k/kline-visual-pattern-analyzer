"""
FIR-13/FIR-24: Empirical Calibration — 校准信号生成层

对每只股票的历史数据逐日生成形态信号，严格 point-in-time 防 look-ahead。

实现说明（FIR-24）：
- W底 / M头 / 头肩底 / 头肩顶 已从 V4 legacy 内联检测迁移到
  `core.pivot_patterns.py` 的 canonical detectors（FIR-12）。
- MA 多头/空头、平台突破/破位仍使用本层内联逻辑（canonical detector 未覆盖）。

核心防 look-ahead 约束：
- 对每个检测日 i 只取 prefix `klines[:i+1]`，再交给 canonical detector。
  detector 内部只使用已确认 pivot（confirmed_at_index <= i），杜绝未来函数。
- 均线计算到 i 为止（含 i）；high_20 / low_20 使用 shift(1) 语义（不含当天 i）。

Confirmation-state contract（P1 / FIR-24）：
- canonical detector（core.pivot_patterns）在任意 prefix 上可输出
  PRE_BREAK / FORMING / BREAKOUT_DAY / POST_BREAK / RETEST / FAILED_BREAK
  六种 PatternStage。
- 校准信号**只**发出其中四种可交易状态：
    BREAKOUT_DAY（突破日）/ POST_BREAK（突破后）/
    RETEST（突破后回踩颈线）/ FAILED_BREAK（假突破）。
- PRE_BREAK（接近颈线但未突破）与 FORMING（形态未确认）被 `_EMIT_STAGES`
  过滤掉——它们是突破前状态，不构成交易信号。
"""

from __future__ import annotations

from typing import List, Optional, Dict

from core.types import OHLCV, PatternStage
from core.pivot_patterns import (
    WBottomDetector,
    MTopDetector,
    HSBottomDetector,
    HSTopDetector,
)
from core.calibration.types import *  # noqa: F401,F403 (PatternType, ConfirmationState, MarketRegime, SignalOccurrence, make_signal_id)


# 形态回看起点（交易日）：保留概念；canonical detector 内部用 pivot 引擎，不显式窗口
PATTERN_WINDOW = 60

# 内联 MA/platform 形态评分
SCORE = {
    PatternType.MA_BULL: 70,
    PatternType.MA_BEAR: 70,
    PatternType.PLATFORM_BREAK: 80,
    PatternType.PLATFORM_BREAK_DOWN: 80,
}

# canonical detector pattern_id -> PatternType
_PATTERN_ID_MAP = {
    "w_bottom": PatternType.W_BOTTOM,
    "m_top": PatternType.M_TOP,
    "hs_bottom": PatternType.HS_BOTTOM,
    "hs_top": PatternType.HS_TOP,
}

# PatternStage -> ConfirmationState 映射
_STAGE_TO_CONFIRMATION = {
    PatternStage.BREAKOUT_DAY: ConfirmationState.BREAKOUT_DAY,
    PatternStage.POST_BREAK: ConfirmationState.POST_BREAK,
    PatternStage.RETEST: ConfirmationState.RETEST,
    PatternStage.FAILED_BREAK: ConfirmationState.FAILED_BREAK,
    PatternStage.PRE_BREAK: ConfirmationState.PRE_BREAK,
}

# 只对已突破/跌破（及其后续回踩/失败）的形态产生校准信号。
# 不含 PRE_BREAK / FORMING —— 它们是突破前状态，不构成可交易信号（P1）。
_EMIT_STAGES = frozenset({
    PatternStage.BREAKOUT_DAY,
    PatternStage.POST_BREAK,
    PatternStage.RETEST,
    PatternStage.FAILED_BREAK,
})


class CalibrationSignalGenerator:
    """校准信号生成器 — 逐日生成形态信号，严格防 look-ahead"""

    # 最少需要的历史K线数：60 日窗口 + MA60
    MIN_START = PATTERN_WINDOW

    def __init__(self) -> None:
        # canonical detectors（FIR-12）
        self._w_detector = WBottomDetector()
        self._m_detector = MTopDetector()
        self._hs_bottom_detector = HSBottomDetector()
        self._hs_top_detector = HSTopDetector()

    # ==================================================================
    # 对外入口
    # ==================================================================
    def generate_signals(
        self,
        klines: List[OHLCV],
        stock_code: str,
        market_regime_by_date: Dict[str, MarketRegime],
        data_source: str = "multi_source",
        adjustment: str = "qfq",
        data_quality: str = "OK",
        provenance: Optional[dict] = None,
    ) -> List[SignalOccurrence]:
        """对一只股票的历史数据逐日生成信号

        对每个交易日 i（从 MIN_START 开始），使用 klines[:i+1] 判断看涨/看跌形态。
        信号只能使用当天及之前的数据。

        Provenance 优先级（P0-2）：
            provenance dict（来自 MarketDataSeries 真实提取）
              > 显式参数 data_source/adjustment/data_quality
              > 默认值。
        传入 provenance 时不再硬编码 multi_source/qfq。
        """
        # ---- 解析真实 provenance（dict > 显式参数 > 默认值）----
        if provenance:
            data_source = provenance.get("data_source", data_source)
            adjustment = provenance.get("adjustment", adjustment)
            data_quality = provenance.get("data_quality", data_quality)

        signals: List[SignalOccurrence] = []
        n = len(klines)

        for i in range(self.MIN_START, n):
            date = klines[i].date
            close = klines[i].close
            regime = market_regime_by_date.get(date, MarketRegime.RANGE)

            # ---- 1) 内联 MA/platform 形态（canonical detector 未覆盖，逻辑不变）----
            inline = [
                (PatternType.MA_BULL, self._detect_ma_bull(klines, i)),
                (PatternType.PLATFORM_BREAK, self._detect_platform_break(klines, i)),
                (PatternType.MA_BEAR, self._detect_ma_bear(klines, i)),
                (PatternType.PLATFORM_BREAK_DOWN, self._detect_platform_break_down(klines, i)),
            ]
            for ptype, detail in inline:
                if detail is None:
                    continue
                sig = SignalOccurrence(
                    signal_id=make_signal_id(stock_code, date, ptype.value, 0),
                    stock_code=stock_code,
                    signal_date=date,
                    pattern_type=ptype,
                    entry_price=close,
                    confirmation_state=ConfirmationState.BREAKOUT_DAY,
                    market_regime=regime,
                    pattern_score=SCORE[ptype],
                    breakout_price=detail.get("breakout_price"),
                    neckline_price=detail.get("neckline_price"),
                )
                self._attach_provenance(sig, detector_version="builtin_v1",
                                        data_source=data_source, adjustment=adjustment,
                                        data_quality=data_quality)
                signals.append(sig)

            # ---- 2) canonical W/M/H&S detectors（point-in-time: 只用 prefix）----
            prefix = klines[:i + 1]
            for detector in (self._w_detector, self._m_detector,
                            self._hs_bottom_detector, self._hs_top_detector):
                results = detector.detect(prefix)
                for idx, res in enumerate(results):
                    if res.stage not in _EMIT_STAGES:
                        continue
                    ptype = _PATTERN_ID_MAP[res.pattern_id]
                    sig = SignalOccurrence(
                        signal_id=make_signal_id(stock_code, date, ptype.value, idx),
                        stock_code=stock_code,
                        signal_date=date,
                        pattern_type=ptype,
                        entry_price=close,
                        confirmation_state=_STAGE_TO_CONFIRMATION[res.stage],
                        market_regime=regime,
                        pattern_score=res.score,
                        breakout_price=res.breakout_price,
                        neckline_price=res.neckline_price,
                        notes=res.notes,
                    )
                    self._attach_provenance(sig, detector_version="canonical_v5.2.0",
                                            data_source=data_source, adjustment=adjustment,
                                            data_quality=data_quality,
                                            pattern_score=res.score, result=res)
                    signals.append(sig)

        # ---- 3) 方向一致性检查 + 事件去重 ----
        signals = self._filter_directional_consistency(signals)
        signals = self._deduplicate_signals(signals)
        return signals

    # ==================================================================
    # Provenance / quality manifest（Phase 5）
    # ==================================================================
    @staticmethod
    def _pattern_confidence(score: int) -> str:
        if score >= 80:
            return "high"
        if score >= 50:
            return "medium"
        return "low"

    def _attach_provenance(
        self,
        sig: SignalOccurrence,
        detector_version: str,
        data_source: str,
        adjustment: str,
        data_quality: str,
        pattern_score: Optional[int] = None,
        result=None,
    ) -> None:
        """填充 provenance/quality 元数据字段。"""
        sig.data_source = data_source
        sig.adjustment = adjustment
        sig.data_quality = data_quality
        sig.detector_version = detector_version
        sig.pattern_confidence = self._pattern_confidence(
            pattern_score if pattern_score is not None else sig.pattern_score)
        # H&S borderline 提示
        if result is not None and result.pattern_id in ("hs_bottom", "hs_top"):
            head = next((kp for kp in result.key_points if kp.name == "头"), None)
            ls = next((kp for kp in result.key_points if kp.name == "左肩"), None)
            rs = next((kp for kp in result.key_points if kp.name == "右肩"), None)
            if head is not None and ls is not None and rs is not None:
                avg_sh = (ls.price + rs.price) / 2.0
                if avg_sh > 0:
                    prom = abs(head.price - avg_sh) / avg_sh
                    from core.pivot_patterns import MIN_HS_HEAD_PROMINENCE
                    if prom < MIN_HS_HEAD_PROMINENCE * 1.2:
                        sig.guardrail_flags.append("head_prominence_borderline")

    # ==================================================================
    # 方向一致性（Phase 3）
    # ==================================================================
    def _filter_directional_consistency(self, signals: List[SignalOccurrence]) -> List[SignalOccurrence]:
        """确保上升形态只在价格站上关键位、下降形态只在跌破关键位时触发。"""
        kept: List[SignalOccurrence] = []
        for s in signals:
            nl = s.neckline_price
            pt = s.pattern_type
            if pt.is_bullish:
                if nl is None:
                    kept.append(s)
                    continue
                if pt in (PatternType.W_BOTTOM, PatternType.HS_BOTTOM):
                    ok = s.entry_price >= nl * 0.98
                else:  # platform_break / ma_bull: neckline = 前20日高点
                    ok = s.entry_price >= nl
                if ok:
                    kept.append(s)
            elif pt.is_bearish:
                if nl is None:
                    kept.append(s)
                    continue
                if pt in (PatternType.M_TOP, PatternType.HS_TOP):
                    ok = s.entry_price <= nl * 1.02
                else:  # platform_break_down / ma_bear: neckline = 前20日低点
                    ok = s.entry_price <= nl
                if ok:
                    kept.append(s)
            else:
                kept.append(s)
        return kept

    # ==================================================================
    # 事件去重（Phase 4）
    # ==================================================================
    @staticmethod
    def _deduplicate_signals(signals: List[SignalOccurrence]) -> List[SignalOccurrence]:
        """同一 (stock, pattern_type) 在 DEDUP_WINDOW 自然日内只保留最早信号。"""
        from datetime import date as _date
        DEDUP_WINDOW = 20
        # 按 (stock, pattern_type) 分组，组内按 signal_date 排序
        groups: Dict[tuple, List[SignalOccurrence]] = {}
        for s in signals:
            key = (s.stock_code, s.pattern_type)
            groups.setdefault(key, []).append(s)

        kept: List[SignalOccurrence] = []
        for grp in groups.values():
            grp.sort(key=lambda s: s.signal_date)
            last_date: Optional[_date] = None
            for s in grp:
                d = _date.fromisoformat(s.signal_date)
                if last_date is not None and (d - last_date).days <= DEDUP_WINDOW:
                    continue
                kept.append(s)
                last_date = d
        # 保持原始时间顺序输出
        kept.sort(key=lambda s: (s.signal_date, s.stock_code, s.pattern_type.value))
        return kept

    # ==================================================================
    # 基础指标辅助（全部 point-in-time：只用到 end_idx 及之前）
    # ==================================================================
    @staticmethod
    def _ma(klines: List[OHLCV], period: int, end_idx: int) -> Optional[float]:
        """到 end_idx（含）为止的 period 日均线收盘价均值。数据不足返回 None。"""
        start = end_idx - period + 1
        if start < 0:
            return None
        window = klines[start:end_idx + 1]
        if len(window) < period:
            return None
        return sum(k.close for k in window) / period

    @staticmethod
    def _high_n(klines: List[OHLCV], n: int, end_idx: int) -> Optional[float]:
        """end_idx 之前 n 天的最高价（**不含** end_idx，shift(1) 语义）。"""
        start = end_idx - n
        if start < 0:
            return None
        window = klines[start:end_idx]  # [end_idx-n, end_idx)
        if len(window) < n:
            return None
        return max(k.high for k in window)

    @staticmethod
    def _low_n(klines: List[OHLCV], n: int, end_idx: int) -> Optional[float]:
        """end_idx 之前 n 天的最低价（**不含** end_idx，shift(1) 语义）。"""
        start = end_idx - n
        if start < 0:
            return None
        window = klines[start:end_idx]
        if len(window) < n:
            return None
        return min(k.low for k in window)

    @staticmethod
    def _vol_ma(klines: List[OHLCV], period: int, end_idx: int) -> Optional[float]:
        """到 end_idx（含）为止的 period 日均量。"""
        start = end_idx - period + 1
        if start < 0:
            return None
        window = klines[start:end_idx + 1]
        if len(window) < period:
            return None
        return sum(k.volume for k in window) / period

    # ==================================================================
    # 看涨形态（内联）
    # ==================================================================
    def _detect_ma_bull(self, klines: List[OHLCV], i: int) -> Optional[dict]:
        """均线多头突破：MA5>MA10>MA20>MA60，收盘>前20日高点，当天收阳。"""
        ma5 = self._ma(klines, 5, i)
        ma10 = self._ma(klines, 10, i)
        ma20 = self._ma(klines, 20, i)
        ma60 = self._ma(klines, 60, i)
        if None in (ma5, ma10, ma20, ma60):
            return None
        if not (ma5 > ma10 > ma20 > ma60):
            return None
        high20 = self._high_n(klines, 20, i)
        if high20 is None:
            return None
        bar = klines[i]
        if bar.close > high20 and bar.close > bar.open:
            return {"breakout_price": bar.close, "neckline_price": high20}
        return None

    def _detect_platform_break(self, klines: List[OHLCV], i: int) -> Optional[dict]:
        """平台突破：收盘>前20日高点，量>5日均量*1.5，当天涨幅>3%。"""
        if i < 1:
            return None
        high20 = self._high_n(klines, 20, i)
        vma5 = self._vol_ma(klines, 5, i)
        if high20 is None or vma5 is None or vma5 <= 0:
            return None
        bar = klines[i]
        prev_close = klines[i - 1].close
        if prev_close <= 0:
            return None
        pct = (bar.close - prev_close) / prev_close
        if bar.close > high20 and bar.volume > vma5 * 1.5 and pct > 0.03:
            return {"breakout_price": bar.close, "neckline_price": high20}
        return None

    # ==================================================================
    # 看跌形态（内联）
    # ==================================================================
    def _detect_ma_bear(self, klines: List[OHLCV], i: int) -> Optional[dict]:
        """均线空头破位：MA5<MA10<MA20<MA60，收盘<前20日低点，当天收阴。"""
        ma5 = self._ma(klines, 5, i)
        ma10 = self._ma(klines, 10, i)
        ma20 = self._ma(klines, 20, i)
        ma60 = self._ma(klines, 60, i)
        if None in (ma5, ma10, ma20, ma60):
            return None
        if not (ma5 < ma10 < ma20 < ma60):
            return None
        low20 = self._low_n(klines, 20, i)
        if low20 is None:
            return None
        bar = klines[i]
        if bar.close < low20 and bar.close < bar.open:
            return {"breakout_price": bar.close, "neckline_price": low20}
        return None

    def _detect_platform_break_down(self, klines: List[OHLCV], i: int) -> Optional[dict]:
        """平台破位：收盘<前20日低点，量>5日均量*1.2，当天跌幅>3%。"""
        if i < 1:
            return None
        low20 = self._low_n(klines, 20, i)
        vma5 = self._vol_ma(klines, 5, i)
        if low20 is None or vma5 is None or vma5 <= 0:
            return None
        bar = klines[i]
        prev_close = klines[i - 1].close
        if prev_close <= 0:
            return None
        pct = (bar.close - prev_close) / prev_close
        if bar.close < low20 and bar.volume > vma5 * 1.2 and pct < -0.03:
            return {"breakout_price": bar.close, "neckline_price": low20}
        return None
