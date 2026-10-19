"""
core.analysis —— 所有入口（管线 / API 个股 / 自选股扫描 / 市场扫描）共用的单一分析实现（FIR-38）

覆盖：严格输入校验、dataHash、asOf point-in-time、交易日历证据与收盘/盘中状态、
与直接调用检测器逐字段一致。
"""
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from core import registry  # noqa: E402
from core import analysis as A  # noqa: E402

CN = A.CN_TZ
NOW = datetime(2026, 10, 14, 16, 0, tzinfo=CN)      # 周三，收盘后
PROV = {"source": "tencent", "mode": "fixture"}


def _bars(name="002436_qfq_20261013.csv"):
    with open(HERE / "fixtures" / name, newline="", encoding="utf-8") as f:
        return [{"day": r["date"], "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
                 "close": float(r["close"]), "volume": float(r["volume"])} for r in csv.DictReader(f)]


def _run(bars=None, **kw):
    kw.setdefault("adjustment", "qfq")
    kw.setdefault("provenance", PROV)
    kw.setdefault("now", NOW)
    return A.analyze("002436", _bars() if bars is None else bars, **kw)


# ------------------------------------------------------------------ 一致性

def test_core_equals_direct_detector_calls():
    doc = _run()
    kl = A.to_klines(_bars()[-250:])
    for pid in A.PATTERN_ORDER:
        direct = json.loads(json.dumps([r.to_dict() for r in registry.get_pattern(pid).detect(kl)], ensure_ascii=False))
        assert doc["core"]["patterns"][pid] == direct, pid
    body = {k: v for k, v in doc["core"].items() if k != "result_sha256"}
    assert doc["core"]["result_sha256"] == A.sha256_json(body)
    assert doc["input"]["bar_count"] == 250 and doc["engine"]["window"] == 250
    json.dumps(doc, allow_nan=False)


def test_deterministic_same_input_same_output():
    a, b = _run(), _run()
    assert a == b
    assert a["input"]["data_hash"] == A.data_hash(_bars()[-250:])


def test_data_hash_changes_with_any_value():
    bars = _bars()
    h0 = A.data_hash(bars[-250:])
    bars[-1] = {**bars[-1], "volume": bars[-1]["volume"] + 1}
    assert A.data_hash(bars[-250:]) != h0


def test_as_of_is_point_in_time():
    full = _bars()
    doc = _run(as_of="2026-09-30")
    trunc = A.analyze("002436", [b for b in full if b["day"] <= "2026-09-30"], adjustment="qfq",
                      provenance=PROV, now=NOW)
    assert doc["as_of"] == "2026-09-30" and doc["request"]["as_of"] == "2026-09-30"
    assert doc["core"] == trunc["core"] and doc["input"]["data_hash"] == trunc["input"]["data_hash"]


def test_summary_is_derived_from_same_doc():
    doc = _run()
    s = A.pattern_summary(doc)
    assert s["data_hash"] == doc["input"]["data_hash"] and s["result_sha256"] == doc["core"]["result_sha256"]
    for pid, lt in s["patterns_latest"].items():
        assert any(r["stage"] == lt["stage"] and r["score"] == lt["score"] for r in doc["core"]["patterns"][pid])
    assert "recommendation" not in json.dumps(s) and "attention" not in json.dumps(s)   # D4：不恢复综合推荐度


# ------------------------------------------------------------------ 严格校验

def _mut(i, **kw):
    bars = _bars()
    bars[i] = {**bars[i], **kw}
    return bars


@pytest.mark.parametrize("bars,field,reason", [
    (_mut(100, high=1.0), "high", "最高价"),
    (_mut(100, low=999.0), "low", "最低价"),
    (_mut(100, volume=-1.0), "volume", "负"),
    (_mut(100, close=math.nan), "close", "有限数"),
    (_mut(100, open=math.inf), "open", "有限数"),
    (_mut(100, close=None), "close", "有限数"),
    (_mut(100, close=0.0), "close", "正数"),
    (_mut(100, close=True), "close", "有限数"),
    (_mut(100, day="2026/01/05"), "day", "格式"),
    (_mut(100, day="2026-02-30"), "day", "不存在"),
])
def test_invalid_values_rejected(bars, field, reason):
    with pytest.raises(A.InvalidInputError) as e:
        _run(bars)
    assert any(i["field"] == field and reason in i["reason"] for i in e.value.issues), e.value.issues


def test_duplicate_and_unordered_dates_rejected():
    bars = _bars()
    with pytest.raises(A.InvalidInputError, match="重复日期"):
        _run(bars[:101] + [dict(bars[100])] + bars[101:])
    swapped = bars[:]
    swapped[100], swapped[101] = swapped[101], swapped[100]
    with pytest.raises(A.InvalidInputError, match="乱序"):
        _run(swapped)


def test_other_request_errors():
    with pytest.raises(A.InvalidInputError, match="6 位数字"):
        A.analyze("2436", _bars(), adjustment="qfq", provenance=PROV, now=NOW)
    with pytest.raises(A.InvalidInputError, match="adjustment"):
        _run(adjustment="hfq")
    with pytest.raises(A.InvalidInputError, match="window"):
        _run(window=120)
    with pytest.raises(A.InvalidInputError, match="不足 60"):
        _run(_bars()[-59:])
    with pytest.raises(A.InvalidInputError, match="晚于当前日期"):
        _run(now=datetime(2026, 10, 12, 16, 0, tzinfo=CN))
    with pytest.raises(A.InvalidInputError, match="asOf"):
        _run(as_of="20260930")


# ------------------------------------------------------------------ 日历 / 收盘状态

def test_close_confirmed_and_not_stale_after_close():
    doc = _run(now=datetime(2026, 10, 13, 15, 30, tzinfo=CN))
    assert doc["session_state"] == "close_confirmed"
    c = doc["calendar"]
    assert c["status"] == "verified" and c["calendar_id"] == "SZSE"
    assert c["expected_trade_date"] == "2026-10-13" and c["stale"] is False and c["as_of_is_trading_day"] is True


def test_intraday_when_last_bar_is_today_before_close():
    doc = _run(now=datetime(2026, 10, 13, 10, 30, tzinfo=CN))
    assert doc["session_state"] == "intraday" and doc["calendar"]["stale"] is False
    assert doc["calendar"]["expected_trade_date"] == "2026-10-12"


def test_stale_when_newer_trading_day_closed():
    doc = _run(now=NOW)   # 10-14 收盘后，最后一根为 10-13
    assert doc["session_state"] == "close_confirmed"
    assert doc["calendar"]["expected_trade_date"] == "2026-10-14" and doc["calendar"]["stale"] is True


def test_weekend_expects_friday():
    bars = [b for b in _bars() if b["day"] <= "2026-10-09"]   # 10-09 周五
    doc = _run(bars, now=datetime(2026, 10, 11, 12, 0, tzinfo=CN))
    assert doc["calendar"]["expected_trade_date"] == "2026-10-09" and doc["calendar"]["stale"] is False


def test_unverified_calendar_never_claims_verified():
    doc = _run(now=datetime(2027, 1, 5, 16, 0, tzinfo=CN))
    c = doc["calendar"]
    assert c["status"] == "unverified" and doc["session_state"] == "unknown"
    assert c["stale"] is None and c["expected_trade_date"] is None and "2027" in c["reason"]


# ------------------------------------------------------------------ 大盘

def test_market_env_validated_and_hashed():
    env = A.analyze_market(_bars("sh000001_20261013.csv"))
    assert env["as_of"] == "2026-10-13" and env["bar_count"] == 250 and len(env["data_hash"]) == 64
    bad = _bars("sh000001_20261013.csv")
    bad[-1] = {**bad[-1], "volume": -1}
    with pytest.raises(A.InvalidInputError):
        A.analyze_market(bad)
