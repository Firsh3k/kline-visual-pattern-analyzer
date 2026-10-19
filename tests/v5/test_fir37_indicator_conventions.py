"""
FIR-37：指标口径（PO 2026-10-14：保留现有口径并注明，另给 Wilder RSI / Wilder ATR / 样本标准差 BOLL 对照值）

- ClassicIndicators 数值不变（仍与冻结的 V4 逐根相等，见 test_v4_migration_parity.py）；
- ReferenceIndicators 与独立的 pandas 实现逐根比对（容差 1e-9）；
- 对照值不参与任何形态 / 信号 / 评分（core 内除 pipeline 输出外无引用）。
"""
import csv
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from core import OHLCV, ClassicIndicators, ReferenceIndicators, INDICATOR_CONVENTIONS, REFERENCE_SERIES  # noqa: E402


def _fixture(name):
    with open(HERE / "fixtures" / name, newline="", encoding="utf-8") as f:
        return [OHLCV(r["date"], float(r["open"]), float(r["high"]), float(r["low"]),
                      float(r["close"]), float(r["volume"])) for r in csv.DictReader(f)]


FIXTURES = ["002436_qfq_20261013.csv", "sh000001_20261013.csv"]


def _pandas_reference(kl):
    df = pd.DataFrame({"h": [k.high for k in kl], "l": [k.low for k in kl], "c": [k.close for k in kl]})
    d = df.c.diff()
    up, dn = d.clip(lower=0), (-d).clip(lower=0)
    # Wilder：首值 = 前 14 个变化的简单平均（索引 14），之后 alpha=1/14 递推
    def wilder(x, first):
        out = [math.nan] * len(x)
        out[first] = x.iloc[first - 13:first + 1].mean()
        for i in range(first + 1, len(x)):
            out[i] = (out[i - 1] * 13 + x.iloc[i]) / 14
        return pd.Series(out)
    ag, al = wilder(up, 14), wilder(dn, 14)
    rsi = 100 - 100 / (1 + ag / al)
    rsi[(al == 0) & (ag > 0)] = 100.0
    rsi[(al == 0) & (ag == 0)] = 50.0
    tr = pd.concat([df.h - df.l, (df.h - df.c.shift()).abs(), (df.l - df.c.shift()).abs()], axis=1).max(axis=1)
    tr.iloc[0] = math.nan
    atr = wilder(tr, 14)
    mid, sd = df.c.rolling(20).mean(), df.c.rolling(20).std(ddof=1)
    return {"rsi_wilder14": rsi, "atr_wilder14": atr, "boll_up_sample20": mid + 2 * sd, "boll_low_sample20": mid - 2 * sd}


@pytest.mark.parametrize("name", FIXTURES)
def test_reference_matches_independent_pandas(name):
    kl = _fixture(name)
    mine, ref = ReferenceIndicators().calculate(kl), _pandas_reference(kl)
    assert set(mine) == set(REFERENCE_SERIES)
    for k in REFERENCE_SERIES:
        for i, (a, b) in enumerate(zip(mine[k], ref[k])):
            if a is None:
                assert math.isnan(b), (k, i)
            else:
                assert abs(a - b) < 1e-9, (k, i, a, b)


def test_null_prefix_lengths():
    kl = _fixture(FIXTURES[0])
    r = ReferenceIndicators().calculate(kl)
    assert r["rsi_wilder14"][13] is None and r["rsi_wilder14"][14] is not None
    assert r["atr_wilder14"][13] is None and r["atr_wilder14"][14] is not None
    assert r["boll_up_sample20"][18] is None and r["boll_up_sample20"][19] is not None


def test_sample_boll_is_wider_than_population_by_sqrt_20_over_19():
    kl = _fixture(FIXTURES[0])
    c, r = ClassicIndicators().calculate(kl), ReferenceIndicators().calculate(kl)
    for i in range(19, len(kl)):
        half_pop = c["boll_up"][i] - c["boll_mid"][i]
        half_smp = r["boll_up_sample20"][i] - c["boll_mid"][i]
        assert abs(half_smp - half_pop * math.sqrt(20 / 19)) < 1e-9


def test_flat_series_edge_cases():
    kl = [OHLCV(f"2026-01-{i+1:02d}", 10.0, 10.0, 10.0, 10.0, 1.0) for i in range(30)]
    r = ReferenceIndicators().calculate(kl)
    assert r["rsi_wilder14"][-1] == 50.0 and r["atr_wilder14"][-1] == 0.0
    assert r["boll_up_sample20"][-1] == r["boll_low_sample20"][-1] == 10.0
    up = [OHLCV(f"2026-01-{i+1:02d}", 10.0 + i, 10.0 + i, 10.0 + i, 10.0 + i, 1.0) for i in range(30)]
    assert ReferenceIndicators().calculate(up)["rsi_wilder14"][-1] == 100.0
    assert ReferenceIndicators().calculate(kl[:14]) == {k: [None] * 14 for k in REFERENCE_SERIES}


# 冻结值：RSI 两种口径相差约 2.6；ATR 经 250 根递推后两种口径已收敛（初值影响衰减为 (13/14)^236）；
# 样本标准差使 BOLL 半宽放大 sqrt(20/19) ≈ 1.026 倍
FROZEN_002436 = {
    "rsi": 39.3428, "atr": 3.0669, "boll_up": 46.0264, "boll_low": 35.8676,
    "rsi_wilder14": 41.9739, "atr_wilder14": 3.0669, "boll_up_sample20": 46.1584, "boll_low_sample20": 35.7356,
}


def test_frozen_002436_latest_values():
    """002436 @ 2026-10-13（前复权，250 根窗口）：现行口径与对照口径并列，供 UI 与同花顺对照（QA Q6）"""
    kl = _fixture(FIXTURES[0])[-250:]
    c, r = ClassicIndicators().calculate(kl), ReferenceIndicators().calculate(kl)
    got = {k: round(c[k][-1], 4) for k in ("rsi", "atr", "boll_up", "boll_low")}
    got.update({k: round(r[k][-1], 4) for k in REFERENCE_SERIES})
    assert got == FROZEN_002436, got


def test_conventions_are_documented_for_every_output():
    ind_keys = set(ClassicIndicators().calculate(_fixture(FIXTURES[0])[:80]))
    groups = {"ma": ("ma5", "ma10", "ma20", "ma60"), "macd": ("dif", "dea", "macd"), "rsi": ("rsi",),
              "kdj": ("kdj_k", "kdj_d", "kdj_j"), "boll": ("boll_mid", "boll_up", "boll_low", "boll_width"),
              "atr": ("atr",)}
    covered = {k for g in groups.values() for k in g} | {"vma5", "vma20"}
    assert ind_keys == covered
    assert set(groups) | {"rsi_wilder14", "atr_wilder14", "boll_sample20"} == set(INDICATOR_CONVENTIONS)
    for v in INDICATOR_CONVENTIONS.values():
        assert v["label"] and v["convention"]


def test_reference_values_not_used_by_any_detector():
    for p in (ROOT / "core").rglob("*.py"):
        if p.name in ("indicators.py", "__init__.py") or p.relative_to(ROOT).as_posix() == "core/analysis.py":
            continue   # analysis.py 只把对照值原样放入输出（见 test_core_analysis）
        t = p.read_text(encoding="utf-8")
        assert "ReferenceIndicators" not in t and "_wilder14" not in t and "_sample20" not in t, p
