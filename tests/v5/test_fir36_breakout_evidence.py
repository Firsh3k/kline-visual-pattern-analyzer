"""
FIR-36：W底 / M头 / 头肩底 / 头肩顶 的突破状态证据（PO 2026-10-14：阶段不变，证据标注）

决定：
- PatternStage 语义与枚举都不变（连续 ≥3 日收盘回到颈线另一侧才判 failed_break）；
- evidence.back_inside_neckline / days_back_inside 标注“已突破但当前收盘回到颈线内”及天数；
- breakout_price = 突破日收盘价（evidence.breakout_close），未发生突破为 None，不再依赖当前价。

回归证据：tests/tools/compare_detector_outputs.py（见 PR 描述）——阶段、分数、置信、关键点、
颈线、目标、止损均不变；变化仅为 breakout_price 与新增的 evidence。
"""
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "oracle"))

from core import OHLCV, PatternStage, WBottomDetector, registry  # noqa: E402
import test_v4_migration_parity as P  # noqa: E402  复用同一组数据集

PIVOT_IDS = ("w_bottom", "m_top", "hs_bottom", "hs_top")


def _fixture(name):
    with open(HERE / "fixtures" / name, newline="", encoding="utf-8") as f:
        return [OHLCV(r["date"], float(r["open"]), float(r["high"]), float(r["low"]),
                      float(r["close"]), float(r["volume"])) for r in csv.DictReader(f)]


# ------------------------------------------------------------------ 真实案例（FIR-36 原始报告）

def test_002436_w_bottom_back_below_neckline_is_flagged():
    kl = _fixture("002436_qfq_20261013.csv")[-250:]
    (r,) = [r for r in WBottomDetector().detect(kl) if r.key_points[0].date == "2026-08-25"]
    assert r.neckline_price == 37.8
    assert r.stage == PatternStage.POST_BREAK          # 阶段不变（未满 3 日）
    e = r.evidence
    assert e["breakout_occurred"] is True and e["breakout_date"] == "2026-09-08"
    assert e["breakout_close"] == 40.12 and r.breakout_price == 40.12   # 以前为 None
    assert e["back_inside_neckline"] is True and e["days_back_inside"] == 2
    assert [k.close for k in kl[-2:]] == [35.37, 36.19]
    assert e["close_beyond_breakout_line"] is False
    assert e["breakout_line"] == round(37.8 * 1.01, 4)


# ------------------------------------------------------------------ 合成序列：逐日状态

def _days(n):
    d, out = date(2026, 1, 5), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _w(tail):
    """标准 W 底：两底 10.0 / 10.1，颈线 12.0，右底后接 tail 收盘序列"""
    path = ([13.0 - i * 0.3 for i in range(10)] + [10.0] +
            [10.0 + i * 0.4 for i in range(1, 5)] + [12.0] +
            [12.0 - i * 0.38 for i in range(1, 5)] + [10.1] +
            [10.1 + i * 0.3 for i in range(1, 6)])
    closes = [round(c, 4) for c in path + list(tail)]
    ds = _days(len(closes))
    return [OHLCV(d, c, round(c + 0.05, 4), round(c - 0.05, 4), c, 1e5) for d, c in zip(ds, closes)]


def _wres(tail):
    res = WBottomDetector().detect(_w(tail))
    assert len(res) == 1, res
    return res[0]


def test_synthetic_w_shape_is_detected_before_breakout():
    r = _wres([11.7, 11.8, 11.9])
    assert r.evidence["breakout_occurred"] is False
    assert r.breakout_price is None and r.evidence["back_inside_neckline"] is None
    assert r.evidence["days_back_inside"] is None and r.evidence["breakout_date"] is None


def test_state_sequence_after_breakout():
    up = [11.7, 11.8, 12.5, 13.0, 13.2]       # 第 3 根首次越过 12.0×1.01
    seq = []
    for k in range(2, 9):
        tail = (up + [11.8, 11.7, 11.6, 11.5])[:k]
        r = _wres(tail)
        seq.append((r.stage, r.breakout_price, r.evidence["back_inside_neckline"], r.evidence["days_back_inside"]))
    assert seq[0] == (PatternStage.PRE_BREAK, None, None, None)
    assert seq[1] == (PatternStage.BREAKOUT_DAY, 12.5, False, 0)
    assert seq[3] == (PatternStage.POST_BREAK, 12.5, False, 0)            # 价格仍在颈线上方
    # 跌回颈线下方 1、2 日：阶段不变，证据标注
    assert seq[4][2:] == (True, 1) and seq[4][0] != PatternStage.FAILED_BREAK
    assert seq[5][2:] == (True, 2) and seq[5][0] != PatternStage.FAILED_BREAK
    assert seq[6] == (PatternStage.FAILED_BREAK, 12.5, True, 3)             # 第 3 日才判失败
    assert all(s[1] == 12.5 for s in seq[1:])                              # 突破日收盘价，不随当前价变化


def test_three_days_back_inside_is_failed_break():
    r = _wres([11.7, 11.8, 12.5, 13.0, 13.2, 11.8, 11.7, 11.6])
    assert r.stage == PatternStage.FAILED_BREAK
    assert r.evidence["days_back_inside"] == 3 and r.evidence["failed_break_days"] == 3
    assert r.breakout_price == 12.5


# ------------------------------------------------------------------ 全数据集不变量

@pytest.mark.parametrize("name", sorted(P.DATASETS))
def test_invariants_every_prefix(name):
    rows = P.DATASETS[name]
    for n in range(60, len(rows) + 1, 3):
        kl = P._v5_klines(rows[:n])
        by_date = {k.date: (i, k) for i, k in enumerate(kl)}
        for pid in PIVOT_IDS:
            for r in registry.get_pattern(pid).detect(kl):
                e = r.evidence
                assert r.breakout_price == e["breakout_close"]
                if not e["breakout_occurred"]:
                    assert r.stage in (PatternStage.FORMING, PatternStage.PRE_BREAK)
                    assert e["breakout_date"] is e["back_inside_neckline"] is e["days_back_inside"] is None
                    continue
                i, k = by_date[e["breakout_date"]]
                assert round(k.close, 4) == e["breakout_close"]
                assert e["bars_since_breakout"] == n - 1 - i
                assert e["back_inside_neckline"] == (e["days_back_inside"] > 0)
                assert (r.stage == PatternStage.FAILED_BREAK) == (e["days_back_inside"] >= 3)
                if r.stage == PatternStage.BREAKOUT_DAY:
                    assert e["bars_since_breakout"] == 0 and e["days_back_inside"] == 0
