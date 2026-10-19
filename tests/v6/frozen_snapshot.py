"""
FIR-40 问题 11：冻结历史快照比对（替代“写死某只股票当日结果”的验收）。

用仓库内冻结的真实行情（固定 K 线 + 固定“当前时间”）跑 core.analysis，结果与已提交的期望快照
tests/v6/snapshots/stage1_frozen.json 逐字段比对。行情每天变也不影响这个基准；
只有算法 / 信号规则 / 数据合同真的变了，比对才会失败。

注意：core.result_sha256 依赖 Python 小版本（3.12 起 sum() 对浮点改用补偿求和，末位会变）。
快照用线上同版本 Python 3.10 生成；其它版本运行时只跳过 result_sha256，其余字段（信号、阶段、分数、
确认、主导、日期、data_hash）必须完全一致。

算法有意变更时（必须经 QA 同意）：
    python3.10 tests/v6/frozen_snapshot.py --write
并在 PR 里说明快照差异。
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import analysis as A  # noqa: E402

SNAPSHOT = ROOT / "tests" / "v6" / "snapshots" / "stage1_frozen.json"
NOW = datetime(2026, 10, 19, 12, 40, tzinfo=A.CN_TZ)   # 固定“当前时间”，session_state / stale 不随运行日期变化

# (用例名, 代码, 行情文件, 日期是否为 YYYYMMDD, asOf)
STOCK_CASES = [
    ("002436_latest", "002436", "tests/v5/fixtures/002436_qfq_20261013.csv", False, None),
    ("002436_asof_20260930", "002436", "tests/v5/fixtures/002436_qfq_20261013.csv", False, "2026-09-30"),
    ("002594_latest", "002594", "data/002594.csv", True, None),
    ("600519_latest", "600519", "data/600519.csv", True, None),
]
MARKET_CASE = ("sh000001_latest", "tests/v5/fixtures/sh000001_20261013.csv")


def _rows(path, yyyymmdd):
    out = []
    with open(ROOT / path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            d = r["date"]
            if yyyymmdd:
                d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            out.append({"day": d, **{k: float(r[k]) for k in ("open", "high", "low", "close", "volume")}})
    return out


def _stock(code, path, yyyymmdd, as_of):
    doc = A.analyze(code, _rows(path, yyyymmdd), adjustment="qfq",
                    provenance={"source": "frozen_fixture", "file": path}, as_of=as_of, now=NOW)
    return {
        "request_as_of": as_of,
        "as_of": doc["as_of"],
        "bar_count": doc["input"]["bar_count"],
        "data_hash": doc["input"]["data_hash"],
        "session_state": doc["session_state"],
        "calendar_status": doc["calendar"]["status"],
        "result_sha256": doc["core"]["result_sha256"],
        "signals": doc["signals"],
        "summary": A.pattern_summary(doc),
    }


def build():
    snap = {"schema_version": A.ANALYSIS_SCHEMA_VERSION, "now": NOW.isoformat(),
            "python": f"{sys.version_info[0]}.{sys.version_info[1]}", "stocks": {}, "market": {}}
    for name, code, path, ymd, as_of in STOCK_CASES:
        snap["stocks"][name] = _stock(code, path, ymd, as_of)
    name, path = MARKET_CASE
    snap["market"][name] = A.analyze_market(_rows(path, False), now=NOW)
    return json.loads(json.dumps(snap, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="重写期望快照（仅限经 QA 同意的算法变更）")
    args = ap.parse_args()
    snap = build()
    if args.write:
        if snap["python"] != "3.10":
            sys.exit(f"请用线上同版本 Python 3.10 生成快照（当前 {snap['python']}）")
        SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"已写入 {SNAPSHOT.relative_to(ROOT)}")
    else:
        json.dump(snap, sys.stdout, ensure_ascii=False, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
