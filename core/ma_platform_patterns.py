"""
均线多头 / 均线空头 / 平台突破 / 平台破位 — 从 V4 (scripts/analyze_stock.py:
match_patterns / match_bear_patterns) 迁移为 V5 PatternDetector

迁移原则：
1. 评分公式、阈值、阶段判定与 V4 完全一致（tests/v5/test_v4_migration_parity.py 逐项断言）。
2. 补齐 V4 丢弃的信息：每个关键点带 K 线 index 与日期（V4 只有价格），平台区间的起止日期。
3. 与 V5 PatternDetector 契约对齐，只在形态结构确实存在时返回结果：
   - 均线形态：V4 阶段为“均线未形成多头/空头排列”时不返回；
   - 平台形态：V4 判定“非平台整理：振幅过大”时不返回。
   V4 在这两种情况下仍给出 20~30 分，这是迁移中唯一有意改变的行为。
4. V4 的中文阶段描述原样保留在 notes 中；stage 映射到 V5 PatternStage 枚举（映射表见下）。

FIR-32 产品决策（2026-10-14）：
- D1：均线形态 category = "trend_state"（趋势状态），平台形态 category = "price_pattern"（价格形态）。
- D3：evidence 提供机器可读的判定证据（键见下）；平台形态在“当日首次”越过突破线时
  stage 记为 breakout_day，其余阶段仍按映射表。分数与 V4 完全一致，不受影响。

  均线 evidence：aligned_ma5_ma10 / aligned_ma10_ma20 / aligned_ma20_ma60（按形态方向排列）、
    fully_aligned、close_beyond_ma5、ma5_slope_5d、ma5_slope_ok、v4_stage
  平台 evidence：range_pct、platform_start_date / platform_end_date、breakout_line、
    broke（当日收盘越过突破线）、broke_today（当日越过且前一日收盘未越过同一条线）、
    near_line、had_break_10d、volume_confirmed（当日量 > 1.3×VMA5）、ma_converge、v4_stage

V4 已知局限（原样保留，未修改）：
- 平台固定取“最近 5 根之前的 15~35 根”，不随实际整理区间伸缩。
- 均线形态只看最后一根 K 线的均线排列。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .indicators import ClassicIndicators
from .interfaces import PatternDetector
from .pivot_patterns import PivotPatternDetectorBase
from .registry import registry
from .types import (
    OHLCV, PivotPoint, PatternResult, PatternKeyPoint, TrendDirection, PatternStage,
    PATTERN_CATEGORY_PRICE, PATTERN_CATEGORY_TREND,
)

_confidence = PivotPatternDetectorBase._confidence


# ------------------------------------------------------------------
# V4 计算（原样迁移）
# ------------------------------------------------------------------

def _eval_ma(klines: List[OHLCV], ind: Dict[str, Any], bull: bool) -> Optional[Dict[str, Any]]:
    n = len(klines)
    last = n - 1
    cur_price = klines[last].close
    ma5, ma10, ma20, ma60 = (ind[k][last] for k in ('ma5', 'ma10', 'ma20', 'ma60'))
    if not (ma5 and ma10 and ma20 and ma60):
        return None
    if bull:
        c1 = ma5 > ma10
        c2 = ma10 > ma20
        c3 = ma20 > ma60
        c4 = cur_price > ma5
        slope = ind['ma5'][last] - ind['ma5'][max(0, last-5)]
        c5 = slope > 0
    else:
        c1 = ma5 < ma10
        c2 = ma10 < ma20
        c3 = ma20 < ma60
        c4 = cur_price < ma5
        slope = ind['ma5'][last] - ind['ma5'][max(0, last-5)]
        c5 = slope < 0
    score = (25 if c1 else 0) + (25 if c2 else 0) + (20 if c3 else 0) + (15 if c4 else 0) + (15 if c5 else 0)
    aligned = c1 and c2 and c3
    if bull:
        if aligned and c4 and slope > 0.02 * cur_price:
            stage = '主升浪中期：均线完美多头，沿MA5爬升'
        elif aligned and c4:
            stage = '主升浪初期：均线刚形成多头排列'
        elif aligned and not c4:
            stage = '主升浪回调中：价格跌破MA5，测试MA10/MA20支撑'
        elif c1 and c2 and not c3:
            stage = '均线粘合发散初期：短期均线已多头，MA20即将上穿MA60'
        else:
            stage = '均线未形成多头排列'
    else:
        if aligned and c4 and slope < -0.02 * cur_price:
            stage = '下跌趋势中期：均线完美空头'
        elif aligned and c4:
            stage = '下跌趋势初期：均线刚形成空头排列'
        elif aligned and not c4:
            stage = '下跌趋势反弹中'
        elif c1 and c2 and not c3:
            stage = '均线粘合向下发散初期'
        else:
            stage = '均线未形成空头排列'
    return {'score': score, 'stage': stage, 'last': last,
            'ma': {'MA5': ma5, 'MA10': ma10, 'MA20': ma20, 'MA60': ma60},
            'flags': {'c1': c1, 'c2': c2, 'c3': c3, 'c4': c4, 'c5': c5, 'slope': slope}}


def _eval_platform(klines: List[OHLCV], ind: Dict[str, Any], up: bool) -> Optional[Dict[str, Any]]:
    n = len(klines)
    last = n - 1
    cur_price = klines[last].close
    plat_len = min(35, n - 5)
    if plat_len < 15:
        return None
    plat_start = n - plat_len - 5
    plat = klines[plat_start:n-5]
    plat_high = max(k.high for k in plat)
    plat_low = min(k.low for k in plat)
    range_pct = (plat_high - plat_low) / plat_low
    tight = range_pct < 0.18
    vol_boost = klines[last].volume > (ind['vma5'][last] or 0) * 1.3
    ma_converge = ind['ma5'][last] and ind['ma20'][last] and abs(ind['ma5'][last] - ind['ma20'][last]) / ind['ma20'][last] < 0.06
    recent = klines[n-10:]
    if up:
        broke = cur_price > plat_high * 1.01
        near = cur_price > plat_high * 0.97 and not broke
        had = any(k.close > plat_high * 1.02 for k in recent)
        pullback = had and plat_high * 0.97 < cur_price < plat_high * 1.03
    else:
        broke = cur_price < plat_low * 0.99
        near = cur_price < plat_low * 1.03 and not broke
        had = any(k.close < plat_low * 0.98 for k in recent)
        pullback = had and plat_low * 0.97 < cur_price < plat_low * 1.03
    score = (30 if tight else 10) + (35 if broke else 10) + (20 if vol_boost else 5) + (15 if ma_converge else 5)
    if not tight:
        stage = '非平台整理：振幅过大'
    elif near and not broke:
        stage = '平台整理末端：突破前夜' if up else '平台整理末端：破位前夜'
    elif broke and vol_boost:
        stage = '放量突破平台：突破确认' if up else '放量跌破平台：破位确认'
    elif broke and not vol_boost:
        stage = '缩量突破平台：需确认' if up else '缩量跌破平台：需确认'
    elif pullback:
        stage = '突破后回踩平台上沿确认中' if up else '破位后回抽平台下沿确认中'
    elif had:
        stage = '平台突破后上涨中' if up else '平台破位后下跌中'
    else:
        stage = '平台整理中'
    hi_idx = plat_start + next(i for i, k in enumerate(plat) if k.high == plat_high)
    lo_idx = plat_start + next(i for i, k in enumerate(plat) if k.low == plat_low)
    # FIR-32 D3：当日首次越过 = 今日收盘越过突破线，且前一日收盘未越过同一条线
    line = plat_high * 1.01 if up else plat_low * 0.99
    prev_close = klines[last - 1].close
    prev_broke = prev_close > line if up else prev_close < line
    return {'score': score, 'stage': stage, 'tight': tight, 'last': last,
            'plat_start': plat_start, 'plat_end': n - 6, 'plat_len': plat_len,
            'plat_high': plat_high, 'plat_low': plat_low, 'range_pct': range_pct,
            'hi_idx': hi_idx, 'lo_idx': lo_idx,
            'flags': {'broke': broke, 'broke_today': broke and not prev_broke, 'near': near,
                      'had': had, 'vol_boost': vol_boost, 'ma_converge': bool(ma_converge),
                      'line': line}}


# ------------------------------------------------------------------
# 阶段映射：V4 中文阶段 → V5 PatternStage
# ------------------------------------------------------------------

_MA_STAGE = {
    '主升浪中期：均线完美多头，沿MA5爬升': PatternStage.POST_BREAK,
    '主升浪初期：均线刚形成多头排列': PatternStage.POST_BREAK,
    '主升浪回调中：价格跌破MA5，测试MA10/MA20支撑': PatternStage.RETEST,
    '均线粘合发散初期：短期均线已多头，MA20即将上穿MA60': PatternStage.PRE_BREAK,
    '下跌趋势中期：均线完美空头': PatternStage.POST_BREAK,
    '下跌趋势初期：均线刚形成空头排列': PatternStage.POST_BREAK,
    '下跌趋势反弹中': PatternStage.RETEST,
    '均线粘合向下发散初期': PatternStage.PRE_BREAK,
}

_PLAT_STAGE = {
    '平台整理末端：突破前夜': PatternStage.PRE_BREAK,
    '平台整理末端：破位前夜': PatternStage.PRE_BREAK,
    '放量突破平台：突破确认': PatternStage.POST_BREAK,
    '放量跌破平台：破位确认': PatternStage.POST_BREAK,
    '缩量突破平台：需确认': PatternStage.POST_BREAK,
    '缩量跌破平台：需确认': PatternStage.POST_BREAK,
    '突破后回踩平台上沿确认中': PatternStage.RETEST,
    '破位后回抽平台下沿确认中': PatternStage.RETEST,
    '平台突破后上涨中': PatternStage.POST_BREAK,
    '平台破位后下跌中': PatternStage.POST_BREAK,
    '平台整理中': PatternStage.FORMING,
}


class _MADetectorBase(PatternDetector):
    _bull = True

    def detect(self, klines: List[OHLCV], pivots: Optional[List[PivotPoint]] = None) -> List[PatternResult]:
        if len(klines) < 60:
            return []
        ind = ClassicIndicators().calculate(klines)
        ev = _eval_ma(klines, ind, self._bull)
        if ev is None or ev['stage'] not in _MA_STAGE:
            return []
        last, date = ev['last'], klines[ev['last']].date
        kps = [PatternKeyPoint(name=name, index=last, date=date, price=round(v, 2),
                               description=f'{date} 的 {name} 数值')
               for name, v in ev['ma'].items()]
        f = ev['flags']
        evidence = {
            'aligned_ma5_ma10': f['c1'],
            'aligned_ma10_ma20': f['c2'],
            'aligned_ma20_ma60': f['c3'],
            'fully_aligned': f['c1'] and f['c2'] and f['c3'],
            'close_beyond_ma5': f['c4'],
            'ma5_slope_5d': round(f['slope'], 4),
            'ma5_slope_ok': f['c5'],
            'v4_stage': ev['stage'],
        }
        return [PatternResult(
            pattern_id=self.pattern_id,
            pattern_name=self.pattern_name,
            direction=TrendDirection.BULL if self._bull else TrendDirection.BEAR,
            stage=_MA_STAGE[ev['stage']],
            score=ev['score'],
            confidence=_confidence(ev['score']),
            key_points=kps,
            notes=ev['stage'],
            category=PATTERN_CATEGORY_TREND,
            evidence=evidence,
        )]


@registry.pattern
class MABullDetector(_MADetectorBase):
    pattern_id = "ma_bull"
    pattern_name = "均线多头"
    _bull = True


@registry.pattern
class MABearDetector(_MADetectorBase):
    pattern_id = "ma_bear"
    pattern_name = "均线空头"
    _bull = False


class _PlatformDetectorBase(PatternDetector):
    _up = True

    def detect(self, klines: List[OHLCV], pivots: Optional[List[PivotPoint]] = None) -> List[PatternResult]:
        if len(klines) < 20:
            return []
        ind = ClassicIndicators().calculate(klines)
        ev = _eval_platform(klines, ind, self._up)
        if ev is None or not ev['tight']:
            return []
        s, e = klines[ev['plat_start']].date, klines[ev['plat_end']].date
        last = ev['last']
        kps = [
            PatternKeyPoint(name='平台上沿', index=ev['hi_idx'], date=klines[ev['hi_idx']].date,
                            price=ev['plat_high'], description=f'平台区间 {s} ~ {e} 的最高价'),
            PatternKeyPoint(name='平台下沿', index=ev['lo_idx'], date=klines[ev['lo_idx']].date,
                            price=ev['plat_low'], description=f'平台区间 {s} ~ {e} 的最低价'),
            PatternKeyPoint(name='当前价', index=last, date=klines[last].date,
                            price=klines[last].close, description='最新收盘价'),
        ]
        level = ev['plat_high'] if self._up else ev['plat_low']
        threshold = '上沿 ×1.01' if self._up else '下沿 ×0.99'
        f = ev['flags']
        stage = _PLAT_STAGE[ev['stage']]
        if f['broke_today']:
            stage = PatternStage.BREAKOUT_DAY  # FIR-32 D3：当日首次越过突破线
        evidence = {
            'range_pct': round(ev['range_pct'], 6),
            'platform_start_date': s,
            'platform_end_date': e,
            'breakout_line': round(f['line'], 4),
            'broke': f['broke'],
            'broke_today': f['broke_today'],
            'near_line': f['near'],
            'had_break_10d': f['had'],
            'volume_confirmed': f['vol_boost'],
            'ma_converge': f['ma_converge'],
            'v4_stage': ev['stage'],
        }
        return [PatternResult(
            pattern_id=self.pattern_id,
            pattern_name=self.pattern_name,
            direction=TrendDirection.BULL if self._up else TrendDirection.BEAR,
            stage=stage,
            score=ev['score'],
            confidence=_confidence(ev['score']),
            key_points=kps,
            neckline_price=level,
            notes=(f"{ev['stage']}｜平台区间 {s} ~ {e}（{ev['plat_len']} 根），"
                   f"振幅 {ev['range_pct']*100:.1f}%，突破判定线 {threshold}"),
            category=PATTERN_CATEGORY_PRICE,
            evidence=evidence,
        )]


@registry.pattern
class PlatformBreakDetector(_PlatformDetectorBase):
    pattern_id = "platform_break"
    pattern_name = "平台突破"
    _up = True


@registry.pattern
class PlatformBreakdownDetector(_PlatformDetectorBase):
    pattern_id = "platform_break_down"
    pattern_name = "平台破位"
    _up = False
