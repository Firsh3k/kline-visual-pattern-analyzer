#!/usr/bin/env python3
"""
黄金形态识别测试用例（Golden Pattern Test Suite）

目的：防止形态识别算法在代码变更后产生偏差。
这些是经过人工确认的基准案例，每次代码变更后必须全部通过。

使用方法：
    python tests/test_golden_patterns.py

运行时机：
    1. 每次修改 core/swing_pivot.py 或 core/pivot_patterns.py 后
    2. 每次部署网站前
    3. CI 自动运行
    4. 网站部署后通过 ?selftest=1 触发前端版本

如果这些测试失败，说明算法行为发生了变化，必须：
    1. 确认是有意的算法改进，还是意外回归
    2. 有意的改进需要更新本文件中的预期值，并说明原因
    3. 意外回归必须修复后才能部署
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.types import OHLCV
from core.pivot_patterns import (
    WBottomDetector,
    MTopDetector,
    HSBottomDetector,
    HSTopDetector,
)
from core.swing_pivot import SwingPivotEngine


# =====================================================================
# 工具函数
# =====================================================================

def make_klines(ohlc_list):
    """
    从简单的OHLC数组构造OHLCV列表。
    ohlc_list: [(open, high, low, close, volume), ...]
    日期自动生成（跳过周末）。
    """
    klines = []
    d = datetime(2026, 1, 1)
    for o, h, l, c, v in ohlc_list:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        klines.append(OHLCV(
            date=d.strftime("%Y-%m-%d"),
            open=float(o), high=float(h), low=float(l),
            close=float(c), volume=float(v)
        ))
        d += timedelta(days=1)
    return klines


def build_w_bottom_bars():
    """
    构造一个经过验证的标准W底数据。
    基准结果：W底85分S级，头肩底0分，颈线48.3，目标52.6。
    """
    bars = []
    # 左侧下跌：60→45（20根）
    for i in range(20):
        p = 60 - 15 * (i / 19)
        bars.append((p, p + 0.2, p - 0.2, p, 1000000))
    # 左底区域
    for i in range(4):
        p = 45.5 - i * 0.1
        bars.append((p, p + 0.3, p - 0.3, p, 1500000))
    # 左底最低点：index 24，low=44.0
    bars.append((44.5, 44.8, 44.0, 44.3, 3000000))
    bars.append((44.3, 45.2, 44.2, 45.0, 2200000))
    # 反弹到颈线48
    for i in range(5):
        p = 45.0 + i * 0.6
        bars.append((p, p + 0.2, p - 0.2, p, 1800000))
    # 颈线点：index 30，high=48.3
    bars.append((48.0, 48.3, 47.8, 48.0, 1600000))
    # 回调到右底
    for i in range(4):
        p = 48.0 - i * 0.7
        bars.append((p, p + 0.2, p - 0.2, p, 1400000))
    # 右底最低点：index 35/36，low=44.5（高于左底44.0）
    bars.append((45.0, 45.3, 44.5, 44.8, 2000000))
    bars.append((44.8, 45.5, 44.7, 45.3, 1800000))
    # 放量突破颈线
    for i in range(9):
        p = 45.3 + i * 0.7
        bars.append((p, p + 0.3, p - 0.2, p + 0.1, 2500000 + i * 200000))
    # 高位震荡
    for i in range(10):
        p = 52 + (i % 3 - 1) * 0.2
        bars.append((p, p + 0.25, p - 0.25, p, 3000000))
    return bars


def build_m_top_bars():
    """
    构造一个经过验证的标准M头数据（W底的镜像）。
    基准结果：M头85分S级，头肩顶0分，颈线41.7，目标37.4。
    """
    bars = []
    # 左侧上涨：30→45（20根）
    for i in range(20):
        p = 30 + 15 * (i / 19)
        bars.append((p, p + 0.2, p - 0.2, p, 1000000))
    # 左头区域
    for i in range(4):
        p = 44.5 + i * 0.1
        bars.append((p, p + 0.3, p - 0.3, p, 1500000))
    # 左头最高点：index 24，high=46.0
    bars.append((45.5, 46.0, 45.2, 45.7, 3000000))
    bars.append((45.7, 45.8, 44.8, 45.0, 2200000))
    # 回调到颈线42
    for i in range(5):
        p = 45.0 - i * 0.6
        bars.append((p, p + 0.2, p - 0.2, p, 1800000))
    # 颈线点：index 30，low=41.7
    bars.append((42.0, 42.2, 41.7, 42.0, 1600000))
    # 反弹到右头
    for i in range(4):
        p = 42.0 + i * 0.7
        bars.append((p, p + 0.2, p - 0.2, p, 1400000))
    # 右头最高点：index 35/36，high=45.5（低于左头46.0）
    bars.append((45.0, 45.5, 44.7, 45.2, 2000000))
    bars.append((45.2, 45.3, 44.5, 44.7, 1800000))
    # 跌破颈线
    for i in range(9):
        p = 44.7 - i * 0.7
        bars.append((p, p + 0.2, p - 0.3, p - 0.1, 2500000 + i * 200000))
    # 低位震荡
    for i in range(10):
        p = 38 + (i % 3 - 1) * 0.2
        bars.append((p, p + 0.25, p - 0.25, p, 3000000))
    return bars


def detect_all(klines):
    """运行全部4个形态检测器，返回 pattern_id -> PatternResult"""
    pivot_engine = SwingPivotEngine(left_bars=5, right_bars=5)
    pivots = pivot_engine.detect(klines)

    detectors = [WBottomDetector(), MTopDetector(),
                 HSBottomDetector(), HSTopDetector()]

    results = {}
    for detector in detectors:
        found = detector.detect(klines, pivots)
        if found:
            best = max(found, key=lambda r: r.score)
            results[best.pattern_id] = best
    return results


# =====================================================================
# 黄金测试用例
# =====================================================================

def test_w_bottom_not_confused_with_hs():
    """
    黄金用例1：标准W底结构
    两底结构必须被识别为W底，不能被误判为头肩底。
    """
    klines = make_klines(build_w_bottom_bars())
    results = detect_all(klines)

    w = results.get("w_bottom")
    hs = results.get("hs_bottom")
    w_score = w.score if w else 0
    hs_score = hs.score if hs else 0

    print(f"  K线数: {len(klines)}")
    print(f"  W底: {w_score}分，颈线={w.neckline_price if w else None}，"
          f"目标={w.target_price if w else None}")
    print(f"  头肩底: {hs_score}分")

    assert w_score >= 80, f"标准W底应>=80分，实际{w_score}"
    assert w_score > hs_score, (
        f"两底结构不应被误判为头肩底（W={w_score}, HS={hs_score}）"
    )
    assert w.neckline_price is not None, "W底必须有颈线价"
    assert w.target_price is not None, "W底必须有目标价"

    print("  ✅ PASS: W底正确识别，未被误判为头肩底")


def test_m_top_not_confused_with_hs():
    """
    黄金用例2：标准M头结构
    两头结构必须被识别为M头，不能被误判为头肩顶。
    """
    klines = make_klines(build_m_top_bars())
    results = detect_all(klines)

    m = results.get("m_top")
    hs = results.get("hs_top")
    m_score = m.score if m else 0
    hs_score = hs.score if hs else 0

    print(f"  K线数: {len(klines)}")
    print(f"  M头: {m_score}分，颈线={m.neckline_price if m else None}，"
          f"目标={m.target_price if m else None}")
    print(f"  头肩顶: {hs_score}分")

    assert m_score >= 80, f"标准M头应>=80分，实际{m_score}"
    assert m_score > hs_score, (
        f"两头结构不应被误判为头肩顶（M={m_score}, HS={hs_score}）"
    )
    assert m.neckline_price is not None, "M头必须有颈线价"

    print("  ✅ PASS: M头正确识别，未被误判为头肩顶")


def test_confirmed_pivots_stable():
    """
    黄金用例3：无未来函数验证
    修改未来右侧K线，会影响尚未确认的pivot，但不能改变已经确认的历史pivot。
    """
    # 构造有明确V形低点的数据
    bars = []
    for i in range(15):
        p = 50 - i * 0.5
        bars.append((p, p + 0.2, p - 0.2, p, 1000000))
    bars.append((42.5, 42.8, 42.0, 42.5, 2000000))
    bars.append((42.5, 43.3, 42.3, 43.0, 1800000))
    for i in range(15):
        p = 43.0 + i * 0.5
        bars.append((p, p + 0.2, p - 0.2, p, 1000000))
    # 足够的后续数据
    for i in range(25):
        p = 50.5 + (i % 3 - 1) * 0.15
        bars.append((p, p + 0.2, p - 0.2, p, 1000000))

    klines = make_klines(bars)
    low_idx = min(range(len(klines)), key=lambda i: klines[i].low)

    engine = SwingPivotEngine(left_bars=5, right_bars=5)

    # prefix中已确认
    pivots_prefix = engine.detect(klines[:low_idx + 10])
    confirmed_prefix = [p for p in pivots_prefix if p.confirmed and p.index < low_idx]

    # 完整数据
    pivots_full = engine.detect(klines)
    confirmed_full = [p for p in pivots_full if p.confirmed]

    print(f"  最低点index={low_idx}, low={klines[low_idx].low}")
    print(f"  Prefix已确认pivot: {len(confirmed_prefix)}")
    print(f"  完整数据已确认pivot: {len(confirmed_full)}")

    for pp in confirmed_prefix:
        matching = [p for p in confirmed_full
                    if p.index == pp.index and p.pivot_type == pp.pivot_type]
        assert matching, f"历史pivot index={pp.index}在完整数据中丢失"
        assert matching[0].price == pp.price, (
            f"历史pivot index={pp.index}价格被改变：{pp.price}→{matching[0].price}"
        )

    print("  ✅ PASS: 已确认的历史pivot不会被未来数据改变")


def test_pending_vs_confirmed_boundary():
    """
    黄金用例4：Pivot确认边界（off-by-one验证）
    right_bars-1根 → pending；right_bars根 → confirmed。
    """
    bars = []
    for i in range(15):
        p = 55 - i * 0.5
        bars.append((p, p + 0.2, p - 0.2, p, 1000000))
    bars.append((47.5, 47.8, 47.0, 47.5, 2000000))
    bars.append((47.5, 48.3, 47.3, 48.0, 1800000))
    for i in range(20):
        p = 48.0 + i * 0.5
        bars.append((p, p + 0.2, p - 0.2, p, 1000000))

    klines = make_klines(bars)
    low_idx = min(range(len(klines)), key=lambda i: klines[i].low)

    engine = SwingPivotEngine(left_bars=5, right_bars=5)

    # 右侧4根 → pending
    p4 = engine.detect(klines[:low_idx + 5])
    pivot4 = [p for p in p4 if p.index == low_idx]
    assert pivot4 and not pivot4[0].confirmed, "右侧4根应为pending"
    print("  右侧4根（right_bars=5）→ pending ✅")

    # 右侧5根 → confirmed
    p5 = engine.detect(klines[:low_idx + 6])
    pivot5 = [p for p in p5 if p.index == low_idx]
    assert pivot5 and pivot5[0].confirmed, "右侧5根应为confirmed"
    assert pivot5[0].confirmed_at_index == low_idx + 5, (
        f"confirmed_at_index应为{low_idx+5}，实际{pivot5[0].confirmed_at_index}"
    )
    print(f"  右侧5根 → confirmed, confirmed_at_index={low_idx+5} ✅")

    print("  ✅ PASS: Pivot确认边界正确，无off-by-one")


def test_output_schema():
    """
    黄金用例5：输出schema完整性
    """
    klines = make_klines(build_w_bottom_bars())
    results = detect_all(klines)

    required = ["pattern_id", "pattern_name", "score", "confidence",
                "stage", "key_points", "neckline_price", "target_price", "stop_loss"]

    assert results, "应检测到形态"
    for pid, result in results.items():
        d = result.to_dict()
        for f in required:
            assert f in d, f"{pid}缺少字段{f}"

    print(f"  检测到形态: {list(results.keys())}")
    print("  ✅ PASS: 输出schema完整")


def run_tests():
    tests = [
        ("W底识别（不被误判为头肩底）", test_w_bottom_not_confused_with_hs),
        ("M头识别（不被误判为头肩顶）", test_m_top_not_confused_with_hs),
        ("无未来函数（历史pivot不变）", test_confirmed_pivots_stable),
        ("Pivot确认边界（off-by-one）", test_pending_vs_confirmed_boundary),
        ("输出schema完整性", test_output_schema),
    ]

    print("=" * 70)
    print("黄金形态识别测试（Golden Pattern Test Suite）")
    print("=" * 70)

    passed = failed = 0
    failures = []

    for name, fn in tests:
        print(f"\n[测试] {name}")
        try:
            fn()
            passed += 1
        except Exception as e:
            failed += 1
            failures.append((name, str(e)))
            import traceback
            traceback.print_exc()
            print(f"  ❌ FAIL: {e}")

    print("\n" + "=" * 70)
    print(f"结果：{passed} 通过，{failed} 失败")
    print("=" * 70)

    if failures:
        print("\n⚠️  黄金测试失败！禁止部署。")
        for n, e in failures:
            print(f"  - {n}: {e}")
        return 1
    print("\n✅ 所有黄金测试通过，可以部署。")
    return 0


if __name__ == "__main__":
    sys.exit(run_tests())
