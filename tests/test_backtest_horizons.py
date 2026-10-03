#!/usr/bin/env python3
"""
BUG-013 回测固定窗口行为测试
直接调用生产函数 calculate_backtest_horizons，验证生产代码行为：
1. T+3/T+5/T+10/T+20/T+40 固定 horizon 都存在
2. future data 不足时返回 None，不缩短窗口
3. Return 计算正确性（调用生产函数后断言结果）
4. MFE (Maximum Favorable Excursion) 正确性
5. MAE (Maximum Adverse Excursion) 正确性
6. data_sufficient 标志正确
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skill', 'scripts'))

# 直接导入生产函数
from analyze_stock import calculate_backtest_horizons


def make_data(n, base_price=10.0, trend=0.0, volatility=0.5):
    """生成确定性测试数据（日期不重复）"""
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
        # 使用不重复的日期（按月递增）
        month = (i // 28) + 1
        day = (i % 28) + 1
        data.append({
            'day': f'2026-{month:02d}-{day:02d}',
            'open': open_p,
            'high': high,
            'low': low,
            'close': close,
            'volume': 1000000 + i * 1000
        })
    return data


def test_horizons_exist():
    """测试1: 调用生产函数，T+3/T+5/T+10/T+20/T+40 固定horizon都存在且data_sufficient=True"""
    print("测试1: 固定horizon存在性（调用生产函数）...")
    # 构造足够长的数据（250日分析 + 50日后续）
    data = make_data(300, base_price=10.0, trend=0.02)
    cutoff = data[249]['day'][:10]

    # 直接调用生产函数
    result = calculate_backtest_horizons(data, cutoff)
    assert 'error' not in result, f"生产函数返回错误: {result.get('error')}"

    horizons = [3, 5, 10, 20, 40]
    for h in horizons:
        key = f'T+{h}'
        assert key in result['horizon_returns'], f"{key} 不存在"
        hr = result['horizon_returns'][key]
        assert hr['data_sufficient'] is True, f"{key} data_sufficient 应为 True"
        assert hr['return_pct'] is not None, f"{key} return_pct 不应为 None"
        assert hr['mfe'] is not None, f"{key} mfe 不应为 None"
        assert hr['mae'] is not None, f"{key} mae 不应为 None"
        assert hr['horizon'] == h, f"{key} horizon 应为 {h}"
        print(f"  {key}: return={hr['return_pct']}%, MFE={hr['mfe']}%, MAE={hr['mae']}%")

    print("  ✅ 通过")
    return True


def test_insufficient_data_returns_none():
    """测试2: 调用生产函数，future data 不足时返回 None，不缩短窗口"""
    print("测试2: future data不足时返回None（调用生产函数）...")
    # 250日分析 + 5日后续（T+10/20/40应该不足）
    data = make_data(255, base_price=10.0, trend=0.02)
    cutoff = data[249]['day'][:10]

    result = calculate_backtest_horizons(data, cutoff)
    assert 'error' not in result

    after_len = len([d for d in data if d['day'][:10] > cutoff])
    print(f"  after_data 长度: {after_len}")

    # T+3/T+5 应该充足
    for h in [3, 5]:
        hr = result['horizon_returns'][f'T+{h}']
        assert hr['data_sufficient'] is True, f"T+{h} 应充足"
        assert hr['return_pct'] is not None

    # T+10/T+20/T+40 应该不足，返回None，不缩短窗口
    for h in [10, 20, 40]:
        hr = result['horizon_returns'][f'T+{h}']
        assert hr['data_sufficient'] is False, f"T+{h} 应不足"
        assert hr['return_pct'] is None, f"T+{h} return_pct 应为 None"
        assert hr['mfe'] is None, f"T+{h} mfe 应为 None"
        assert hr['mae'] is None, f"T+{h} mae 应为 None"
        assert 'note' in hr, f"T+{h} 应有 note 说明"
        print(f"  T+{h}: data_sufficient=False, note={hr['note']}")

    print("  ✅ 通过")
    return True


def test_return_calculation():
    """测试3: 调用生产函数，Return 计算正确性"""
    print("测试3: Return计算正确性（调用生产函数后断言）...")
    # 简单线性上涨数据，便于手动验证
    data = []
    for i in range(260):
        close = 10.0 + i * 0.1  # 每天涨0.1
        data.append({
            'day': f'2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}',
            'open': close - 0.05,
            'high': close + 0.2,
            'low': close - 0.2,
            'close': close,
            'volume': 1000000
        })
    cutoff = data[249]['day'][:10]
    bt_close = data[249]['close']  # 34.9

    result = calculate_backtest_horizons(data, cutoff)
    assert 'error' not in result

    # T+3: 第252天 close = 10 + 252*0.1 = 35.2
    hr = result['horizon_returns']['T+3']
    expected_return = round((35.2 - bt_close) / bt_close * 100, 2)
    assert abs(hr['return_pct'] - expected_return) < 0.01, \
        f"T+3 return={hr['return_pct']}%, 预期={expected_return}%"
    print(f"  T+3: bt_close={bt_close}, h_close={hr['close']}, return={hr['return_pct']}% (预期={expected_return}%)")

    # T+5: 第254天 close = 10 + 254*0.1 = 35.4
    hr5 = result['horizon_returns']['T+5']
    expected_return5 = round((35.4 - bt_close) / bt_close * 100, 2)
    assert abs(hr5['return_pct'] - expected_return5) < 0.01
    print(f"  T+5: return={hr5['return_pct']}% (预期={expected_return5}%)")

    print("  ✅ 通过")
    return True


def test_mfe_mae():
    """测试4: 调用生产函数，MFE/MAE 正确性"""
    print("测试4: MFE/MAE正确性（调用生产函数后断言）...")
    # 构造有明确高低点的数据
    data = []
    for i in range(260):
        if i < 250:
            close = 10.0 + i * 0.01  # 分析期缓慢上涨
        else:
            # 后续5天：先涨后跌
            if i == 251:
                close = 12.0  # 最高点
            elif i == 253:
                close = 8.0   # 最低点
            else:
                close = 10.0
        data.append({
            'day': f'2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}',
            'open': close,
            'high': close + 0.5,
            'low': close - 0.5,
            'close': close,
            'volume': 1000000
        })
    cutoff = data[249]['day'][:10]
    bt_close = data[249]['close']  # 12.49

    result = calculate_backtest_horizons(data, cutoff)
    assert 'error' not in result

    # T+5 包含最高点(12.0+0.5=12.5 high)和最低点(8.0-0.5=7.5 low)
    hr = result['horizon_returns']['T+5']
    # MFE = (max_high / bt_close - 1) * 100
    expected_mfe = round((12.5 / bt_close - 1) * 100, 2)
    # MAE = (min_low / bt_close - 1) * 100
    expected_mae = round((7.5 / bt_close - 1) * 100, 2)

    assert abs(hr['mfe'] - expected_mfe) < 0.01, f"MFE={hr['mfe']}%, 预期={expected_mfe}%"
    assert abs(hr['mae'] - expected_mae) < 0.01, f"MAE={hr['mae']}%, 预期={expected_mae}%"
    print(f"  T+5: MFE={hr['mfe']}% (预期={expected_mfe}%), MAE={hr['mae']}% (预期={expected_mae}%)")

    # 验证兼容字段 max_pct/min_pct 与 mfe/mae 一致
    assert hr['max_pct'] == hr['mfe'], "max_pct 应等于 mfe"
    assert hr['min_pct'] == hr['mae'], "min_pct 应等于 mae"
    print("  兼容字段 max_pct/min_pct 与 mfe/mae 一致")

    print("  ✅ 通过")
    return True


def test_data_sufficient_flag():
    """测试5: data_sufficient 标志正确"""
    print("测试5: data_sufficient标志正确性...")
    # 刚好5日后续
    data = make_data(255, base_price=10.0, trend=0.02)
    cutoff = data[249]['day'][:10]
    result = calculate_backtest_horizons(data, cutoff)

    assert result['horizon_returns']['T+5']['data_sufficient'] is True
    assert result['horizon_returns']['T+10']['data_sufficient'] is False
    assert result['horizon_returns']['T+10']['note'].startswith('future data不足')
    print("  T+5 data_sufficient=True, T+10 data_sufficient=False with note")

    # 0日后续（cutoff是最后一天）
    data2 = make_data(250, base_price=10.0, trend=0.02)
    cutoff2 = data2[249]['day'][:10]
    result2 = calculate_backtest_horizons(data2, cutoff2)
    for h in [3, 5, 10, 20, 40]:
        assert result2['horizon_returns'][f'T+{h}']['data_sufficient'] is False
        assert result2['horizon_returns'][f'T+{h}']['return_pct'] is None
    print("  0日后续：所有horizon data_sufficient=False, return_pct=None")

    print("  ✅ 通过")
    return True


def test_insufficient_analysis_data():
    """测试6: 分析数据不足时返回error"""
    print("测试6: 分析数据不足时返回error...")
    # 只有30天数据（<60）
    data = make_data(30, base_price=10.0, trend=0.02)
    cutoff = data[29]['day'][:10]
    result = calculate_backtest_horizons(data, cutoff)
    assert 'error' in result, "数据不足时应返回error"
    assert '数据不足' in result['error']
    print(f"  返回error: {result['error']}")
    print("  ✅ 通过")
    return True


def main():
    print("=" * 60)
    print("BUG-013 回测固定窗口行为测试（调用生产函数）")
    print("=" * 60)

    tests = [
        test_horizons_exist,
        test_insufficient_data_returns_none,
        test_return_calculation,
        test_mfe_mae,
        test_data_sufficient_flag,
        test_insufficient_analysis_data,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    print("=" * 60)
    return failed == 0


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
