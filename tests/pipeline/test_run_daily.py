"""
管线一致性测试：保证前端读到的 JSON 与 V5 core 直接计算的结果完全一致。

检查项：
1. 输出的 bars 与输入 CSV（最近 250 根）逐根、逐字段完全相同
2. 8 种形态、摆动点、指标、量价、ATR 止损 == 直接调用 V5 core 的结果
3. 每个关键点、每个 pivot 的 index/date 指向真实K线；pivot 价格等于该K线的 high/low
4. 大盘环境 == 直接调用 ClassicMarketRegimeAnalyzer 的结果
5. 结果哈希与内容一致；重复运行、不同 PYTHONHASHSEED 下输出逐字节相同（确定性）
"""
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipeline"))

import run_daily  # noqa: E402
from core import (ClassicIndicators, ClassicMarketRegimeAnalyzer, SwingPivotEngine,  # noqa: E402
                  registry, volume_status, atr_stop)

CSV_DIR = ROOT / "data" / "v5_validation"
INDEX_CSV = ROOT / "tests" / "v5" / "fixtures" / "sh000001_20261013.csv"
CODES = sorted(p.stem for p in CSV_DIR.glob("*.csv"))


def _run(out: Path, seed: str = "0") -> None:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    subprocess.run([sys.executable, str(ROOT / "pipeline" / "run_daily.py"),
                    "--csv-dir", str(CSV_DIR), "--index-csv", str(INDEX_CSV), "--out", str(out)],
                   check=True, env=env, capture_output=True)


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("out")
    _run(d)
    return d


def _doc(out, code):
    return json.loads((out / "stocks" / f"{code}.json").read_text(encoding="utf-8"))


def _kl(code):
    return run_daily.to_klines(run_daily.load_csv(CSV_DIR / f"{code}.csv")[-run_daily.WINDOW:])


def _rt(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False))


@pytest.mark.parametrize("code", CODES)
def test_bars_identical_to_source(out, code):
    doc = _doc(out, code)
    with open(CSV_DIR / f"{code}.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))[-run_daily.WINDOW:]
    assert len(doc["bars"]) == len(rows)
    for b, r in zip(doc["bars"], rows):
        assert b[0] == run_daily._norm_date(r["date"])
        assert b[1:] == [float(r[k]) for k in ("open", "high", "low", "close", "volume")]


@pytest.mark.parametrize("code", CODES)
def test_core_matches_direct_calls(out, code):
    doc, kl = _doc(out, code), _kl(code)
    c = doc["core"]
    assert c["pivots"] == _rt([p.to_dict() for p in SwingPivotEngine(5, 5).detect(kl)])
    assert list(c["patterns"]) == run_daily.PATTERN_ORDER
    for pid in run_daily.PATTERN_ORDER:
        assert c["patterns"][pid] == _rt([r.to_dict() for r in registry.get_pattern(pid).detect(kl)]), pid
    ind = ClassicIndicators().calculate(kl)
    assert c["indicators"]["latest"] == _rt({k: v[-1] for k, v in ind.items()})
    for k in run_daily.INDICATOR_SERIES:
        assert c["indicators"]["series"][k] == _rt(ind[k])
    assert c["indicators"]["volume"] == volume_status(kl, ind)
    assert c["indicators"]["atr_stop"] == _rt(atr_stop(kl, ind))
    # FIR-37：口径说明与对照值
    from core import ReferenceIndicators, INDICATOR_CONVENTIONS
    ref = ReferenceIndicators().calculate(kl)
    assert c["indicators"]["conventions"] == _rt(INDICATOR_CONVENTIONS)
    assert c["indicators"]["reference"]["series"] == _rt(ref)
    assert c["indicators"]["reference"]["latest"] == _rt({k: v[-1] for k, v in ref.items()})
    body = {k: v for k, v in c.items() if k != "result_sha256"}
    assert c["result_sha256"] == run_daily._sha256(body)


@pytest.mark.parametrize("code", CODES)
def test_points_anchor_to_real_bars(out, code):
    doc = _doc(out, code)
    bars = doc["bars"]
    for p in doc["core"]["pivots"]:
        b = bars[p["index"]]
        assert b[0] == p["date"]
        assert p["price"] == (b[2] if p["pivot_type"] == "swing_high" else b[3])
    for results in doc["core"]["patterns"].values():
        for r in results:
            for kp in r["key_points"]:
                assert 0 <= kp["index"] < len(bars)
                assert bars[kp["index"]][0] == kp["date"]


def test_market_env_matches_direct_call(out):
    idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
    kl = run_daily.to_klines(run_daily.load_csv(INDEX_CSV)[-run_daily.WINDOW:])
    assert idx["market_env"] == _rt(ClassicMarketRegimeAnalyzer().analyze_dict(kl))


def test_index_summary_consistent(out):
    idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert [s["code"] for s in idx["stocks"]] == CODES
    for s in idx["stocks"]:
        doc = _doc(out, s["code"])
        assert s["close"] == doc["bars"][-1][4]
        for pid, lt in s["patterns_latest"].items():
            assert any(r["score"] == lt["score"] and r["stage"] == lt["stage"] and r["category"] == lt["category"]
                       for r in doc["core"]["patterns"][pid])


def test_deterministic_across_runs_and_hash_seeds(out, tmp_path):
    other = tmp_path / "again"
    _run(other, seed="12345")
    for code in CODES:
        assert (out / "stocks" / f"{code}.json").read_bytes() == (other / "stocks" / f"{code}.json").read_bytes(), code
