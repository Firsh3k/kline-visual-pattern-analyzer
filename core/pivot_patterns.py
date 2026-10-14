"""
FIR-12 Pattern Migration — 基于 SwingPivotEngine 的经典形态识别器

四个经典反转形态的 V5 canonical 实现：

- WBottomDetector   W底/双底   (pattern_id="w_bottom",  方向 BULL)
- MTopDetector      M头/双头   (pattern_id="m_top",      方向 BEAR)
- HSBottomDetector  头肩底     (pattern_id="hs_bottom",  方向 BULL，支持倾斜颈线)
- HSTopDetector     头肩顶     (pattern_id="hs_top",      方向 BEAR，支持倾斜颈线)

设计要点（与 V4 的区别）：
1. **只使用已确认的 pivot**（detect_confirmed_only），pending pivot 不参与形态构建，
   从数据源头上杜绝未来函数。
2. **颈线**：W/M 使用水平颈线（两底/顶之间的摆动极值）；H&S 使用左、右两个颈线
   点连线，支持向上/向下倾斜的动态颈线。
3. **突破状态机**（四个形态共用，由基类实现）：
   FORMING → PRE_BREAK → BREAKOUT_DAY → POST_BREAK / RETEST / FAILED_BREAK
4. **评分 0-100**：结构分 + 突破分 + 量能分（H&S 为结构三项 + 突破分），
   FAILED_BREAK 扣 30 分（最低 10 分）。
5. 所有 detector 通过 @registry.pattern 注册到全局注册中心。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .types import (
    OHLCV,
    PivotPoint,
    PatternResult,
    PatternKeyPoint,
    TrendDirection,
    PatternStage,
    ConfidenceLevel,
    PATTERN_CATEGORY_PRICE,
)
from .interfaces import PatternDetector
from .swing_pivot import SwingPivotEngine
from .registry import registry


# ============================================================
# FIR-24: H&S Guardrails（证据驱动阈值）
# ============================================================
#
# 阈值来源：scripts/fir24_hs_prominence_analysis.py 在全部缓存 qfq 数据
# （data/calibration/*_qfq.json，约 74 只股票；统计时临时中和本 guardrail，
# 收集到所有通过既有结构闸门的候选，n=738 个 nl_now>0 的 H&S 候选）上统计：
#
#   head_prominence 分布（|head - avg_shoulder|/avg_shoulder，avg_shoulder=(ls+rs)/2）：
#     P10=1.47%  P25=2.71%  P50=5.29%  P75=8.71%  P90=15.01%
#     mean=6.94%  std=6.03%  min=0.33%  max=47.47%
#   按任务规则「max(P25, median/2, floor 3%)」= max(2.71%, 2.65%, 3%) = 3%。
#   该阈值拒绝「平头」候选（如 002415 某候选 prominence=2.72%），即头部与双肩
#   几乎齐平、根本不是 H&S 而是普通双底/震荡的结构。
#
#   颈线外推比率分布（bottom: nl_now/head；top: head/nl_now，n=738，nl_now>0）：
#     P50=1.12  P75=1.93  P90=3.37  P95=5.74  P99=12.55  max=69.9
#   标准健康 fixture 的外推比率 ≈ 1.19（颈线仅比头部高 ~19%）。
#   倾斜颈线一旦斜率较陡，外推到当前 bar 会漂移到远离结构的无意义水平
#   （如 002415 弱候选 nl_now/head=1.82，外推颈线 53.04 远高于现价 32.46；
#   退化候选甚至到 10.8x / 69.9x）。取 1.5：颈线最多比头部高/低 50%，
#   超出即拒绝——健康结构（~1.1-1.3）不受影响。
MIN_HS_HEAD_PROMINENCE: float = 0.03
MAX_NECKLINE_EXTRAPOLATION_RATIO: float = 1.5


def _avg_volume(klines: List[OHLCV], window: int) -> float:
    """计算最近 window 根K线的平均成交量（数据不足时用全部已有数据）"""
    if not klines:
        return 0.0
    seg = klines[-window:] if window < len(klines) else klines
    return sum(k.volume for k in seg) / len(seg)


class PivotPatternDetectorBase(PatternDetector):
    """
    形态识别器基类 — 封装所有四个形态共用的逻辑：

    - pivot 获取（默认内部用 SwingPivotEngine(left=5, right=5) 只取已确认 pivot）
    - 量能确认判断（最近5日均量 > 20日均量 * 1.1）
    - 突破/跌破状态机
    - 置信度映射
    """

    #: pivot 引擎左右窗口参数（子类可覆盖）
    left_bars: int = 5
    right_bars: int = 5

    # ---------- pivot 获取 ----------

    def _get_confirmed_pivots(
        self,
        klines: List[OHLCV],
        pivots: Optional[List[PivotPoint]],
    ) -> List[PivotPoint]:
        """
        获取已确认 pivot。

        - pivots 为 None 时：内部用 SwingPivotEngine(left=5, right=5).detect_confirmed_only()
        - pivots 传入时：过滤出 confirmed=True 的 pivot（pending 不参与形态识别，
          保证与内部计算一致，避免未来函数）
        """
        if pivots is None:
            engine = SwingPivotEngine(left_bars=self.left_bars, right_bars=self.right_bars)
            return engine.detect_confirmed_only(klines)

        n = len(klines)
        result: List[PivotPoint] = []
        for p in pivots:
            # 1) 必须是已确认 pivot（pending 不参与形态识别）
            if not p.confirmed:
                continue
            # 2) 必须携带 point-in-time 确认元数据
            if p.confirmed_at_index is None:
                continue
            # 3) 确认时点不能晚于当前 prefix 的最后一根K线 —— 否则引入未来函数
            if p.confirmed_at_index > n - 1:
                continue
            # 4) pivot index/date 必须属于当前 series/prefix，防止跨 series 错位
            if p.index < 0 or p.index >= n:
                continue
            if p.date != klines[p.index].date:
                continue
            result.append(p)
        return result

    # ---------- 量能 ----------

    def _volume_confirmed(self, klines: List[OHLCV]) -> bool:
        """量能确认：最近5日均量 > 最近20日均量 * 1.1"""
        v20 = _avg_volume(klines, 20)
        v5 = _avg_volume(klines, 5)
        if v20 <= 0:
            return False
        return v5 > v20 * 1.1

    # ---------- 置信度 ----------

    @staticmethod
    def _confidence(score: int) -> ConfidenceLevel:
        """评分 → 置信度映射：≥80 S / ≥65 A / ≥50 B / ≥35 C / <35 D"""
        if score >= 80:
            return ConfidenceLevel.S
        if score >= 65:
            return ConfidenceLevel.A
        if score >= 50:
            return ConfidenceLevel.B
        if score >= 35:
            return ConfidenceLevel.C
        return ConfidenceLevel.D

    # ---------- 颈线突破状态机 ----------

    def _classify_stage(
        self,
        klines: List[OHLCV],
        shape_done_idx: int,
        neckline_fn: Callable[[int], float],
        bullish: bool,
        breakout_mult: float,
    ) -> PatternStage:
        """通用突破/跌破状态机（只返回阶段；详细状态见 _breakout_state）"""
        return self._breakout_state(klines, shape_done_idx, neckline_fn, bullish, breakout_mult)["stage"]

    def _breakout_state(
        self,
        klines: List[OHLCV],
        shape_done_idx: int,
        neckline_fn: Callable[[int], float],
        bullish: bool,
        breakout_mult: float,
    ) -> Dict[str, Any]:
        """
        通用突破/跌破状态机。

        Args:
            klines: K线列表
            shape_done_idx: 形态结构完成的最后一个结构点索引（如 W底的右底、
                            H&S的右肩），只在这之后判断突破
            neckline_fn: 给定K线索引，返回该索引处的颈线值
                         （水平颈线为常数函数；倾斜颈线为斜率插值函数）
            bullish: True=看涨形态（向上突破颈线）；False=看跌形态（向下跌破颈线）
            breakout_mult: 突破确认倍数
                           （看涨：收盘 > 颈线 * mult；看跌：收盘 < 颈线 * mult）

        Returns:
            {"stage": PatternStage, "cross_idx": 突破日索引或 None,
             "days_back_inside": 突破后、截至今日连续收盘回到颈线另一侧的天数（未突破为 None）}
            FIR-36：阶段语义不变（≥3 日才判 failed_break），1–2 日的回落由 days_back_inside 标注。
        """
        n = len(klines)
        last = n - 1
        closes = [k.close for k in klines]
        nl_last = neckline_fn(last)

        # 1) 寻找突破/跌破日：前一日收盘在颈线正确一侧，当日收盘越过颈线 * mult
        cross_idx: Optional[int] = None
        for i in range(shape_done_idx + 1, n):
            nl_i = neckline_fn(i)
            nl_prev = neckline_fn(i - 1)
            if bullish:
                crossed = closes[i] > nl_i * breakout_mult and closes[i - 1] <= nl_prev
            else:
                crossed = closes[i] < nl_i * breakout_mult and closes[i - 1] >= nl_prev
            if crossed:
                cross_idx = i
                break

        # 2) 尚未突破：价格接近颈线 ±3% → PRE_BREAK，否则 FORMING
        if cross_idx is None:
            if nl_last > 0 and abs(closes[last] - nl_last) / nl_last <= 0.03:
                return {'stage': PatternStage.PRE_BREAK, 'cross_idx': None, 'days_back_inside': None}
            return {'stage': PatternStage.FORMING, 'cross_idx': None, 'days_back_inside': None}

        # 3) 突破当日
        if cross_idx == last:
            return {'stage': PatternStage.BREAKOUT_DAY, 'cross_idx': cross_idx, 'days_back_inside': 0}

        if bullish:
            # 3a) 突破失败：突破后连续 ≥3 日收盘跌破颈线，且当前价在颈线下方
            below_streak = 0
            for j in range(last, cross_idx, -1):
                if closes[j] < neckline_fn(j):
                    below_streak += 1
                else:
                    break
            if below_streak >= 3:
                return {'stage': PatternStage.FAILED_BREAK, 'cross_idx': cross_idx, 'days_back_inside': below_streak}

            # 3b) 回踩：突破后最低价触及颈线 ±2% 区间，且当前收盘在颈线上方 8% 内
            touched = any(
                neckline_fn(j) * 0.98 <= klines[j].low <= neckline_fn(j) * 1.02
                for j in range(cross_idx, n)
            )
            in_band = nl_last < closes[last] <= nl_last * 1.08
            if touched and in_band:
                return {'stage': PatternStage.RETEST, 'cross_idx': cross_idx, 'days_back_inside': below_streak}
            return {'stage': PatternStage.POST_BREAK, 'cross_idx': cross_idx, 'days_back_inside': below_streak}
        else:
            # 看跌形态对称：跌破失败 = 连续 ≥3 日收盘站回颈线上方
            above_streak = 0
            for j in range(last, cross_idx, -1):
                if closes[j] > neckline_fn(j):
                    above_streak += 1
                else:
                    break
            if above_streak >= 3:
                return {'stage': PatternStage.FAILED_BREAK, 'cross_idx': cross_idx, 'days_back_inside': above_streak}

            # 回抽：跌破后最高价触及颈线 ±2%，且当前收盘在颈线下方 8% 内
            touched = any(
                neckline_fn(j) * 0.98 <= klines[j].high <= neckline_fn(j) * 1.02
                for j in range(cross_idx, n)
            )
            in_band = nl_last * 0.92 <= closes[last] < nl_last
            if touched and in_band:
                return {'stage': PatternStage.RETEST, 'cross_idx': cross_idx, 'days_back_inside': above_streak}
            return {'stage': PatternStage.POST_BREAK, 'cross_idx': cross_idx, 'days_back_inside': above_streak}

    def _breakout_evidence(
        self,
        klines: List[OHLCV],
        state: Dict[str, Any],
        neckline_fn: Callable[[int], float],
        breakout_mult: float,
        close_beyond_line: bool,
    ) -> Dict[str, Any]:
        """FIR-36 / FIR-32 D3：机器可读的突破证据（阶段语义不变）"""
        last = len(klines) - 1
        ci = state["cross_idx"]
        back = state["days_back_inside"]
        return {
            "breakout_line": round(neckline_fn(last) * breakout_mult, 4),
            "close_beyond_breakout_line": close_beyond_line,
            "breakout_occurred": ci is not None,
            "breakout_date": klines[ci].date if ci is not None else None,
            "breakout_close": round(klines[ci].close, 4) if ci is not None else None,
            "bars_since_breakout": (last - ci) if ci is not None else None,
            "back_inside_neckline": (back > 0) if ci is not None else None,
            "days_back_inside": back,
            "failed_break_days": 3,
            "volume_confirmed": self._volume_confirmed(klines),
        }

    # ---------- 颈线点定位辅助 ----------

    @staticmethod
    def _pick_extreme_pivot(
        candidates: List[PivotPoint],
        pick_max: bool,
    ) -> Optional[PivotPoint]:
        """从候选 pivot 中取价格最高/最低者；返回副本（含原始 index/date）"""
        if not candidates:
            return None
        if pick_max:
            return max(candidates, key=lambda p: p.price)
        return min(candidates, key=lambda p: p.price)

# ============================================================
# W Bottom — W底/双底（看涨）
# ============================================================

@registry.pattern
class WBottomDetector(PivotPatternDetectorBase):
    """
    W底/双底识别器

    判断条件：
    1. 两个相邻的已确认 swing low（左底 a、右底 b）
    2. 两底价格差距 ≤ 5%
    3. 右底不低于左底的 97%（允许比左底低至多 3%，见 FIR-34）
    4. 颈线 = 两底之间最高的 swing high（水平颈线）
    5. 突破确认：当前收盘 > 颈线 * 1.01

    目标价 = 颈线 + (颈线 - 两底最低价)
    止损   = 右底最低价 * 0.98
    """

    @property
    def pattern_id(self) -> str:
        return "w_bottom"

    @property
    def pattern_name(self) -> str:
        return "W底"

    def detect(self, klines: List[OHLCV], pivots: Optional[List[PivotPoint]] = None) -> List[PatternResult]:
        results: List[PatternResult] = []
        if len(klines) < 15:
            return results

        confirmed = self._get_confirmed_pivots(klines, pivots)
        lows = sorted([p for p in confirmed if p.pivot_type == "swing_low"],
                      key=lambda p: p.index)
        highs = sorted([p for p in confirmed if p.pivot_type == "swing_high"],
                       key=lambda p: p.index)

        # 遍历相邻的一对 swing low（左底、右底）
        for a, b in zip(lows, lows[1:]):
            # --- 形态闸门 ---
            # 右底不低于左底 97%
            if b.price < a.price * 0.97:
                continue
            # 两底价格差距 ≤ 5%
            gap_pct = abs(b.price - a.price) / min(a.price, b.price)
            if gap_pct > 0.05:
                continue

            # --- 颈线：两底之间最高的 swing high ---
            between = [h for h in highs if a.index < h.index < b.index]
            if not between:
                # 缺少对立 pivot（两底之间无已确认 swing high）：拒绝该候选。
                # 不允许退化到无限制的原始 K 线极值（违反 FIR-12 acceptance）。
                continue
            nl_pt = self._pick_extreme_pivot(between, pick_max=True)
            neckline = nl_pt.price

            # 水平颈线：常数函数
            nl_fn = lambda idx, nl=neckline: nl

            bstate = self._breakout_state(klines, b.index, nl_fn, bullish=True, breakout_mult=1.01)
            stage = bstate["stage"]
            broken = klines[-1].close > neckline * 1.01

            low_min = min(a.price, b.price)
            target = neckline + (neckline - low_min)
            stop = b.price * 0.98

            # --- 评分：两底接近40 + 突破35/15 + 量能25/10 ---
            score = 40 + (35 if broken else 15) + (25 if self._volume_confirmed(klines) else 10)
            if stage == PatternStage.FAILED_BREAK:
                score = max(10, score - 30)

            evidence = self._breakout_evidence(klines, bstate, nl_fn, 1.01, broken)
            key_points = [
                PatternKeyPoint("左底", a.index, a.date, a.price, "第一个摆动低点"),
                PatternKeyPoint("右底", b.index, b.date, b.price, "第二个摆动低点（不低于左底 3% 以上）"),
                PatternKeyPoint("颈线点", nl_pt.index, nl_pt.date, neckline,
                                "两底之间的最高点"),
            ]
            results.append(PatternResult(
                pattern_id=self.pattern_id,
                pattern_name=self.pattern_name,
                direction=TrendDirection.BULL,
                stage=stage,
                score=score,
                confidence=self._confidence(score),
                key_points=key_points,
                neckline_price=round(neckline, 4),
                # FIR-36：breakout_price = 突破日收盘价（未发生突破为 None），不再随当前价变化
                breakout_price=evidence["breakout_close"],
                target_price=round(target, 4),
                stop_loss=round(stop, 4),
                category=PATTERN_CATEGORY_PRICE,
                evidence=evidence,
                notes=f"W底：左底{a.price:.2f} 右底{b.price:.2f} 颈线{neckline:.2f}",
            ))
        return results


# ============================================================
# M Top — M头/双头（看跌）
# ============================================================

@registry.pattern
class MTopDetector(PivotPatternDetectorBase):
    """
    M头/双头识别器（与 W Bottom 对称）

    判断条件：
    1. 两个相邻的已确认 swing high（左顶 a、右顶 b）
    2. 两顶价格差距 ≤ 5%
    3. 右顶不高于左顶的 103%（允许比左顶高至多 3%，见 FIR-34）
    4. 颈线 = 两顶之间最低的 swing low（水平颈线）
    5. 跌破确认：当前收盘 < 颈线 * 0.99

    目标价 = 颈线 - (两顶最高价 - 颈线)
    止损   = 右顶最高价 * 1.02
    """

    @property
    def pattern_id(self) -> str:
        return "m_top"

    @property
    def pattern_name(self) -> str:
        return "M头"

    def detect(self, klines: List[OHLCV], pivots: Optional[List[PivotPoint]] = None) -> List[PatternResult]:
        results: List[PatternResult] = []
        if len(klines) < 15:
            return results

        confirmed = self._get_confirmed_pivots(klines, pivots)
        highs = sorted([p for p in confirmed if p.pivot_type == "swing_high"],
                       key=lambda p: p.index)
        lows = sorted([p for p in confirmed if p.pivot_type == "swing_low"],
                      key=lambda p: p.index)

        for a, b in zip(highs, highs[1:]):
            # --- 形态闸门 ---
            # 右顶不高于左顶 103%
            if b.price > a.price * 1.03:
                continue
            # 两顶价格差距 ≤ 5%
            gap_pct = abs(b.price - a.price) / max(a.price, b.price)
            if gap_pct > 0.05:
                continue

            # --- 颈线：两顶之间最低的 swing low ---
            between = [l for l in lows if a.index < l.index < b.index]
            if not between:
                # 缺少对立 pivot（两顶之间无已确认 swing low）：拒绝该候选
                continue
            nl_pt = self._pick_extreme_pivot(between, pick_max=False)
            neckline = nl_pt.price

            nl_fn = lambda idx, nl=neckline: nl

            bstate = self._breakout_state(klines, b.index, nl_fn, bullish=False, breakout_mult=0.99)
            stage = bstate["stage"]
            broken = klines[-1].close < neckline * 0.99

            top_max = max(a.price, b.price)
            target = neckline - (top_max - neckline)
            stop = b.price * 1.02

            # --- 评分：两顶对称40 + 跌破35/15 + 量能25/10（与W底对称） ---
            score = 40 + (35 if broken else 15) + (25 if self._volume_confirmed(klines) else 10)
            if stage == PatternStage.FAILED_BREAK:
                score = max(10, score - 30)

            evidence = self._breakout_evidence(klines, bstate, nl_fn, 0.99, broken)
            key_points = [
                PatternKeyPoint("左顶", a.index, a.date, a.price, "第一个摆动高点"),
                PatternKeyPoint("右顶", b.index, b.date, b.price, "第二个摆动高点（不高于左顶 3% 以上）"),
                PatternKeyPoint("颈线点", nl_pt.index, nl_pt.date, neckline,
                                "两顶之间的最低点"),
            ]
            results.append(PatternResult(
                pattern_id=self.pattern_id,
                pattern_name=self.pattern_name,
                direction=TrendDirection.BEAR,
                stage=stage,
                score=score,
                confidence=self._confidence(score),
                key_points=key_points,
                neckline_price=round(neckline, 4),
                # FIR-36：breakout_price = 突破日收盘价（未发生突破为 None），不再随当前价变化
                breakout_price=evidence["breakout_close"],
                target_price=round(target, 4),
                stop_loss=round(stop, 4),
                category=PATTERN_CATEGORY_PRICE,
                evidence=evidence,
                notes=f"M头：左顶{a.price:.2f} 右顶{b.price:.2f} 颈线{neckline:.2f}",
            ))
        return results


# ============================================================
# H&S Bottom — 头肩底（看涨，倾斜颈线）
# ============================================================

@registry.pattern
class HSBottomDetector(PivotPatternDetectorBase):
    """
    头肩底识别器（支持倾斜颈线）

    判断条件：
    1. 三个相邻的已确认 swing low：左肩 ls、头部 h、右肩 rs
    2. 头部低于左肩：h.price < ls.price
    3. 右肩高于头部：rs.price > h.price
    4. 左右肩对称：|ls.price - rs.price| / ls.price ≤ 20%
    5. 颈线：左肩→头部之间最高的 swing high（左颈线点）与
       头部→右肩之间最高的 swing high（右颈线点）连线，支持倾斜
       斜率 = (右颈线价 - 左颈线价) / (右颈线索引 - 左颈线索引)
       当前颈线值 = 左颈线价 + 斜率 * (当前索引 - 左颈线索引)
    6. 突破确认：当前收盘 > 当前颈线值 * 0.98

    目标价 = 当前颈线值 + (当前颈线值 - 头部价)
    止损   = 头部价 * 0.98
    """

    @property
    def pattern_id(self) -> str:
        return "hs_bottom"

    @property
    def pattern_name(self) -> str:
        return "头肩底"

    def detect(self, klines: List[OHLCV], pivots: Optional[List[PivotPoint]] = None) -> List[PatternResult]:
        results: List[PatternResult] = []
        if len(klines) < 25:
            return results

        confirmed = self._get_confirmed_pivots(klines, pivots)
        lows = sorted([p for p in confirmed if p.pivot_type == "swing_low"],
                      key=lambda p: p.index)
        highs = sorted([p for p in confirmed if p.pivot_type == "swing_high"],
                       key=lambda p: p.index)

        # 遍历相邻三连 swing low：左肩、头、右肩
        for ls, head, rs in zip(lows, lows[1:], lows[2:]):
            # --- 形态闸门 ---
            # 头部必须低于左肩
            if head.price >= ls.price:
                continue
            # 右肩必须高于头部
            if rs.price <= head.price:
                continue
            # 左右肩价格差距 ≤ 20%
            if ls.price <= 0 or abs(ls.price - rs.price) / ls.price > 0.20:
                continue

            # --- FIR-24 guardrail 1a：最小头-肩突出度 ---
            # head 必须相对双肩平均价有足够「突出」，否则是平头/普通双底而非 H&S。
            avg_shoulder = (ls.price + rs.price) / 2.0
            if avg_shoulder <= 0:
                continue
            head_prominence = abs(head.price - avg_shoulder) / avg_shoulder
            if head_prominence < MIN_HS_HEAD_PROMINENCE:
                continue

            # --- 颈线两点 ---
            left_between = [h for h in highs if ls.index < h.index < head.index]
            if not left_between:
                # 左颈线区间缺少已确认对立 pivot：拒绝该候选
                continue
            ln = self._pick_extreme_pivot(left_between, pick_max=True)

            right_between = [h for h in highs if head.index < h.index < rs.index]
            if not right_between:
                # 右颈线区间缺少已确认对立 pivot：拒绝该候选
                continue
            rn = self._pick_extreme_pivot(right_between, pick_max=True)

            # 倾斜颈线：线性插值
            if rn.index != ln.index:
                slope = (rn.price - ln.price) / (rn.index - ln.index)
            else:
                slope = 0.0
            nl_fn = lambda idx, ln=ln, slope=slope: ln.price + slope * (idx - ln.index)

            bstate = self._breakout_state(klines, rs.index, nl_fn, bullish=True, breakout_mult=0.98)
            stage = bstate["stage"]
            nl_now = nl_fn(len(klines) - 1)

            # --- FIR-24 guardrail 1b：倾斜颈线外推合理性 ---
            # 外推颈线必须为正；倾斜颈线（slope != 0）外推值不能远离结构
            # （不能比头部高过 MAX_NECKLINE_EXTRAPOLATION_RATIO 倍）。
            # 水平颈线（slope == 0）外推=常数，不受此限制。
            if nl_now <= 0:
                continue
            if slope != 0.0 and nl_now > head.price * MAX_NECKLINE_EXTRAPOLATION_RATIO:
                continue

            broken = klines[-1].close > nl_now * 0.98

            target = nl_now + (nl_now - head.price)
            stop = head.price * 0.98

            # --- 评分：左肩高于头20 + 右肩高于头20 + 左右肩对称20 + 突破40/10 ---
            score = 20 + 20 + 20 + (40 if broken else 10)
            if stage == PatternStage.FAILED_BREAK:
                score = max(10, score - 30)

            evidence = self._breakout_evidence(klines, bstate, nl_fn, 0.98, broken)
            key_points = [
                PatternKeyPoint("左肩", ls.index, ls.date, ls.price, "左肩摆动低点"),
                PatternKeyPoint("头", head.index, head.date, head.price, "头部摆动低点（最低）"),
                PatternKeyPoint("右肩", rs.index, rs.date, rs.price, "右肩摆动低点（高于头部）"),
                PatternKeyPoint("左颈线点", ln.index, ln.date, ln.price,
                                "左肩→头部之间的反弹高点"),
                PatternKeyPoint("右颈线点", rn.index, rn.date, rn.price,
                                "头部→右肩之间的反弹高点"),
            ]
            results.append(PatternResult(
                pattern_id=self.pattern_id,
                pattern_name=self.pattern_name,
                direction=TrendDirection.BULL,
                stage=stage,
                score=score,
                confidence=self._confidence(score),
                key_points=key_points,
                neckline_price=round(nl_now, 4),
                # FIR-36：breakout_price = 突破日收盘价（未发生突破为 None），不再随当前价变化
                breakout_price=evidence["breakout_close"],
                target_price=round(target, 4),
                stop_loss=round(stop, 4),
                category=PATTERN_CATEGORY_PRICE,
                evidence=evidence,
                notes=f"头肩底：左肩{ls.price:.2f} 头{head.price:.2f} "
                      f"右肩{rs.price:.2f} 颈线{ln.price:.2f}→{rn.price:.2f}(斜率{slope:.4f})",
            ))
        return results


# ============================================================
# H&S Top — 头肩顶（看跌，倾斜颈线）
# ============================================================

@registry.pattern
class HSTopDetector(PivotPatternDetectorBase):
    """
    头肩顶识别器（与头肩底对称，支持倾斜颈线）

    判断条件：
    1. 三个相邻的已确认 swing high：左肩 ls、头部 h、右肩 rs
    2. 头部高于左肩：h.price > ls.price
    3. 右肩低于头部：rs.price < h.price
    4. 左右肩对称：|ls.price - rs.price| / ls.price ≤ 20%
    5. 颈线：左肩→头部之间最低的 swing low（左颈线点）与
       头部→右肩之间最低的 swing low（右颈线点）连线
    6. 跌破确认：当前收盘 < 当前颈线值 * 1.02

    目标价 = 当前颈线值 - (头部价 - 当前颈线值)
    止损   = 头部价 * 1.02
    """

    @property
    def pattern_id(self) -> str:
        return "hs_top"

    @property
    def pattern_name(self) -> str:
        return "头肩顶"

    def detect(self, klines: List[OHLCV], pivots: Optional[List[PivotPoint]] = None) -> List[PatternResult]:
        results: List[PatternResult] = []
        if len(klines) < 25:
            return results

        confirmed = self._get_confirmed_pivots(klines, pivots)
        highs = sorted([p for p in confirmed if p.pivot_type == "swing_high"],
                       key=lambda p: p.index)
        lows = sorted([p for p in confirmed if p.pivot_type == "swing_low"],
                      key=lambda p: p.index)

        for ls, head, rs in zip(highs, highs[1:], highs[2:]):
            # --- 形态闸门 ---
            # 头部必须高于左肩
            if head.price <= ls.price:
                continue
            # 右肩必须低于头部
            if rs.price >= head.price:
                continue
            # 左右肩价格差距 ≤ 20%
            if ls.price <= 0 or abs(ls.price - rs.price) / ls.price > 0.20:
                continue

            # --- FIR-24 guardrail 1a：最小头-肩突出度（与头肩底对称） ---
            avg_shoulder = (ls.price + rs.price) / 2.0
            if avg_shoulder <= 0:
                continue
            head_prominence = abs(head.price - avg_shoulder) / avg_shoulder
            if head_prominence < MIN_HS_HEAD_PROMINENCE:
                continue

            # --- 颈线两点 ---
            left_between = [l for l in lows if ls.index < l.index < head.index]
            if not left_between:
                # 左颈线区间缺少已确认对立 pivot：拒绝该候选
                continue
            ln = self._pick_extreme_pivot(left_between, pick_max=False)

            right_between = [l for l in lows if head.index < l.index < rs.index]
            if not right_between:
                # 右颈线区间缺少已确认对立 pivot：拒绝该候选
                continue
            rn = self._pick_extreme_pivot(right_between, pick_max=False)

            # 倾斜颈线：线性插值
            if rn.index != ln.index:
                slope = (rn.price - ln.price) / (rn.index - ln.index)
            else:
                slope = 0.0
            nl_fn = lambda idx, ln=ln, slope=slope: ln.price + slope * (idx - ln.index)

            bstate = self._breakout_state(klines, rs.index, nl_fn, bullish=False, breakout_mult=1.02)
            stage = bstate["stage"]
            nl_now = nl_fn(len(klines) - 1)

            # --- FIR-24 guardrail 1b：倾斜颈线外推合理性（与头肩底对称） ---
            # 头肩顶颈线应在头部下方；外推值必须为正，倾斜颈线外推不能比头部
            # 低过 MAX_NECKLINE_EXTRAPOLATION_RATIO 倍（否则外推失控）。
            if nl_now <= 0:
                continue
            if slope != 0.0 and nl_now < head.price / MAX_NECKLINE_EXTRAPOLATION_RATIO:
                continue

            broken = klines[-1].close < nl_now * 1.02

            target = nl_now - (head.price - nl_now)
            stop = head.price * 1.02

            # --- 评分：左肩低于头20 + 右肩低于头20 + 左右肩对称20 + 跌破40/10 ---
            score = 20 + 20 + 20 + (40 if broken else 10)
            if stage == PatternStage.FAILED_BREAK:
                score = max(10, score - 30)

            evidence = self._breakout_evidence(klines, bstate, nl_fn, 1.02, broken)
            key_points = [
                PatternKeyPoint("左肩", ls.index, ls.date, ls.price, "左肩摆动高点"),
                PatternKeyPoint("头", head.index, head.date, head.price, "头部摆动高点（最高）"),
                PatternKeyPoint("右肩", rs.index, rs.date, rs.price, "右肩摆动高点（低于头部）"),
                PatternKeyPoint("左颈线点", ln.index, ln.date, ln.price,
                                "左肩→头部之间的回调低点"),
                PatternKeyPoint("右颈线点", rn.index, rn.date, rn.price,
                                "头部→右肩之间的回调低点"),
            ]
            results.append(PatternResult(
                pattern_id=self.pattern_id,
                pattern_name=self.pattern_name,
                direction=TrendDirection.BEAR,
                stage=stage,
                score=score,
                confidence=self._confidence(score),
                key_points=key_points,
                neckline_price=round(nl_now, 4),
                # FIR-36：breakout_price = 突破日收盘价（未发生突破为 None），不再随当前价变化
                breakout_price=evidence["breakout_close"],
                target_price=round(target, 4),
                stop_loss=round(stop, 4),
                category=PATTERN_CATEGORY_PRICE,
                evidence=evidence,
                notes=f"头肩顶：左肩{ls.price:.2f} 头{head.price:.2f} "
                      f"右肩{rs.price:.2f} 颈线{ln.price:.2f}→{rn.price:.2f}(斜率{slope:.4f})",
            ))
        return results
