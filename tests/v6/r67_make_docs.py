"""
R67 前端测试用的真实 doc 生成器：用仓库内的真实行情 fixture 跑 core.analysis.analyze()，
再按 QA 复现步骤构造反例；反例的 signals / summary 一律由 Python 信号层重新计算（模拟 Python 的真实输出），
前端测试只消费这里的输出。仅依赖标准库，CI 的 Node job 直接调用 python3。

输出（stdout JSON）：{case_name: doc, ...}
"""
import copy
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import analysis as A  # noqa: E402
from core.signals import build_signals  # noqa: E402

NOW = datetime(2026, 10, 19, 12, 40, tzinfo=A.CN_TZ)


def _rows(path, yyyymmdd=False):
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        d = r["date"]
        if yyyymmdd:
            d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        out.append({"day": d, **{k: float(r[k]) for k in ("open", "high", "low", "close", "volume")}})
    return out


def _finish(doc):
    doc["signals"] = json.loads(json.dumps(build_signals(doc["core"]["patterns"], doc["bars"], A.PATTERN_ORDER)))
    doc["summary"] = A.pattern_summary(doc)
    doc["engine"]["analyzer"] = "real_core"
    return doc


def _doc(code, rows):
    return _finish(A.analyze(code, rows, adjustment="qfq", provenance={"source": "fixture"}, now=NOW))


def _with_w_bottom(base, evidence):
    doc = copy.deepcopy(base)
    i = doc["signals"]["latest"]["w_bottom"]["instance_index"]
    inst = doc["core"]["patterns"]["w_bottom"][i]
    inst["stage"] = "post_break"
    inst["evidence"].update(evidence)
    # 其它价格形态降为 forming，保证主导就是这只 W 底（与 QA 复现一致）
    for pid in ("m_top", "hs_bottom", "hs_top", "platform_break", "platform_break_down"):
        for r in doc["core"]["patterns"][pid]:
            r["stage"] = "forming"
    return _finish(doc)


def main():
    base = _doc("002436", _rows(ROOT / "tests/v5/fixtures/002436_qfq_20261013.csv"))
    hs = _doc("002594", _rows(ROOT / "data/002594.csv", yyyymmdd=True))
    cases = {
        "base_002436": base,
        "hs_002594": hs,
        # QA R67-1 复现：post_break，但缩量 + 已回到颈线内侧 2 日
        "w_post_break_unconfirmed": _with_w_bottom(base, {
            "volume_confirmed": False, "back_inside_neckline": True, "days_back_inside": 2,
            "close_beyond_breakout_line": False, "breakout_occurred": True}),
        "w_post_break_confirmed": _with_w_bottom(base, {
            "volume_confirmed": True, "back_inside_neckline": False, "days_back_inside": 0,
            "close_beyond_breakout_line": True, "breakout_occurred": True}),
    }
    json.dump(cases, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
