"""
FIR-35：平台“突破后回踩 / 破位后回抽”在 V5 中可达（PO 2026-10-14：调整顺序使其可达）

规则（阈值未改，只调整判定顺序并把“曾越过”限定为今日之前）：
  回踩 = 此前 9 个交易日（不含今日）内曾收盘 > 上沿×1.02
         且 今日收盘位于 (上沿×0.97, 上沿×1.03)
         且 今日不是“首次越过判定线”日（否则仍为 breakout_day）
  回抽为对称规则（下沿×0.98 / ×0.97~×1.03）。
  分数公式不变；evidence.v4_stage 保留 V4 原顺序的结论，evidence.retest 给出新判定。
"""
import math
from datetime import date, timedelta

from core import OHLCV, PatternStage, PlatformBreakDetector, PlatformBreakdownDetector


def _days(n):
    d, out = date(2026, 1, 5), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _platform(tail, vols=None):
    vols = vols or [1e5] * len(tail)
    ds = _days(120 + len(tail))
    rows, p = [], 50.0
    for i in range(120):
        c = round(50 + math.sin(i / 3) * 1.5, 2)
        rows.append(OHLCV(ds[i], p, round(max(p, c) + 0.3, 2), round(min(p, c) - 0.3, 2), c, 100000.0))
        p = c
    for j, c in enumerate(tail):
        rows.append(OHLCV(ds[120 + j], p, round(max(p, c) + 0.2, 2), round(min(p, c) - 0.2, 2), c, float(vols[j])))
        p = c
    return rows


def _one(det, kl):
    (r,) = det.detect(kl)
    return r


def _level(r, up=True):
    return next(k.price for k in r.key_points if k.name == ("平台上沿" if up else "平台下沿"))


def test_platform_level_assumption():
    r = _one(PlatformBreakDetector(), _platform([51.0] * 5))
    assert abs(_level(r) - 51.8) < 1e-9 and abs(_level(r, True) * 1.01 - 52.318) < 1e-9


def test_retest_below_line_was_v4_pre_break_now_retest():
    # 53.0 首次突破 → 53.5 → 回落到线下 51.5（仍在上沿 ±3% 内）
    r = _one(PlatformBreakDetector(), _platform([53.0, 53.5, 52.8, 52.2, 51.5]))
    assert r.stage == PatternStage.RETEST
    assert r.notes.startswith("突破后回踩平台上沿确认中｜")
    assert r.evidence["retest"] is True and r.evidence["had_break_prior_9d"] is True
    assert r.evidence["broke"] is False
    assert r.evidence["v4_stage"] == "平台整理末端：突破前夜"   # V4 会误判为“突破前夜”
    assert r.score == 60                                           # 分数公式不变：30+10+5+15


def test_retest_above_line_was_v4_broke_now_retest():
    r = _one(PlatformBreakDetector(), _platform([53.0, 53.5, 52.8, 52.5, 52.6]))
    assert r.stage == PatternStage.RETEST
    assert r.evidence["broke"] is True and r.evidence["broke_today"] is False
    assert r.evidence["v4_stage"] == "缩量突破平台：需确认"
    assert r.score == 85


def test_rebreak_after_pullback_is_breakout_day_not_retest():
    # 曾突破、回到线内，今日再次越过判定线：当日首次越过优先
    r = _one(PlatformBreakDetector(), _platform([53.0, 53.5, 52.0, 51.9, 52.9]))
    assert r.stage == PatternStage.BREAKOUT_DAY
    assert r.evidence["retest"] is False and r.evidence["broke_today"] is True


def test_first_breakout_day_in_band_is_not_retest():
    # 今日首次突破且收盘落在 1.02~1.03 区间：此前无越过 → 不是回踩
    r = _one(PlatformBreakDetector(), _platform([51.0, 51.2, 51.4, 51.5, 53.0]))
    assert r.stage == PatternStage.BREAKOUT_DAY
    assert r.evidence["retest"] is False and r.evidence["had_break_prior_9d"] is False


def test_far_above_band_stays_post_break():
    r = _one(PlatformBreakDetector(), _platform([53.0, 54.0, 55.0, 55.5, 56.0]))
    assert r.stage == PatternStage.POST_BREAK and r.evidence["retest"] is False


def test_no_prior_break_near_line_stays_pre_break():
    r = _one(PlatformBreakDetector(), _platform([51.0, 51.3, 51.5, 51.4, 51.6]))
    assert r.stage == PatternStage.PRE_BREAK and r.evidence["retest"] is False


def test_breakdown_pullback_symmetric():
    base = _one(PlatformBreakdownDetector(), _platform([49.0] * 5))
    lo = _level(base, up=False)
    tail = [round(lo * f, 2) for f in (0.975, 0.965, 0.975, 0.98, 1.005)]
    r = _one(PlatformBreakdownDetector(), _platform(tail))
    assert r.stage == PatternStage.RETEST
    assert r.notes.startswith("破位后回抽平台下沿确认中｜")
    assert r.evidence["v4_stage"] == "平台整理末端：破位前夜"
