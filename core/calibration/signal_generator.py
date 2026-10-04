"""
FIR-13: Empirical Calibration — 校准信号生成层

对每只股票的历史数据逐日生成形态信号，严格 point-in-time 防 look-ahead。

注意：PR #36 (PivotPatternDetector) 未合并，此处使用 V4 pattern_scanner.py
的形态逻辑，并改造成 point-in-time 版本：对每个检测日 i 只使用 klines[:i+1]。
legacy pattern detector, will migrate after PR #36 merge.

核心防 look-ahead 约束：
- 均线计算到 i 为止（含 i）：当天收盘后均线已知
- high_20 / low_20 使用 shift(1) 语义，即 klines[i-20:i]，**不含**当天 i，
  否则"当天突破前高"将永远成立（自引用）
- W底 / M头 / 头肩 形态使用过去 60 天窗口 klines[i-60:i+1]，不含未来
"""

from __future__ import annotations

from typing import List, Optional, Dict

from core.types import OHLCV
from core.calibration.types import *  # noqa: F401,F403 (PatternType, ConfirmationState, MarketRegime, SignalOccurrence, make_signal_id)


# 形态回看窗口（交易日）
PATTERN_WINDOW = 60
# 颈线突破容差（1% / 1%）
BREAKOUT_TOL = 1.01
BREAKDOWN_TOL = 0.99
# W底/M头两底/顶高度容差
DOUBLE_TOL = 0.05
# 头肩左右肩高度差容差
HS_SHOULDER_TOL = 0.05
# 两底/顶最小间距（交易日）
DOUBLE_MIN_GAP = 10


class CalibrationSignalGenerator:
    """校准信号生成器 — 逐日生成形态信号，严格防 look-ahead"""

    # 最少需要的历史K线数：60 日窗口 + MA60
    MIN_START = PATTERN_WINDOW

    # 形态评分：均线类 70，突破类 80，双底/双顶/头肩类 75
    SCORE = {
        PatternType.MA_BULL: 70,
        PatternType.MA_BEAR: 70,
        PatternType.PLATFORM_BREAK: 80,
        PatternType.PLATFORM_BREAK_DOWN: 80,
        PatternType.W_BOTTOM: 75,
        PatternType.M_TOP: 75,
        PatternType.HS_BOTTOM: 75,
        PatternType.HS_TOP: 75,
    }

    # ==================================================================
    # 对外入口
    # ==================================================================
    def generate_signals(
        self,
        klines: List[OHLCV],
        stock_code: str,
        market_regime_by_date: Dict[str, MarketRegime],
    ) -> List[SignalOccurrence]:
        """对一只股票的历史数据逐日生成信号

        对每个交易日 i（从 MIN_START 开始），使用 klines[:i+1] 判断看涨/看跌形态。
        信号只能使用当天及之前的数据。
        """
        signals: List[SignalOccurrence] = []
        n = len(klines)

        for i in range(self.MIN_START, n):
            date = klines[i].date
            close = klines[i].close

            detections = [
                (PatternType.MA_BULL, self._detect_ma_bull(klines, i)),
                (PatternType.PLATFORM_BREAK, self._detect_platform_break(klines, i)),
                (PatternType.W_BOTTOM, self._detect_w_bottom(klines, i)),
                (PatternType.HS_BOTTOM, self._detect_hs_bottom(klines, i)),
                (PatternType.MA_BEAR, self._detect_ma_bear(klines, i)),
                (PatternType.PLATFORM_BREAK_DOWN, self._detect_platform_break_down(klines, i)),
                (PatternType.M_TOP, self._detect_m_top(klines, i)),
                (PatternType.HS_TOP, self._detect_hs_top(klines, i)),
            ]

            for ptype, detail in detections:
                if detail is None:
                    continue
                regime = market_regime_by_date.get(date, MarketRegime.RANGE)
                signals.append(SignalOccurrence(
                    signal_id=make_signal_id(stock_code, date, ptype.value, 0),
                    stock_code=stock_code,
                    signal_date=date,
                    pattern_type=ptype,
                    entry_price=close,
                    confirmation_state=ConfirmationState.BREAKOUT_DAY,
                    market_regime=regime,
                    pattern_score=self.SCORE[ptype],
                    breakout_price=detail.get("breakout_price"),
                    neckline_price=detail.get("neckline_price"),
                ))
        return signals

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
    # 看涨形态
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

    def _detect_w_bottom(self, klines: List[OHLCV], i: int) -> Optional[dict]:
        """W底：过去60日两个低点，第二底>=第一底97%，间距>=10，收盘突破颈线*1.01。"""
        lo = i - PATTERN_WINDOW
        if lo < 0:
            return None
        seg = klines[lo:i + 1]
        m = len(seg)
        lows = [k.low for k in seg]
        highs = [k.high for k in seg]

        first_rel = min(range(m), key=lambda x: lows[x])
        # 第一底需留两侧确认边距
        if first_rel < 5 or first_rel > m - 10:
            return None
        first_low = lows[first_rel]

        after = lows[first_rel + 5:]
        if len(after) < 5:
            return None
        second_rel = first_rel + 5 + min(range(len(after)), key=lambda x: after[x])
        second_low = lows[second_rel]

        # 第二底不创新低（>=第一底97%），且两底高度差<5%
        if second_low < first_low * 0.97:
            return None
        if first_low > 0 and abs(second_low - first_low) / first_low > DOUBLE_TOL:
            return None
        if second_rel - first_rel < DOUBLE_MIN_GAP:
            return None

        neckline = max(highs[first_rel:second_rel + 1])
        cur_close = seg[-1].close
        if cur_close > neckline * BREAKOUT_TOL:
            return {"breakout_price": cur_close, "neckline_price": neckline}
        return None

    def _detect_hs_bottom(self, klines: List[OHLCV], i: int) -> Optional[dict]:
        """头肩底（简化）：三个低点（中间最低=头），左右肩差<5%，收盘突破颈线*1.01。"""
        lo = i - PATTERN_WINDOW
        if lo < 0:
            return None
        seg = klines[lo:i + 1]
        m = len(seg)
        lows = [k.low for k in seg]
        highs = [k.high for k in seg]

        # "头" = 全局最低点，须位于中段
        head_rel = min(range(m), key=lambda x: lows[x])
        if head_rel < m // 3 or head_rel > m - m // 3:
            return None
        head_low = lows[head_rel]

        # 左肩 = 头左侧最低点；右肩 = 头右侧最低点
        left_rel = min(range(0, head_rel), key=lambda x: lows[x]) if head_rel > 0 else -1
        right_rel = head_rel + 1 + min(range(m - head_rel - 1), key=lambda x: lows[head_rel + 1 + x]) \
            if head_rel < m - 1 else -1
        if left_rel < 0 or right_rel < 0:
            return None
        left_low, right_low = lows[left_rel], lows[right_rel]

        # 中间最低
        if not (head_low < left_low and head_low < right_low):
            return None
        # 左右肩高度差 < 5%
        denom = max(left_low, right_low)
        if denom <= 0 or abs(left_low - right_low) / denom > HS_SHOULDER_TOL:
            return None
        if right_rel - left_rel < DOUBLE_MIN_GAP:
            return None

        neckline = max(highs[left_rel:right_rel + 1])
        cur_close = seg[-1].close
        if cur_close > neckline * BREAKOUT_TOL:
            return {"breakout_price": cur_close, "neckline_price": neckline}
        return None

    # ==================================================================
    # 看跌形态
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

    def _detect_m_top(self, klines: List[OHLCV], i: int) -> Optional[dict]:
        """M头：过去60日两个高点，第二顶<=第一顶103%，间距>=10，收盘跌破颈线*0.99。"""
        lo = i - PATTERN_WINDOW
        if lo < 0:
            return None
        seg = klines[lo:i + 1]
        m = len(seg)
        highs = [k.high for k in seg]
        lows = [k.low for k in seg]

        first_rel = max(range(m), key=lambda x: highs[x])
        if first_rel < 5 or first_rel > m - 10:
            return None
        first_high = highs[first_rel]

        after = highs[first_rel + 5:]
        if len(after) < 5:
            return None
        second_rel = first_rel + 5 + max(range(len(after)), key=lambda x: after[x])
        second_high = highs[second_rel]

        # 第二顶不高于第一顶 103%，且两顶高度差 < 5%
        if second_high > first_high * 1.03:
            return None
        if first_high > 0 and abs(second_high - first_high) / first_high > DOUBLE_TOL:
            return None
        if second_rel - first_rel < DOUBLE_MIN_GAP:
            return None

        neckline = min(lows[first_rel:second_rel + 1])
        cur_close = seg[-1].close
        if cur_close < neckline * BREAKDOWN_TOL:
            return {"breakout_price": cur_close, "neckline_price": neckline}
        return None

    def _detect_hs_top(self, klines: List[OHLCV], i: int) -> Optional[dict]:
        """头肩顶（简化）：三个高点（中间最高=头），左右肩差<5%，收盘跌破颈线*0.99。"""
        lo = i - PATTERN_WINDOW
        if lo < 0:
            return None
        seg = klines[lo:i + 1]
        m = len(seg)
        highs = [k.high for k in seg]
        lows = [k.low for k in seg]

        head_rel = max(range(m), key=lambda x: highs[x])
        if head_rel < m // 3 or head_rel > m - m // 3:
            return None
        head_high = highs[head_rel]

        left_rel = max(range(0, head_rel), key=lambda x: highs[x]) if head_rel > 0 else -1
        right_rel = head_rel + 1 + max(range(m - head_rel - 1), key=lambda x: highs[head_rel + 1 + x]) \
            if head_rel < m - 1 else -1
        if left_rel < 0 or right_rel < 0:
            return None
        left_high, right_high = highs[left_rel], highs[right_rel]

        if not (head_high > left_high and head_high > right_high):
            return None
        denom = max(left_high, right_high)
        if denom <= 0 or abs(left_high - right_high) / denom > HS_SHOULDER_TOL:
            return None
        if right_rel - left_rel < DOUBLE_MIN_GAP:
            return None

        neckline = min(lows[left_rel:right_rel + 1])
        cur_close = seg[-1].close
        if cur_close < neckline * BREAKDOWN_TOL:
            return {"breakout_price": cur_close, "neckline_price": neckline}
        return None
