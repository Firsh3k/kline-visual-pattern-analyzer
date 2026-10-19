"""
FIR-40：App Builder 部署层（v6/app-builder）的两条保护：

1. 部署用的 core 副本（v6/app-builder/core）必须与仓库根 core 逐字节一致——CI 只测根 core，
   线上 python_analyzer.py 导入的是副本，两者一旦漂移，线上结果就不再被测试覆盖。
2. 回测（--as-of）取数起点必须从截止日往前推，截止日前要有满 250 根 K 线；
   从“今天”往前推时，回测日期越早窗口越小（实测 as_of=2026-09-01 只剩 248 根，2026-01-05 只剩 88 根）。
"""
import filecmp
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "v6" / "app-builder"
sys.path.insert(0, str(ROOT))


def _diff(dc):
    out = [str(Path(dc.left) / n) for n in dc.left_only + dc.right_only + dc.diff_files + dc.funny_files]
    for sub in dc.subdirs.values():
        out += _diff(sub)
    return out


def test_deployed_core_copy_is_identical_to_canonical_core():
    dc = filecmp.dircmp(ROOT / "core", APP / "core", ignore=["__pycache__"])
    # dircmp 的 diff_files 只比 stat；对 common_files 逐字节再比一遍
    mismatched = _diff(dc)
    stack = [dc]
    while stack:
        d = stack.pop()
        _, bad, err = filecmp.cmpfiles(d.left, d.right, d.common_files, shallow=False)
        mismatched += [str(Path(d.left) / n) for n in bad + err]
        stack.extend(d.subdirs.values())
    assert not mismatched, f"v6/app-builder/core 与根 core 不一致：{sorted(set(mismatched))}"


@pytest.fixture()
def analyzer():
    spec = importlib.util.spec_from_file_location("app_builder_python_analyzer", APP / "python_analyzer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backtest_fetch_window_is_anchored_on_as_of(analyzer):
    from core.market_data.trading_calendar import get_trading_days
    for as_of in ("2026-09-01", "2026-06-01", "2026-01-05"):
        start = analyzer._fetch_start(as_of)
        assert len(get_trading_days(start, as_of)) >= 250, (as_of, start)


def test_live_fetch_window_still_covers_250_bars(analyzer):
    from datetime import datetime
    from core.market_data.trading_calendar import get_trading_days
    today = datetime.now(analyzer._import_cn_tz()).strftime("%Y-%m-%d")
    start = analyzer._fetch_start(None)
    if today[:4] not in ("2025", "2026"):
        pytest.skip("交易日历只覆盖已核验年份")
    assert len(get_trading_days(start, today)) >= 250
