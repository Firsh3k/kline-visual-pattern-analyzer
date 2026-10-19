"""
PR #67 QA 阻断项（Python 侧）：

R67-1 信号层（core/signals.py）：最新实例、确认判定、主导形态都由 Python 给出，且不进入 core.result_sha256。
R67-2 线段：头肩倾斜颈线从左颈线点到最后一根，必经右颈线点；W/M、平台为水平线。
R67-3 日期合同：未来 / 不存在的 asOf 在取行情前拒绝（个股、大盘、python_analyzer 同一口径）；
      已核实日历年份内的休市日 K 线拒绝，未核实年份不判断。
"""
import copy
import csv
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import analysis as A  # noqa: E402
from core import signals as S  # noqa: E402

NOW = datetime(2026, 10, 19, 12, 40, tzinfo=A.CN_TZ)
FIX = ROOT / "tests" / "v5" / "fixtures" / "002436_qfq_20261013.csv"
IDX = ROOT / "tests" / "v5" / "fixtures" / "sh000001_20261013.csv"


def _rows(path, yyyymmdd=False):
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        d = r["date"]
        if yyyymmdd:
            d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        out.append({"day": d, **{k: float(r[k]) for k in ("open", "high", "low", "close", "volume")}})
    return out


def _doc(code="002436", rows=None, **kw):
    return A.analyze(code, rows if rows is not None else _rows(FIX), adjustment="qfq",
                     provenance={"source": "fixture"}, now=NOW, **kw)


# ------------------------------------------------------------------ R67-3 日期合同

@pytest.mark.parametrize("bad", ["2026-10-20", "2027-01-04", "2026-02-30", "2026-13-01", "20261013", "2026-1-5"])
def test_future_or_invalid_as_of_rejected(bad):
    with pytest.raises(A.InvalidInputError):
        _doc(as_of=bad)


def test_as_of_today_and_past_accepted():
    assert _doc(as_of="2026-10-19")["as_of"] == "2026-10-13"   # fixture 最后一根
    assert _doc(as_of="2026-10-09")["as_of"] == "2026-10-09"
    assert _doc(as_of="2026-10-05")["as_of"] == "2026-09-30"   # 国庆休市：截取到节前最后一个交易日


def test_as_of_uses_shanghai_date():
    late_utc = datetime(2026, 10, 19, 17, 0, tzinfo=A.timezone.utc)   # 北京时间 10-20 01:00
    assert A.parse_as_of("2026-10-20", late_utc) == "2026-10-20"
    with pytest.raises(A.InvalidInputError):
        A.parse_as_of("2026-10-20", NOW)


def _insert(rows, day):
    """按序插入一根 OHLCV 合法的 K 线（复制前一根）"""
    out = []
    for r in rows:
        if out and out[-1]["day"] < day < r["day"]:
            out.append({**out[-1], "day": day})
        out.append(r)
    assert any(r["day"] == day for r in out)
    return out


def test_holiday_bar_rejected_in_verified_year():
    rows = _insert(_rows(FIX), "2026-10-07")            # 国庆休市日（QA 反例）
    with pytest.raises(A.InvalidInputError, match="休市日"):
        _doc(rows=rows)
    weekend = _insert(_rows(FIX), "2026-09-27")         # 周日
    with pytest.raises(A.InvalidInputError, match="休市日"):
        _doc(rows=weekend)


def test_holiday_bar_rejected_for_market_index():
    rows = _insert(_rows(IDX), "2026-10-07")
    with pytest.raises(A.InvalidInputError):
        A.analyze_market(rows)
    with pytest.raises(A.InvalidInputError):
        A.analyze_market(_rows(IDX), as_of="2026-10-20", now=NOW)


def test_unverified_year_is_not_calendar_checked():
    issues = A.validate_bars([{"day": "2019-10-05", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])
    assert issues == []   # 2019 不在已核实年份：不判休市（calendar.status 会是 unverified）
    issues = A.validate_bars([{"day": "2026-10-05", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])
    assert issues and issues[0]["reason"].startswith("休市日")


@pytest.fixture()
def analyzer():
    spec = importlib.util.spec_from_file_location("ab_python_analyzer_r67", ROOT / "v6" / "app-builder" / "python_analyzer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("bad", ["2099-01-02", "2026-02-30"])
def test_python_analyzer_rejects_before_fetching(analyzer, monkeypatch, bad):
    calls = []
    monkeypatch.setattr(analyzer, "_fetch_series", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("不应取行情")))
    with pytest.raises(A.InvalidInputError):
        analyzer.analyze_stock("002436", as_of=bad)
    with pytest.raises(A.InvalidInputError):
        analyzer.analyze_market(as_of=bad)
    assert calls == []


# ------------------------------------------------------------------ R67-1 信号层

def _inst(stage="post_break", **ev):
    base = {"breakout_occurred": True, "back_inside_neckline": False, "days_back_inside": 0,
            "volume_confirmed": True, "close_beyond_breakout_line": True}
    base.update(ev)
    return {"pattern_id": "w_bottom", "direction": "bull", "category": "price_pattern", "stage": stage,
            "score": 80, "key_points": [{"name": "颈线点", "index": 3, "price": 10.0}], "evidence": base,
            "neckline_price": 10.0}


@pytest.mark.parametrize("stage,ev,confirmed,codes", [
    ("post_break", {}, True, []),
    ("breakout_day", {}, True, []),
    ("retest", {}, True, []),
    # QA 复现：阶段 post_break，但缩量且已回到颈线内侧 2 日 → 未确认
    ("post_break", {"volume_confirmed": False, "back_inside_neckline": True, "days_back_inside": 2,
                    "close_beyond_breakout_line": False}, False, ["back_inside_neckline", "volume_not_confirmed"]),
    ("post_break", {"volume_confirmed": False}, False, ["volume_not_confirmed"]),
    ("pre_break", {"breakout_occurred": False, "back_inside_neckline": None}, False, ["stage_not_active", "no_breakout"]),
    ("failed_break", {"back_inside_neckline": True, "days_back_inside": 3}, False, ["stage_not_active", "back_inside_neckline"]),
])
def test_neckline_confirm_rule(stage, ev, confirmed, codes):
    r = S._confirm("w_bottom", _inst(stage, **ev))
    assert r["confirmed"] is confirmed
    assert [x["code"] for x in r["reasons"]] == codes


@pytest.mark.parametrize("stage,broke,vol,confirmed", [
    ("post_break", True, True, True),     # 放量突破平台：突破确认
    ("breakout_day", True, True, True),
    ("post_break", True, False, False),   # 缩量突破平台：需确认
    ("post_break", False, True, False),   # 平台突破后上涨中（当日已不在判定线外）
    ("retest", False, False, False),      # 回踩确认中
    ("pre_break", False, True, False),
])
def test_platform_confirm_rule(stage, broke, vol, confirmed):
    inst = {"pattern_id": "platform_break", "direction": "bull", "category": "price_pattern", "stage": stage,
            "evidence": {"broke": broke, "volume_confirmed": vol}}
    assert S._confirm("platform_break", inst)["confirmed"] is confirmed


def test_trend_state_never_confirmed():
    inst = {"pattern_id": "ma_bull", "direction": "bull", "category": "trend_state", "stage": "post_break", "evidence": {}}
    r = S._confirm("ma_bull", inst)
    assert r["confirmed"] is False and r["reasons"][0]["code"] == "trend_state_only"


def test_latest_instance_tie_break():
    mk = lambda idx: {"key_points": [{"index": i} for i in idx]}
    assert S.latest_instance([mk([1, 9]), mk([5, 20]), mk([2, 20])]) == 1      # 最大 index 相同 → 最小 index 大者
    assert S.latest_instance([mk([3, 20]), mk([3, 20])]) == 0                  # 完全相同 → 检测器顺序靠前
    assert S.latest_instance([]) is None


def test_unknown_stage_or_direction_fails_closed():
    doc = _doc()
    pats = copy.deepcopy(doc["core"]["patterns"])
    i = doc["signals"]["latest"]["w_bottom"]["instance_index"]
    pats["w_bottom"][i]["stage"] = "future_new_stage"
    with pytest.raises(S.SignalSchemaError):
        S.build_signals(pats, doc["bars"], A.PATTERN_ORDER)
    pats = copy.deepcopy(doc["core"]["patterns"])
    pats["w_bottom"][i]["direction"] = "sideways"
    with pytest.raises(S.SignalSchemaError):
        S.build_signals(pats, doc["bars"], A.PATTERN_ORDER)


def test_real_002436_is_post_break_but_not_confirmed():
    """QA 指出的真实情形：002436（2026-10-13 收盘）W 底 post_break，缩量 + 回到颈线内侧 2 日"""
    doc = _doc()
    w = doc["signals"]["latest"]["w_bottom"]
    assert (w["stage"], w["active"], w["confirmed"]) == ("post_break", True, False)
    assert {r["code"] for r in w["reasons"]} == {"back_inside_neckline", "volume_not_confirmed"}
    assert doc["signals"]["dominant"]["bull"] is None
    assert doc["signals"]["dominant"]["overall"] == {"pattern_id": "w_bottom", "direction": "bull", "confirmed": False}


def test_dominant_prefers_confirmed_then_score():
    doc = _doc()
    pats = copy.deepcopy(doc["core"]["patterns"])
    i = doc["signals"]["latest"]["w_bottom"]["instance_index"]
    pats["w_bottom"][i]["evidence"].update(volume_confirmed=True, back_inside_neckline=False, days_back_inside=0)
    sig = S.build_signals(pats, doc["bars"], A.PATTERN_ORDER)
    assert sig["dominant"]["bull"] == "w_bottom"
    assert sig["dominant"]["overall"] == {"pattern_id": "w_bottom", "direction": "bull", "confirmed": True}
    assert sig["dominant"]["structure"]["bull"]["confirmed"] is True


def test_signals_outside_core_hash_and_deterministic():
    a, b = _doc(), _doc()
    assert a["signals"] == b["signals"] and a["signals"]["signals_sha256"] == b["signals"]["signals_sha256"]
    body = {k: v for k, v in a["core"].items() if k != "result_sha256"}
    assert a["core"]["result_sha256"] == A.sha256_json(body)
    assert "signals" not in a["core"]
    assert a["signals"]["rule_version"] == S.SIGNAL_RULE_VERSION


def test_summary_uses_signals():
    doc = _doc()
    s = A.pattern_summary(doc)
    assert s["dominant"] == doc["signals"]["dominant"]
    assert s["signal_rule_version"] == S.SIGNAL_RULE_VERSION
    for pid, p in s["patterns_latest"].items():
        sig = doc["signals"]["latest"][pid]
        assert (p["stage"], p["confirmed"], p["active"]) == (sig["stage"], sig["confirmed"], sig["active"])


def test_core_indicator_series_cover_page_indicators():
    doc = _doc()
    n = len(doc["bars"])
    for k in A.INDICATOR_SERIES:
        assert len(doc["core"]["indicators"]["series"][k]) == n, k


# ------------------------------------------------------------------ R67-2 线段

def _hs_cases():
    docs = [_doc(), _doc("002594", _rows(ROOT / "data" / "002594.csv", yyyymmdd=True))]
    for d in docs:
        for pid in ("hs_bottom", "hs_top"):
            for r in d["core"]["patterns"][pid]:
                yield d, pid, r


def test_hs_segment_passes_through_right_neck_point():
    seen = 0
    for d, pid, r in _hs_cases():
        line = S._lines(pid, r, d["bars"])[0]
        kp = {k["name"]: k for k in r["key_points"]}
        ln, rn = kp["左颈线点"], kp["右颈线点"]
        assert (line["idx1"], line["price1"]) == (ln["index"], round(ln["price"], 4))
        assert (line["idx2"], line["price2"]) == (len(d["bars"]) - 1, round(r["neckline_price"], 4))
        at = line["price1"] + (line["price2"] - line["price1"]) * (rn["index"] - line["idx1"]) / (line["idx2"] - line["idx1"])
        assert abs(at - rn["price"]) < 0.01, (pid, at, rn["price"])
        seen += 1
    assert seen > 0


def test_002594_hs_bottom_segment_matches_qa_numbers():
    d = _doc("002594", _rows(ROOT / "data" / "002594.csv", yyyymmdd=True))
    line = d["signals"]["latest"]["hs_bottom"]["lines"][0]
    assert (line["idx1"], line["price1"], line["idx2"], line["price2"]) == (14, 99.14, 99, 92.765)


def test_w_and_platform_segments_horizontal():
    d = _doc("002594", _rows(ROOT / "data" / "002594.csv", yyyymmdd=True))
    for pid in ("w_bottom", "platform_break"):
        line = d["signals"]["latest"][pid]["lines"][0]
        assert line["price1"] == line["price2"] and line["idx2"] == len(d["bars"]) - 1
    trend = [v for k, v in d["signals"]["latest"].items() if k in ("ma_bull", "ma_bear") and v]
    assert trend and all(v["lines"] == [] for v in trend)
