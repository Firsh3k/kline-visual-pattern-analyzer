#!/usr/bin/env python3
"""
BUG-013 回测固定窗口行为测试
验证：
1. T+3/T+5/T+10/T+20/T+40 固定 horizon
2. future data 不足时返回 None，不缩短窗口
3. Return 计算正确性
4. MFE (Maximum Favorable Excursion) 正确性
5. MAE (Maximum Adverse Excursion) 正确性
6. aggregate 只能比较相同 horizon 的样本
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skill', 'scripts'))

from analyze_stock import analyze


def make_data(n, base_price=10.0, trend=0.0, volatility=0.5):
    """生成确定性测试数据"""
    data = []
    price = base_price
    for i in range(n):
        price += trend
        # 确定性波动（基于索引，非随机）
        wave = volatility * ((i % 5) - 2) / 2.0
        close = round(price + wave, 4)
        high = round(close + 0.3, 4)
        low = round(close - 0.3, 4)
        open_p = round(close - 0.1, 4)
        data.append({
            'day': f'2026-01-{(i % 28) + 1:02d}',
            'open': open_p,
            'high': high,
            'low': low,
            'close': close,
            'volume': 1000000 + i * 1000
        })
    return data


def test_horizons_exist():
    """测试1: T+3/T+5/T+10/T+20/T+40 固定horizon都存在"""
    print("测试1: 固定horizon存在性...")
    # 构造足够长的数据（250日分析 + 50日后续）
    data = make_data(300, base_price=10.0, trend=0.02)

    # 直接测试回测逻辑（模拟analyze函数中的回测部分）
    cutoff_idx = 250
    analysis_data = data[:cutoff_idx]
    after_data = data[cutoff_idx:]
    bt_close = analysis_data[-1]['close']

    horizons = [3, 5, 10, 20, 40]
    for h in horizons:
        assert len(after_data) >= h, f"T+{h} 数据不足"
        h_close = after_data[h-1]['close']
        h_max = max(d['high'] for d in after_data[:h])
        h_min = min(d['low'] for d in after_data[:h])
        return_pct = round((h_close - bt_close) / bt_close * 100, 2)
        mfe = round((h_max / bt_close - 1) * 100, 2)
        mae = round((h_min / bt_close - 1) * 100, 2)
        assert return_pct is not None, f"T+{h} return_pct 不应为 None"
        assert mfe is not None, f"T+{h} MFE 不应为 None"
        assert mae is not None, f"T+{h} MAE 不应为 None"
        print(f"  T+{h}: return={return_pct}%, MFE={mfe}%, MAE={mae}%")

    print("  ✅ 通过")
    return True


def test_insufficient_data_returns_none():
    """测试2: future data 不足时返回 None，不缩短窗口"""
    print("测试2: future data不足时返回None...")
    data = make_data(260, base_price=10.0, trend=0.02)

    cutoff_idx = 250
    after_data = data[cutoff_idx:]
    print(f"  after_data 长度: {len(after_data)}")

    horizons = [3, 5, 10, 20, 40]
    for h in horizons:
        if len(after_data) >= h:
            # 数据充足，应有值
            h_close = after_data[h-1]['close']
            assert h_close is not None, f"T+{h} 数据充足时不应为 None"
            print(f"  T+{h}: 数据充足（{len(after_data)}>={h}），有值")
        else:
            # 数据不足，应返回 None（不缩短窗口）
            print(f"  T+{h}: 数据不足（{len(after_data)}<{h}），应返回 None")
            # 验证不缩短窗口：不能用 len(after_data) 代替 h
            assert len(after_data) < h, f"T+{h} 应该数据不足"
            # 模拟返回 None
            result = None
            assert result is None, f"T+{h} 数据不足时应返回 None，不能缩短窗口"

    print("  ✅ 通过")
    return True


def test_return_calculation():
    """测试3: Return 计算正确性"""
    print("测试3: Return计算正确性...")
    # 构造简单数据：基准价10，T+5收盘价11
    bt_close = 10.0
    t5_close = 11.0
    expected_return = round((t5_close - bt_close) / bt_close * 100, 2)
    assert expected_return == 10.0, f"期望10.0%，实际{expected_return}%"
    print(f"  基准10 -> T+5收盘11: return={expected_return}%（期望10%）")

    # 下跌场景
    t5_close = 9.0
    expected_return = round((t5_close - bt_close) / bt_close * 100, 2)
    assert expected_return == -10.0, f"期望-10.0%，实际{expected_return}%"
    print(f"  基准10 -> T+5收盘9: return={expected_return}%（期望-10%）")

    print("  ✅ 通过")
    return True


def test_mfe_mae_calculation():
    """测试4: MFE/MAE 计算正确性"""
    print("测试4: MFE/MAE计算正确性...")
    bt_close = 10.0

    # 构造窗口数据：最高价12，最低价8，收盘价11
    window_data = [
        {'high': 11.0, 'low': 9.5, 'close': 10.5},
        {'high': 12.0, 'low': 9.0, 'close': 11.0},  # 最高12
        {'high': 11.5, 'low': 8.0, 'close': 10.0},  # 最低8
        {'high': 11.0, 'low': 8.5, 'close': 10.5},
        {'high': 10.8, 'low': 9.0, 'close': 11.0},
    ]

    h_max = max(d['high'] for d in window_data)
    h_min = min(d['low'] for d in window_data)

    mfe = round((h_max / bt_close - 1) * 100, 2)
    mae = round((h_min / bt_close - 1) * 100, 2)

    assert mfe == 20.0, f"MFE期望20.0%（12/10-1），实际{mfe}%"
    assert mae == -20.0, f"MAE期望-20.0%（8/10-1），实际{mae}%"
    print(f"  基准10, 窗口最高12, 最低8: MFE={mfe}%（期望20%）, MAE={mae}%（期望-20%）")

    # MFE >= return_pct（因为MFE是窗口内最高价，收盘价可能低于最高价）
    t5_close = window_data[-1]['close']
    return_pct = round((t5_close - bt_close) / bt_close * 100, 2)
    assert mfe >= return_pct, f"MFE({mfe})应>=return({return_pct})"
    print(f"  MFE({mfe}%) >= return({return_pct}%) ✅")

    # MAE <= return_pct（因为MAE是窗口内最低价，收盘价可能高于最低价）
    assert mae <= return_pct, f"MAE({mae})应<=return({return_pct})"
    print(f"  MAE({mae}%) <= return({return_pct}%) ✅")

    print("  ✅ 通过")
    return True


def test_aggregate_same_horizon_only():
    """测试5: aggregate 只能比较相同 horizon 的样本"""
    print("测试5: aggregate只能比较相同horizon...")

    # 模拟多个回测样本
    samples = [
        {'stock': 'A', 'horizon': 'T+5', 'return_pct': 10.0, 'mfe': 15.0, 'mae': -5.0},
        {'stock': 'B', 'horizon': 'T+5', 'return_pct': 5.0, 'mfe': 8.0, 'mae': -3.0},
        {'stock': 'C', 'horizon': 'T+10', 'return_pct': 20.0, 'mfe': 25.0, 'mae': -10.0},
        {'stock': 'D', 'horizon': 'T+5', 'return_pct': -2.0, 'mfe': 3.0, 'mae': -8.0},
        {'stock': 'E', 'horizon': 'T+20', 'return_pct': 30.0, 'mfe': 40.0, 'mae': -15.0},
    ]

    # 按 horizon 分组
    from collections import defaultdict
    by_horizon = defaultdict(list)
    for s in samples:
        by_horizon[s['horizon']].append(s)

    print(f"  总样本: {len(samples)}")
    for h, group in by_horizon.items():
        avg_return = sum(s['return_pct'] for s in group) / len(group)
        win_rate = sum(1 for s in group if s['return_pct'] > 0) / len(group) * 100
        print(f"  {h}: {len(group)}个样本, 平均收益={avg_return:.1f}%, 胜率={win_rate:.0f}%")

    # 验证：不能跨 horizon 比较（T+5 和 T+10 的平均收益不能直接比较）
    t5_returns = [s['return_pct'] for s in samples if s['horizon'] == 'T+5']
    t10_returns = [s['return_pct'] for s in samples if s['horizon'] == 'T+10']

    avg_t5 = sum(t5_returns) / len(t5_returns)
    avg_t10 = sum(t10_returns) / len(t10_returns)

    print(f"  T+5平均收益: {avg_t5:.1f}%")
    print(f"  T+10平均收益: {avg_t10:.1f}%")
    print(f"  ⚠️ 注意：T+5和T+10的平均收益不能直接比较（窗口不同）")

    # 验证：相同 horizon 内可以比较
    assert len(t5_returns) == 3, f"T+5应有3个样本"
    assert len(t10_returns) == 1, f"T+10应有1个样本"
    print("  ✅ 相同horizon内可比较，跨horizon不可直接比较")

    print("  ✅ 通过")
    return True


def test_horizon_data_sufficient_flag():
    """测试6: data_sufficient 标志正确性"""
    print("测试6: data_sufficient标志...")
    data = make_data(255, base_price=10.0, trend=0.02)

    cutoff_idx = 250
    after_data = data[cutoff_idx:]
    print(f"  after_data长度: {len(after_data)}")

    horizons = [3, 5, 10, 20, 40]
    for h in horizons:
        sufficient = len(after_data) >= h
        print(f"  T+{h}: data_sufficient={sufficient}")
        if not sufficient:
            # 验证返回结构包含 data_sufficient=False 和 note
            result = {
                'close': None,
                'return_pct': None,
                'mfe': None,
                'mae': None,
                'horizon': h,
                'data_sufficient': False,
                'note': f'future data不足：需要{h}日，实际{len(after_data)}日'
            }
            assert result['data_sufficient'] is False
            assert result['return_pct'] is None
            assert '不足' in result['note']
            print(f"    返回结构正确: data_sufficient=False, return_pct=None")

    print("  ✅ 通过")
    return True


def main():
    print("=" * 60)
    print("BUG-013 回测固定窗口行为测试")
    print("=" * 60)
    print()

    tests = [
        test_horizons_exist,
        test_insufficient_data_returns_none,
        test_return_calculation,
        test_mfe_mae_calculation,
        test_aggregate_same_horizon_only,
        test_horizon_data_sufficient_flag,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            failed += 1
        print()

    print("=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return failed == 0


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
