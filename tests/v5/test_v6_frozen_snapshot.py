"""FIR-40 问题 11：冻结历史快照比对（生成逻辑与更新方法见 tests/v6/frozen_snapshot.py）"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("v6_frozen_snapshot", ROOT / "tests" / "v6" / "frozen_snapshot.py")
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)

EXPECTED = json.loads(F.SNAPSHOT.read_text(encoding="utf-8"))
ACTUAL = F.build()
SAME_PY = ACTUAL["python"] == EXPECTED["python"]


def _drop_hash(entry):
    """非线上 Python 版本：result_sha256 末位浮点可能不同，跳过这一项（其余必须一致）"""
    if SAME_PY:
        return entry
    e = json.loads(json.dumps(entry))
    e.pop("result_sha256", None)
    e["summary"].pop("result_sha256", None)
    return e


HINT = "算法 / 信号规则 / 数据合同发生变化。若为经 QA 同意的有意变更，运行 python3 tests/v6/frozen_snapshot.py --write 并在 PR 说明差异"


def test_snapshot_cases_unchanged():
    assert sorted(ACTUAL["stocks"]) == sorted(EXPECTED["stocks"])
    assert sorted(ACTUAL["market"]) == sorted(EXPECTED["market"])
    assert ACTUAL["schema_version"] == EXPECTED["schema_version"], HINT
    assert EXPECTED["python"] == "3.10", "快照必须用线上同版本 Python 3.10 生成"


@pytest.mark.parametrize("name", sorted(EXPECTED["stocks"]))
def test_stock_matches_frozen_snapshot(name):
    exp, act = EXPECTED["stocks"][name], ACTUAL["stocks"][name]
    # 先比关键字段，失败信息更好读
    for k in ("as_of", "bar_count", "data_hash", "session_state", "calendar_status"):
        assert act[k] == exp[k], f"{name}.{k}: {act[k]!r} != {exp[k]!r}（{HINT}）"
    assert act["signals"]["dominant"] == exp["signals"]["dominant"], f"{name} 主导形态变化（{HINT}）"
    for pid, s in exp["signals"]["latest"].items():
        a = act["signals"]["latest"][pid]
        brief = lambda x: x and (x["stage"], x["score"], x["confirmed"], x["last_key_date"])  # noqa: E731
        assert brief(a) == brief(s), f"{name}.{pid}: {brief(a)} != {brief(s)}（{HINT}）"
    if SAME_PY:
        assert act["result_sha256"] == exp["result_sha256"], f"{name} 检测器原始输出变化（{HINT}）"
    assert _drop_hash(act) == _drop_hash(exp), f"{name} 其余字段变化（{HINT}）"


@pytest.mark.parametrize("name", sorted(EXPECTED["market"]))
def test_market_matches_frozen_snapshot(name):
    assert ACTUAL["market"][name] == EXPECTED["market"][name], HINT
