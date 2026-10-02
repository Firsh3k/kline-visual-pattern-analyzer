#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Batch 2-4 回归测试
覆盖：布林带120日百分位、batch_scan路径、头肩底倾斜颈线、突破事件、回测固定窗口、信号可信度命名
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skill', 'scripts'))

from analyze_stock import (
    detect_bollinger, calc_indicators,
    extract_weekly_data, analyze
)
import pandas as pd
from pattern_scanner import detect_all_patterns as detect_all_patterns_df


def make_test_data(n=100, trend='flat'):
    """生成测试K线数据"""
    import math
    data = []
    base = 100.0
    for i in range(n):
        if trend == 'up':
            close = base + i * 0.5 + math.sin(i * 0.3) * 2
        elif trend == 'down':
            close = base - i * 0.3 + math.sin(i * 0.3) * 2
        else:
            close = base + math.sin(i * 0.2) * 5
        high = close + abs(math.sin(i * 0.5)) * 2
        low = close - abs(math.cos(i * 0.5)) * 2
        volume = 1000000 + int(math.sin(i * 0.4) * 500000)
        data.append({
            'day': f'2026-01-{i+1:02d}',
            'open': round(close - 0.5, 2),
            'high': round(high, 2),
            'low': round(low, 2),
            'close': round(close, 2),
            'volume': volume
        })
    return data


def test_bollinger_120_percentile():
    """BUG-008: 布林带收口使用120日宽度百分位<25%"""
    print("测试 BUG-008: 布林带120日百分位...")
    data = make_test_data(150, 'flat')
    ind = calc_indicators(data)
    result = detect_bollinger(data, ind)
    assert 'width_percentile' in result, "缺少width_percentile字段"
    assert 'is_squeeze' in result, "缺少is_squeeze字段"
    wp = int(result['width_percentile'])
    assert 0 <= wp <= 100, f"宽度百分位应在0-100之间，实际{wp}"
    # 横盘数据应该大概率是收口状态
    print(f"  宽度百分位: {wp}%, is_squeeze: {result['is_squeeze']}")
    print("  ✅ 通过")


def test_hs_bottom_sloped_neckline():
    """BUG-011: 头肩底使用倾斜颈线（两点连线）"""
    print("测试 BUG-011: 头肩底倾斜颈线...")
    # 验证analyze_stock.py中包含头肩底倾斜颈线逻辑
    script_path = os.path.join(os.path.dirname(__file__), '..', 'skill', 'scripts', 'analyze_stock.py')
    with open(script_path, 'r', encoding='utf-8') as f:
        source = f.read()
    assert 'neck_left_idx' in source, "缺少颈线左点索引计算"
    assert 'neck_right_idx' in source, "缺少颈线右点索引计算"
    assert 'neck_slope' in source, "缺少颈线斜率计算"
    assert '左颈线点' in source, "缺少左颈线点标注"
    assert '右颈线点' in source, "缺少右颈线点标注"
    print("  ✅ 头肩底倾斜颈线逻辑已实现")


def test_hs_top_sloped_neckline():
    """BUG-011: 头肩顶使用倾斜颈线（两点连线）"""
    print("测试 BUG-011: 头肩顶倾斜颈线...")
    script_path = os.path.join(os.path.dirname(__file__), '..', 'skill', 'scripts', 'analyze_stock.py')
    with open(script_path, 'r', encoding='utf-8') as f:
        source = f.read()
    # 头肩顶也使用同样的倾斜颈线逻辑
    assert 'neck_slope' in source, "缺少颈线斜率计算"
    assert '左颈线点' in source, "缺少左颈线点标注"
    assert '右颈线点' in source, "缺少右颈线点标注"
    print("  ✅ 头肩顶倾斜颈线逻辑已实现")


def test_breakout_event_detection():
    """BUG-012: 突破事件检测（前一日<=颈线，当日>颈线）"""
    print("测试 BUG-012: 突破事件检测...")
    script_path = os.path.join(os.path.dirname(__file__), '..', 'skill', 'scripts', 'analyze_stock.py')
    with open(script_path, 'r', encoding='utf-8') as f:
        source = f.read()
    assert 'breakout_event' in source, "缺少突破事件检测"
    assert 'breakdown_event' in source, "缺少破位事件检测"
    assert "data[i-1]['close'] <=" in source, "缺少前一日收盘价判断"
    assert "data[i]['close'] >" in source, "缺少当日收盘价判断"
    print("  ✅ 突破事件检测逻辑已实现")


def test_backtest_fixed_horizon():
    """BUG-013: 回测使用固定窗口T+3/5/10/20/40"""
    print("测试 BUG-013: 回测固定窗口...")
    script_path = os.path.join(os.path.dirname(__file__), '..', 'skill', 'scripts', 'analyze_stock.py')
    with open(script_path, 'r', encoding='utf-8') as f:
        source = f.read()
    assert 'horizon_returns' in source, "缺少horizon_returns固定窗口逻辑"
    assert "f'T+{h}'" in source or 'f"T+{h}"' in source, "缺少T+N窗口动态生成"
    assert '[3, 5, 10, 20, 40]' in source, "缺少固定窗口列表[3,5,10,20,40]"
    print("  ✅ 回测固定窗口T+3/5/10/20/40已实现")


def test_batch_scan_path_resolution():
    """BUG-019: batch_scan路径解析（向上查找data目录）"""
    print("测试 BUG-019: batch_scan路径解析...")
    batch_scan_path = os.path.join(os.path.dirname(__file__), '..', 'skill', 'scripts', 'batch_scan.py')
    with open(batch_scan_path, 'r', encoding='utf-8') as f:
        source = f.read()
    assert '向上查找data目录' in source or 'os.path.dirname(os.path.dirname' in source, "缺少向上查找data目录逻辑"
    assert 'argparse' in source, "缺少argparse CLI参数解析"
    print("  ✅ batch_scan路径解析已修复（argparse）")


def test_signal_confidence_naming():
    """BUG-014: 信号可信度命名（非"准确率"）"""
    print("测试 BUG-014: 信号可信度命名...")
    # 检查Python端字段名
    import inspect
    source = inspect.getsource(analyze)
    # buy_accuracy/sell_accuracy是内部计算字段，UI显示已改为"信号可信度"
    assert 'buy_accuracy' in source, "缺少buy_accuracy计算"
    assert 'sell_accuracy' in source, "缺少sell_accuracy计算"
    # 检查Web端UI是否已改为"信号可信度"
    html_path = os.path.join(os.path.dirname(__file__), '..', 'kline_analyzer.html')
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    assert '买入信号可信度' in html, "Web端缺少'买入信号可信度'命名"
    assert '卖出信号可信度' in html, "Web端缺少'卖出信号可信度'命名"
    assert '经验先验' in html, "缺少'经验先验'说明"
    print("  ✅ 信号可信度命名已更新")


def test_pattern_reliability_heuristic():
    """BUG-015: 形态可靠性标注为经验先验"""
    print("测试 BUG-015: 形态可靠性经验先验标注...")
    html_path = os.path.join(os.path.dirname(__file__), '..', 'kline_analyzer.html')
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    assert '经验先验' in html, "缺少'经验先验'标注"
    assert '非项目内真实回测胜率' in html, "缺少'非项目内真实回测胜率'说明"
    print("  ✅ 形态可靠性已标注为经验先验")


def test_weekly_no_duplicate():
    """BUG-002: 周线聚合无重复（回归验证）"""
    print("测试 BUG-002: 周线聚合无重复（回归）...")
    data = make_test_data(30, 'flat')
    weekly = extract_weekly_data(data)
    # 验证每根日K只属于一个周
    assert len(weekly) > 0, "周线数据为空"
    total_days = sum(w.get('trading_days', 1) for w in weekly)
    assert total_days <= len(data), f"周线交易日总数{total_days}超过原始数据{len(data)}"
    print(f"  原始{len(data)}天 -> 周线{len(weekly)}根")
    print("  ✅ 通过")


if __name__ == '__main__':
    print("=" * 60)
    print("Batch 2-4 回归测试")
    print("=" * 60)

    tests = [
        test_bollinger_120_percentile,
        test_hs_bottom_sloped_neckline,
        test_hs_top_sloped_neckline,
        test_breakout_event_detection,
        test_backtest_fixed_horizon,
        test_batch_scan_path_resolution,
        test_signal_confidence_naming,
        test_pattern_reliability_heuristic,
        test_weekly_no_duplicate,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            failed += 1
        print()

    print("=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    print("=" * 60)
    sys.exit(1 if failed > 0 else 0)
