"""FIR-40 问题 11：冻结历史快照比对（生成逻辑与更新方法见 tests/v6/frozen_snapshot.py）"""
import importlib.util
import json
import re
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


HINT = (f"算法 / 信号规则 / 数据合同发生变化。若为经 QA 同意的有意变更，运行 python{F.PROD_PYTHON} "
        "tests/v6/frozen_snapshot.py --write 并在 PR 说明差异")


def test_snapshot_cases_unchanged():
    assert sorted(ACTUAL["stocks"]) == sorted(EXPECTED["stocks"])
    assert sorted(ACTUAL["market"]) == sorted(EXPECTED["market"])
    assert ACTUAL["schema_version"] == EXPECTED["schema_version"], HINT
    assert EXPECTED["python"] == F.PROD_PYTHON, f"快照必须用线上同版本 Python {F.PROD_PYTHON} 生成"


def test_ci_runs_snapshot_on_production_python():
    """FIR-45：CI 的 python-tests 必须用线上同版本 Python 跑，result_sha256 才真的被核对（否则一直被跳过）"""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci.split("\n  python-tests:", 1)[1].split("\n  js-syntax-check:", 1)[0]
    versions = re.findall(r"python-version:\s*'([0-9.]+)'", job)
    assert versions == [F.PROD_PYTHON], f"python-tests 的 Python 版本 {versions} 应为线上的 {F.PROD_PYTHON}"
    pkg = ci.split("\n  platform-package:", 1)[1].split("\n  cross-runtime-parity:", 1)[0]
    assert re.findall(r"python-version:\s*'([0-9.]+)'", pkg) == [F.PROD_PYTHON], "platform-package 应与线上同版本"
    readme = (ROOT / "v6" / "app-builder" / "README.md").read_text(encoding="utf-8")
    assert f"| Python | `{F.PROD_PYTHON}." in readme, "README 的平台 Python 版本与 PROD_PYTHON 不一致"


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
