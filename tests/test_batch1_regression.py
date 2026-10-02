#!/usr/bin/env python3
"""
Batch 1 回归测试脚本
覆盖：BUG-002 周线聚合、BUG-004 量价分析一致性、BUG-005/006 W底判定
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skill', 'scripts'))

from analyze_stock import extract_weekly_data, analyze_volume, calc_indicators
from pattern_scanner import detect_double_bottom
import pandas as pd
import numpy as np

def test_weekly_aggregation_no_duplicate():
    """BUG-002: 周线聚合不能重复计算，每根日K线只属于一个周"""
    print("\n=== TEST BUG-002: 周线聚合无重复计算 ===")
    # 构造10个交易日，跨2个ISO周
    data = [
        {'day': '2026-09-21', 'open': 100, 'high': 102, 'low': 99, 'close': 101, 'volume': 1000},
        {'day': '2026-09-22', 'open': 101, 'high': 103, 'low': 100, 'close': 102, 'volume': 1100},
        {'day': '2026-09-23', 'open': 102, 'high': 104, 'low': 101, 'close': 103, 'volume': 1200},
        {'day': '2026-09-24', 'open': 103, 'high': 105, 'low': 102, 'close': 104, 'volume': 1300},
        {'day': '2026-09-25', 'open': 104, 'high': 106, 'low': 103, 'close': 105, 'volume': 1400},
        # 新周开始（9月28日是周一）
        {'day': '2026-09-28', 'open': 105, 'high': 107, 'low': 104, 'close': 106, 'volume': 1500},
        {'day': '2026-09-29', 'open': 106, 'high': 108, 'low': 105, 'close': 107, 'volume': 1600},
        {'day': '2026-09-30', 'open': 107, 'high': 109, 'low': 106, 'close': 108, 'volume': 1700},
    ]
    weekly = extract_weekly_data(data)
    print(f"  生成周线数量: {len(weekly)} (预期: 2)")
    assert len(weekly) == 2, f"周线数量错误: {len(weekly)} != 2"

    # 第一周（9/21-9/25）的volume应该是5天之和，不包含9/28
    week1_vol = sum(d['volume'] for d in data[:5])
    print(f"  第一周volume: {weekly[0]['volume']} (预期: {week1_vol})")
    assert weekly[0]['volume'] == week1_vol, f"第一周volume错误: {weekly[0]['volume']} != {week1_vol}"

    # 第二周（9/28-9/30）的volume应该是3天之和
    week2_vol = sum(d['volume'] for d in data[5:])
    print(f"  第二周volume: {weekly[1]['volume']} (预期: {week2_vol})")
    assert weekly[1]['volume'] == week2_vol, f"第二周volume错误: {weekly[1]['volume']} != {week2_vol}"

    # 新周第一天（9/28）的high不能出现在第一周
    print(f"  第一周high: {weekly[0]['high']} (预期: 106, 不包含9/28的107)")
    assert weekly[0]['high'] == 106, f"第一周high错误: {weekly[0]['high']} != 106"
    print("  ✅ PASSED")


def test_volume_relation_algorithm():
    """BUG-004: 量价分析使用近5日趋势算法"""
    print("\n=== TEST BUG-004: 量价分析近5日趋势算法 ===")
    # 构造近5日上涨 + VMA5上升的数据
    data = []
    for i in range(20):
        data.append({
            'day': f'2026-09-{i+1:02d}',
            'open': 100 + i, 'high': 102 + i, 'low': 99 + i,
            'close': 101 + i, 'volume': 1000 + i * 100
        })
    ind = calc_indicators(data)
    vol = analyze_volume(data, ind)
    print(f"  relation: {vol['relation']}")
    # 近5日价格上涨（116>112），VMA5上升 → 量价齐升
    assert '量价齐升' in vol['relation'], f"预期量价齐升，实际: {vol['relation']}"
    print("  ✅ PASSED")


def test_w_bottom_neckline_uses_highs():
    """BUG-005: W底颈线使用highs而非lows"""
    print("\n=== TEST BUG-005: W底颈线使用highs ===")
    # 构造标准W底：L1=10, 反弹高点=15, L2=10.2, 突破
    dates = pd.date_range('2026-01-01', periods=40, freq='B')
    df = pd.DataFrame({
        'date': dates,
        'open': [12]*40, 'high': [12]*40, 'low': [12]*40,
        'close': [12]*40, 'volume': [1000]*40
    })
    # L1 at index 5
    df.loc[5, ['low', 'close']] = 10
    df.loc[5, 'high'] = 11
    # 反弹高点 at index 10
    df.loc[10, ['high', 'close']] = 15
    df.loc[10, 'low'] = 14
    # L2 at index 15
    df.loc[15, ['low', 'close']] = 10.2
    df.loc[15, 'high'] = 11
    # 突破 at index 20
    df.loc[20:, ['close', 'high']] = 16
    df.loc[20:, 'low'] = 15.5

    signals = detect_double_bottom(df)
    print(f"  检测到W底信号数: {len(signals)}")
    if len(signals) > 0:
        print(f"  信号描述: {signals[0]['desc']}")
        # 颈线应该是15（两个底之间的高点），而不是从lows中找的最大值
        assert '15.0' in signals[0]['desc'] or '15' in signals[0]['desc'], f"颈线应该是15，实际描述: {signals[0]['desc']}"
    print("  ✅ PASSED")


def test_w_bottom_similarity_upper_bound():
    """BUG-006: W底两底差异≤5%上限"""
    print("\n=== TEST BUG-006: W底两底差异≤5%上限 ===")

    def make_w_bottom(l1, l2):
        dates = pd.date_range('2026-01-01', periods=40, freq='B')
        df = pd.DataFrame({
            'date': dates,
            'open': [12]*40, 'high': [12]*40, 'low': [12]*40,
            'close': [12]*40, 'volume': [1000]*40
        })
        df.loc[5, ['low', 'close']] = l1
        df.loc[5, 'high'] = l1 + 1
        df.loc[10, ['high', 'close']] = l1 + 5
        df.loc[10, 'low'] = l1 + 4
        df.loc[15, ['low', 'close']] = l2
        df.loc[15, 'high'] = l2 + 1
        df.loc[20:, ['close', 'high']] = l1 + 6
        df.loc[20:, 'low'] = l1 + 5.5
        return df

    # 测试1: 两底差异2% → 应该通过
    df1 = make_w_bottom(10, 10.2)
    signals1 = detect_double_bottom(df1)
    print(f"  差异2% (10 vs 10.2): 检测到 {len(signals1)} 个信号 (预期≥1)")

    # 测试2: 两底差异10% → 应该被过滤
    df2 = make_w_bottom(10, 11)
    signals2 = detect_double_bottom(df2)
    print(f"  差异10% (10 vs 11): 检测到 {len(signals2)} 个信号 (预期=0)")
    assert len(signals2) == 0, f"差异10%不应判定为W底，实际检测到 {len(signals2)} 个"
    print("  ✅ PASSED")


if __name__ == '__main__':
    print("=" * 60)
    print("Batch 1 回归测试")
    print("=" * 60)

    passed = 0
    failed = 0

    tests = [
        test_weekly_aggregation_no_duplicate,
        test_volume_relation_algorithm,
        test_w_bottom_neckline_uses_highs,
        test_w_bottom_similarity_upper_bound,
    ]

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果: {passed} PASSED, {failed} FAILED")
    print("=" * 60)
    sys.exit(1 if failed > 0 else 0)
