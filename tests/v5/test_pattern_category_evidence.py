"""
FIR-32 产品决策 D1 / D3 的冻结测试（PO 2026-10-14 确认）

D1：PatternResult.category 区分 “trend_state”（均线多头/空头）与 “price_pattern”（其余 6 种）。
D3：PatternResult.evidence 提供机器可读的判定证据；平台形态在“当日首次越过突破线”时
    stage = breakout_day（前一日收盘未越过同一条线），之后为 post_break；
    放量 / 缩量由 evidence.volume_confirmed 区分，不再只靠 notes 文字。

W底 / M头 / 头肩底 / 头肩顶 的 evidence 暂为空（其突破状态细化见 FIR-36）。
"""
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import (  # noqa: E402
    OHLCV, PatternResult, PatternStage, TrendDirection, ConfidenceLevel, registry,
    PlatformBreakDetector, PlatformBreakdownDetector,
)
from core.types import PATTERN_CATEGORY_PRICE, PATTERN_CATEGORY_TREND  # noqa: E402

EXPECTED_CATEGORY = {
    "w_bottom": PATTERN_CATEGORY_PRICE, "m_top": PATTERN_CATEGORY_PRICE,
    "hs_bottom": PATTERN_CATEGORY_PRICE, "hs_top": PATTERN_CATEGORY_PRICE,
    "platform_break": PATTERN_CATEGORY_PRICE, "platform_break_down": PATTERN_CATEGORY_PRICE,
    "ma_bull": PATTERN_CATEGORY_TREND, "ma_bear": PATTERN_CATEGORY_TREND,
}


# ------------------------------------------------------------------ 数据

def _days(n):
    d, out = date(2026, 1, 5), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _platform(tail, vols):
    """120 根窄幅震荡（50±1.5）后接 tail；返回 OHLCV 列表"""
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


def _fixture_002436():
    import csv
    rows = []
    with open(ROOT / "tests" / "v5" / "fixtures" / "002436_qfq_20261013.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(OHLCV(r["date"], float(r["open"]), float(r["high"]), float(r["low"]),
                              float(r["close"]), float(r["volume"])))
    return rows[-250:]


# ------------------------------------------------------------------ D1

def test_schema_defaults_and_serialization():
    r = PatternResult(pattern_id="x", pattern_name="x", direction=TrendDirection.BULL,
                      stage=PatternStage.FORMING, score=1, confidence=ConfidenceLevel.D, key_points=[])
    assert r.category == PATTERN_CATEGORY_PRICE and r.evidence == {}
    d = r.to_dict()
    assert d["category"] == PATTERN_CATEGORY_PRICE and d["evidence"] == {}
    json.dumps(d)
    # evidence 在 to_dict 中是拷贝，修改输出不影响原对象
    d["evidence"]["k"] = 1
    assert r.evidence == {}


@pytest.mark.parametrize("pid", sorted(EXPECTED_CATEGORY))
def test_every_registered_detector_reports_its_category(pid):
    det = registry.get_pattern(pid)
    sets = [_fixture_002436(),
            _platform([51.0, 51.4, 52.6, 53.0], [1e5, 1e5, 4e5, 1e5]),
            _platform([49.0, 48.6, 47.4, 47.0], [1e5, 1e5, 4e5, 1e5])]
    for kl in sets:
        for n in range(60, len(kl) + 1, 7):
            for r in det.detect(kl[:n]):
                assert r.category == EXPECTED_CATEGORY[pid]
                json.dumps(r.to_dict(), ensure_ascii=False)


# ------------------------------------------------------------------ D3：平台突破时点

@pytest.mark.parametrize("det,tail,line", [
    (PlatformBreakDetector(), [51.0, 51.4, 52.6, 53.0], 52.318),
    (PlatformBreakdownDetector(), [49.0, 48.6, 47.4, 47.0], 47.7279),
], ids=["break_up", "break_down"])
def test_platform_breakout_day_then_post_break(det, tail, line):
    rows = _platform(tail, [1e5, 1e5, 4e5, 1e5])
    seq = []
    for n in (121, 122, 123, 124):
        (r,) = det.detect(rows[:n])
        e = r.evidence
        seq.append((r.stage, r.score, e["broke"], e["broke_today"], e["volume_confirmed"]))
        assert e["breakout_line"] == line
    assert seq == [
        (PatternStage.PRE_BREAK, 60, False, False, False),
        (PatternStage.PRE_BREAK, 60, False, False, False),
        (PatternStage.BREAKOUT_DAY, 100, True, True, True),     # 当日首次越过，放量
        (PatternStage.POST_BREAK, 85, True, False, False),      # 次日仍在线外，缩量
    ]


def test_low_volume_breakout_day_is_flagged_not_confirmed():
    (r,) = PlatformBreakDetector().detect(_platform([51.0, 51.4, 52.6], [1e5, 1e5, 1e5])[:123])
    assert r.stage == PatternStage.BREAKOUT_DAY
    assert r.evidence["broke_today"] is True and r.evidence["volume_confirmed"] is False
    assert r.evidence["v4_stage"] == "缩量突破平台：需确认"


def test_platform_evidence_keys_are_stable():
    (r,) = PlatformBreakDetector().detect(_platform([51.0, 51.4, 52.6], [1e5, 1e5, 4e5])[:123])
    assert set(r.evidence) == {
        "range_pct", "platform_start_date", "platform_end_date", "breakout_line", "broke",
        "broke_today", "near_line", "had_break_10d", "volume_confirmed", "ma_converge", "v4_stage",
    }


# ------------------------------------------------------------------ 真实数据冻结（002436 @ 2026-10-13）

def test_002436_frozen_outputs():
    kl = _fixture_002436()
    (bear,) = registry.get_pattern("ma_bear").detect(kl)
    assert bear.category == PATTERN_CATEGORY_TREND
    assert bear.stage == PatternStage.PRE_BREAK and bear.score == 80
    assert bear.evidence == {
        "aligned_ma5_ma10": True, "aligned_ma10_ma20": True, "aligned_ma20_ma60": False,
        "fully_aligned": False, "close_beyond_ma5": True, "ma5_slope_5d": -5.072,
        "ma5_slope_ok": True, "v4_stage": "均线粘合向下发散初期",
    }
    assert registry.get_pattern("ma_bull").detect(kl) == []
    for pid in ("w_bottom", "m_top"):
        for r in registry.get_pattern(pid).detect(kl):
            assert r.category == PATTERN_CATEGORY_PRICE and r.evidence == {}
