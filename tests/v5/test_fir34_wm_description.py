"""
FIR-34：W底 / M头 说明文字与实际判定条件一致（仅文字修正，判定逻辑不变）

判定条件（未改动）：
  W底：右底 >= 左底 * 0.97 —— 右底允许比左底低至多 3%，所以“不创新低”的说法是错的；
  M头：右顶 <= 左顶 * 1.03 —— 右顶允许比左顶高至多 3%。

回归证据：tests/tools/compare_detector_outputs.py 对比 #50 基线，全部 20016 次识别中
只有 key_points[].description 发生变化（见 PR 描述）。
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from core import OHLCV, WBottomDetector, MTopDetector  # noqa: E402

W_RIGHT = "第二个摆动低点（不低于左底 3% 以上）"
M_RIGHT = "第二个摆动高点（不高于左顶 3% 以上）"


def _load(name):
    rows = []
    with open(ROOT / "tests" / "v5" / "fixtures" / name, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(OHLCV(r["date"], float(r["open"]), float(r["high"]), float(r["low"]),
                              float(r["close"]), float(r["volume"])))
    return rows


def _kp(res, name):
    return next(k for k in res.key_points if k.name == name)


def test_docstrings_state_the_actual_tolerance():
    assert "不创新低" not in WBottomDetector.__doc__
    assert "允许比左底低至多 3%" in WBottomDetector.__doc__
    assert "不过度创新高" not in MTopDetector.__doc__
    assert "允许比左顶高至多 3%" in MTopDetector.__doc__


def test_002436_w_bottom_right_below_left_is_described_truthfully():
    """真实案例：002436 右底 18.38 低于左底 18.63，旧文字却写“不创新低”"""
    kl = _load("002436_qfq_20261013.csv")
    seen_lower = False
    for n in range(60, len(kl) + 1):
        for r in WBottomDetector().detect(kl[:n]):
            left, right = _kp(r, "左底"), _kp(r, "右底")
            assert right.description == W_RIGHT
            assert "不创新低" not in right.description
            assert right.price >= left.price * 0.97 - 1e-9   # 文字描述的条件确实成立
            if right.price < left.price:
                seen_lower = True
    assert seen_lower, "fixture 应包含右底低于左底的 W 底（FIR-34 的触发场景）"


def test_all_fixtures_descriptions_match_rule():
    for name in ("002436_qfq_20261013.csv", "sh000001_20261013.csv"):
        kl = _load(name)
        for n in range(60, len(kl) + 1, 5):
            for r in WBottomDetector().detect(kl[:n]):
                assert _kp(r, "右底").description == W_RIGHT
                assert _kp(r, "右底").price >= _kp(r, "左底").price * 0.97 - 1e-9
            for r in MTopDetector().detect(kl[:n]):
                assert _kp(r, "右顶").description == M_RIGHT
                assert _kp(r, "右顶").price <= _kp(r, "左顶").price * 1.03 + 1e-9


def test_no_stale_wording_left_in_core():
    for p in (ROOT / "core").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "不创新低" not in text, p
        assert "不过度创新高" not in text, p
