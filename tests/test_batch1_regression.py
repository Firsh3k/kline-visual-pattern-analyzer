#!/usr/bin/env python3
"""
Batch 1 回归测试脚本 (non-vacuous)
覆盖：BUG-002 周线聚合、BUG-004 量价分析4种状态、BUG-005/006 W底判定
所有测试用例均为非空验证
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
    print("\n=== TEST BUG-002: 周线聚合无重复计算（Friday→Monday跨周） ===")
    data = [
        {'day': '2026-09-21', 'open': 100, 'high': 102, 'low': 99, 'close': 101, 'volume': 1000},
        {'day': '2026-09-22', 'open': 101, 'high': 103, 'low': 100, 'close': 102, 'volume': 1100},
        {'day': '2026-09-23', 'open': 102, 'high': 104, 'low': 101, 'close': 103, 'volume': 1200},
        {'day': '2026-09-24', 'open': 103, 'high': 105, 'low': 102, 'close': 104, 'volume': 1300},
        {'day': '2026-09-25', 'open': 104, 'high': 106, 'low': 103, 'close': 105, 'volume': 1400},
        {'day': '2026-09-28', 'open': 105, 'high': 107, 'low': 104, 'close': 106, 'volume': 1500},
        {'day': '2026-09-29', 'open': 106, 'high': 108, 'low': 105, 'close': 107, 'volume': 1600},
        {'day': '2026-09-30', 'open': 107, 'high': 109, 'low': 106, 'close': 108, 'volume': 1700},
    ]
    weekly = extract_weekly_data(data)
    print(f"  生成周线数量: {len(weekly)} (预期: 2)")
    assert len(weekly) == 2, f"周线数量错误: {len(weekly)} != 2"

    week1_vol = 1000 + 1100 + 1200 + 1300 + 1400
    print(f"  第一周volume: {weekly[0]['volume']} (预期: {week1_vol})")
    assert weekly[0]['volume'] == week1_vol, f"第一周volume错误"

    print(f"  第一周high: {weekly[0]['high']} (预期: 106，不能包含9/28的107)")
    assert weekly[0]['high'] == 106, f"第一周high错误，新周第一天被错误写入旧周"

    week2_vol = 1500 + 1600 + 1700
    print(f"  第二周volume: {weekly[1]['volume']} (预期: {week2_vol})")
    assert weekly[1]['volume'] == week2_vol, f"第二周volume错误"
    print("  ✅ PASSED")


def test_volume_relation_all_four_states():
    """BUG-004: 量价分析覆盖全部4种状态"""
    print("\n=== TEST BUG-004: 量价分析4种状态全覆盖 ===")

    def make_data(price_trend, vol_trend, n=30):
        data = []
        base_price = 100.0
        base_vol = 1000.0
        for i in range(n):
            close = base_price + i * 0.5 if price_trend == 'up' else base_price - i * 0.5
            volume = base_vol + i * 50 if vol_trend == 'up' else base_vol - i * 30
            data.append({
                'day': f'2026-09-{i+1:02d}',
                'open': close - 0.5, 'high': close + 1, 'low': close - 1,
                'close': close, 'volume': max(volume, 100.0)
            })
        return data

    test_cases = [
        ('up', 'up', '量价齐升'),
        ('up', 'down', '价涨量缩'),
        ('down', 'up', '价跌量增'),
        ('down', 'down', '价跌量缩'),
    ]

    for price_trend, vol_trend, expected_keyword in test_cases:
        data = make_data(price_trend, vol_trend)
        ind = calc_indicators(data)
        vol = analyze_volume(data, ind)
        print(f"  价格{price_trend}+量{vol_trend}: '{vol['relation']}' (预期含'{expected_keyword}')")
        assert expected_keyword in vol['relation'], f"预期包含'{expected_keyword}'，实际: {vol['relation']}"
    print("  ✅ PASSED (4种状态全部验证)")


def make_w_bottom_df(l1, l2, neckline_high, n=80):
    """构造标准W底数据，n>=61确保detect_double_bottom能运行"""
    dates = pd.date_range('2026-01-01', periods=n, freq='B')
    df = pd.DataFrame({
        'date': dates,
        'open': [12.0]*n, 'high': [13.0]*n, 'low': [11.0]*n,
        'close': [12.0]*n, 'volume': [1000.0]*n
    })
    df.loc[10, ['low', 'close']] = float(l1)
    df.loc[10, 'high'] = float(l1 + 1)
    df.loc[10, 'open'] = float(l1 + 0.5)
    df.loc[25, ['high', 'close']] = float(neckline_high)
    df.loc[25, 'low'] = float(neckline_high - 1)
    df.loc[25, 'open'] = float(neckline_high - 0.5)
    df.loc[40, ['low', 'close']] = float(l2)
    df.loc[40, 'high'] = float(l2 + 1)
    df.loc[40, 'open'] = float(l2 + 0.5)
    breakout_price = float(neckline_high + 1)
    df.loc[55:, ['close', 'high']] = breakout_price
    df.loc[55:, 'low'] = float(breakout_price - 0.5)
    df.loc[55:, 'open'] = float(breakout_price - 0.3)
    return df


def test_w_bottom_neckline_uses_highs():
    """BUG-005: W底颈线使用highs而非lows，非空验证"""
    print("\n=== TEST BUG-005: W底颈线使用highs（非空验证） ===")
    df = make_w_bottom_df(l1=10, l2=10.2, neckline_high=15, n=80)
    signals = detect_double_bottom(df)
    print(f"  检测到W底信号数: {len(signals)} (预期≥1)")
    assert len(signals) >= 1, f"标准W底应检测到信号，实际: {len(signals)}"
    desc = signals[0]['desc']
    print(f"  信号描述: {desc}")
    assert '15.0' in desc or '颈线位15' in desc, f"颈线应该是15（来自highs），实际: {desc}"
    print("  ✅ PASSED")


def test_w_bottom_similarity_upper_bound():
    """BUG-006: W底两底差异≤5%上限，非空验证"""
    print("\n=== TEST BUG-006: W底两底差异≤5%上限（非空验证） ===")

    df_2pct = make_w_bottom_df(l1=10, l2=10.2, neckline_high=15, n=80)
    signals_2pct = detect_double_bottom(df_2pct)
    print(f"  差异2% (10 vs 10.2): {len(signals_2pct)} 个信号 (预期≥1)")
    assert len(signals_2pct) >= 1, f"差异2%应判定为W底，实际: {len(signals_2pct)}"

    df_10pct = make_w_bottom_df(l1=10, l2=11, neckline_high=15, n=80)
    signals_10pct = detect_double_bottom(df_10pct)
    print(f"  差异10% (10 vs 11): {len(signals_10pct)} 个信号 (预期=0)")
    assert len(signals_10pct) == 0, f"差异10%不应判定为W底，实际: {len(signals_10pct)}"
    print("  ✅ PASSED")


if __name__ == '__main__':
    print("=" * 60)
    print("Batch 1 回归测试 (non-vacuous)")
    print("=" * 60)
    passed = 0
    failed = 0
    tests = [
        test_weekly_aggregation_no_duplicate,
        test_volume_relation_all_four_states,
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
            print(f"  ❌ ERROR: {type(e).__name__}: {e}")
            failed += 1
    print("\n" + "=" * 60)
    print(f"测试结果: {passed} PASSED, {failed} FAILED")
    print("=" * 60)
    sys.exit(1 if failed > 0 else 0)
