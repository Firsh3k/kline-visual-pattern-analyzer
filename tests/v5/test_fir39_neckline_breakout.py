"""
FIR-39：颈线突破判定（PO 2026-10-14 采纳建议）

A. 突破日 = 当日收盘越过判定线（颈线 × mult）且前一日收盘未越过同一条判定线（与平台 breakout_day 一致）；
   旧规则要求前一日收盘 ≤ 颈线本身，“先站上颈线、次日才过判定线”的两步越线永远不算突破。
B. 头肩形态：右肩极值点必须在（外推）颈线内侧，否则不报告。
C. 头肩倍数与 W/M 一致：头肩底 1.01（原 0.98）、头肩顶 0.99（原 1.02）。

回归证据见 PR 描述（tests/tools/compare_detector_outputs.py，基线 4df2462）。
"""
import csv
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "oracle"))

from core import HSBottomDetector, HSTopDetector, OHLCV, PatternStage, WBottomDetector, registry  # noqa: E402
from core.types import PivotPoint  # noqa: E402
from test_fir36_breakout_evidence import _w, _wres  # noqa: E402
import test_v4_migration_parity as P  # noqa: E402


def _fixture():
    with open(HERE / "fixtures" / "002436_qfq_20261013.csv", newline="", encoding="utf-8") as f:
        return [OHLCV(r["date"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                      float(r["volume"])) for r in csv.DictReader(f)]


# ------------------------------------------------------------------ A：两步越线

def test_two_step_cross_is_a_breakout():
    # 颈线 12.0，判定线 12.12：12.05 先站上颈线但未过判定线，次日 12.3 越过 → 当日为突破日
    r = _wres([11.7, 12.05, 12.3])
    assert r.stage == PatternStage.BREAKOUT_DAY
    assert r.evidence["breakout_close"] == 12.3 and r.breakout_price == 12.3


def test_002436_real_two_step_breakout():
    """002436：颈线 22.21，01-07 收 22.28（站上颈线未过判定线）、01-08 22.23、01-09 22.46 → 01-09 为突破日（原为 forming）"""
    kl = _fixture()[:71]
    (r,) = [r for r in WBottomDetector().detect(kl) if r.key_points[1].date == "2025-12-16"]
    assert r.neckline_price == 22.21
    assert r.stage == PatternStage.BREAKOUT_DAY
    assert r.evidence["breakout_date"] == "2026-01-09" and r.breakout_price == 22.46


def test_already_beyond_line_on_previous_day_is_not_a_new_breakout():
    r = _wres([11.7, 12.2, 12.3])          # 12.2 已越过判定线 → 12.2 当日为突破日，12.3 为突破后
    assert r.evidence["breakout_close"] == 12.2 and r.stage != PatternStage.BREAKOUT_DAY


# ------------------------------------------------------------------ B / C：头肩

def _flat(n=80, price=100.0):
    from test_pivot_patterns import make_klines  # noqa: WPS433
    return make_klines([price] * n)


def _piv(kl, idx, price, kind):
    return PivotPoint(index=idx, date=kl[idx].date, price=price, pivot_type=kind, confirmed=True,
                      confirmed_at_index=idx + 5, confirmed_at_date=None)


def test_hs_bottom_right_shoulder_beyond_sloped_neckline_is_rejected():
    kl = _flat()
    # 颈线从 96（idx 17）降到 86（idx 32），外推到右肩 idx 40 ≈ 80.7；右肩低点 88 在颈线上方 → 不是有效头肩底
    pivots = [_piv(kl, 10, 90.0, "swing_low"), _piv(kl, 25, 80.0, "swing_low"), _piv(kl, 40, 88.0, "swing_low"),
              _piv(kl, 17, 96.0, "swing_high"), _piv(kl, 32, 86.0, "swing_high")]
    assert HSBottomDetector().detect(kl, pivots=pivots) == []


def test_hs_top_right_shoulder_beyond_sloped_neckline_is_rejected():
    kl = _flat()
    pivots = [_piv(kl, 10, 110.0, "swing_high"), _piv(kl, 25, 120.0, "swing_high"), _piv(kl, 40, 112.0, "swing_high"),
              _piv(kl, 17, 104.0, "swing_low"), _piv(kl, 32, 114.0, "swing_low")]
    assert HSTopDetector().detect(kl, pivots=pivots) == []


def test_hs_bottom_close_just_below_neckline_is_not_a_breakout():
    """颈线 96：收盘 95（在 0.98 倍以上、颈线以下）旧规则算突破，现在不算"""
    from test_pivot_patterns import make_klines  # noqa: WPS433
    kl = make_klines([90.0] * 79 + [95.0])
    pivots = [_piv(kl, 10, 90.0, "swing_low"), _piv(kl, 25, 84.0, "swing_low"), _piv(kl, 40, 90.0, "swing_low"),
              _piv(kl, 17, 96.0, "swing_high"), _piv(kl, 32, 96.0, "swing_high")]
    (r,) = HSBottomDetector().detect(kl, pivots=pivots)
    assert r.evidence["breakout_line"] == round(96.0 * 1.01, 4)
    assert r.evidence["close_beyond_breakout_line"] is False and r.evidence["breakout_occurred"] is False
    assert r.stage == PatternStage.PRE_BREAK and r.breakout_price is None


# ------------------------------------------------------------------ 全数据集：不再有“已越线却未识别突破”

@pytest.mark.parametrize("name", sorted(P.DATASETS))
def test_no_missed_breakouts(name):
    rows = P.DATASETS[name]
    for n in range(60, len(rows) + 1, 2):
        kl = P._v5_klines(rows[:n])
        for pid in ("w_bottom", "m_top", "hs_bottom", "hs_top"):
            for r in registry.get_pattern(pid).detect(kl):
                e = r.evidence
                assert not (e["close_beyond_breakout_line"] and not e["breakout_occurred"]), (name, n, pid)
