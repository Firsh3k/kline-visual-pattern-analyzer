#!/usr/bin/env python3
"""
JS/Python Parity Test Runner
读取 parity_fixtures.json，生成测试数据，运行 Python 端分析，输出 JSON 结果供 JS 端对比。

用法：
  python3 tests/run_parity_tests.py [--fixture ID] [--output FILE]
"""
import json
import sys
import os
import argparse

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skill', 'scripts'))

from analyze_stock import calc_indicators, match_patterns, match_bear_patterns, analyze_volume, calc_fake_signal_risk, detect_macd_divergence, calc_market_env_from_data


def generate_linear_uptrend(params):
    """生成线性上涨数据"""
    data = []
    price = params['start_price']
    for i in range(params['days']):
        open_p = price
        close_p = price * (1 + params['daily_return'])
        high_p = max(open_p, close_p) * 1.005
        low_p = min(open_p, close_p) * 0.995
        data.append({
            'day': f'2026-{i//30+1:02d}-{i%30+1:02d}',
            'open': round(open_p, 4),
            'high': round(high_p, 4),
            'low': round(low_p, 4),
            'close': round(close_p, 4),
            'volume': params['base_volume']
        })
        price = close_p
    return data


def generate_linear_downtrend(params):
    """生成线性下跌数据"""
    params['daily_return'] = abs(params['daily_return'])
    data = []
    price = params['start_price']
    for i in range(params['days']):
        open_p = price
        close_p = price * (1 - params['daily_return'])
        high_p = max(open_p, close_p) * 1.005
        low_p = min(open_p, close_p) * 0.995
        data.append({
            'day': f'2026-{i//30+1:02d}-{i%30+1:02d}',
            'open': round(open_p, 4),
            'high': round(high_p, 4),
            'low': round(low_p, 4),
            'close': round(close_p, 4),
            'volume': params['base_volume']
        })
        price = close_p
    return data


def generate_w_bottom(params):
    """生成W底形态数据"""
    days = params['days']
    data = []
    price = params['start_price']
    # 阶段1: 下跌到第一底 (前25%)
    phase1_days = int(days * 0.25)
    for i in range(phase1_days):
        t = i / phase1_days
        close_p = params['start_price'] + (params['first_low'] - params['start_price']) * t
        data.append(_make_bar(close_p, i))
    # 阶段2: 反弹到颈线 (25%-40%)
    phase2_days = int(days * 0.15)
    for i in range(phase2_days):
        t = i / phase2_days
        close_p = params['first_low'] + (params['neckline'] - params['first_low']) * t
        data.append(_make_bar(close_p, phase1_days + i))
    # 阶段3: 下跌到第二底 (40%-60%)
    phase3_days = int(days * 0.20)
    for i in range(phase3_days):
        t = i / phase3_days
        close_p = params['neckline'] + (params['second_low'] - params['neckline']) * t
        data.append(_make_bar(close_p, phase1_days + phase2_days + i))
    # 阶段4: 上涨突破 (60%-100%)
    phase4_days = days - len(data)
    for i in range(phase4_days):
        t = i / phase4_days
        close_p = params['second_low'] + (params['breakout_price'] - params['second_low']) * t
        data.append(_make_bar(close_p, len(data)))
    return data


def generate_m_top(params):
    """生成M头形态数据"""
    days = params['days']
    data = []
    # 阶段1: 上涨到第一顶 (前25%)
    phase1_days = int(days * 0.25)
    for i in range(phase1_days):
        t = i / phase1_days
        close_p = params['start_price'] + (params['first_high'] - params['start_price']) * t
        data.append(_make_bar(close_p, i))
    # 阶段2: 回调到颈线 (25%-40%)
    phase2_days = int(days * 0.15)
    for i in range(phase2_days):
        t = i / phase2_days
        close_p = params['first_high'] + (params['neckline'] - params['first_high']) * t
        data.append(_make_bar(close_p, phase1_days + i))
    # 阶段3: 上涨到第二顶 (40%-60%)
    phase3_days = int(days * 0.20)
    for i in range(phase3_days):
        t = i / phase3_days
        close_p = params['neckline'] + (params['second_high'] - params['neckline']) * t
        data.append(_make_bar(close_p, phase1_days + phase2_days + i))
    # 阶段4: 下跌破位 (60%-100%)
    phase4_days = days - len(data)
    for i in range(phase4_days):
        t = i / phase4_days
        close_p = params['second_high'] + (params['breakdown_price'] - params['second_high']) * t
        data.append(_make_bar(close_p, len(data)))
    return data


def _make_bar(close_p, idx):
    open_p = close_p * 0.998
    high_p = max(open_p, close_p) * 1.005
    low_p = min(open_p, close_p) * 0.995
    return {
        'day': f'2026-{idx//30+1:02d}-{idx%30+1:02d}',
        'open': round(open_p, 4),
        'high': round(high_p, 4),
        'low': round(low_p, 4),
        'close': round(close_p, 4),
        'volume': 1000000
    }


GENERATORS = {
    'linear_uptrend': generate_linear_uptrend,
    'linear_downtrend': generate_linear_downtrend,
    'w_bottom_pattern': generate_w_bottom,
    'm_top_pattern': generate_m_top,
    'hs_bottom_sloped': 'hs_bottom_sloped',  # 占位，下面定义
    'hs_bottom_failed': 'hs_bottom_failed',  # 占位，下面定义
}


def generate_hs_bottom_sloped(params):
    """生成头肩底倾斜颈线数据（向上倾斜）"""
    days = params['days']
    data = []
    # 阶段1: 下跌到左肩 (0-20%)
    p1 = int(days * 0.20)
    for i in range(p1):
        t = i / p1
        close_p = params['start_price'] + (params['left_shoulder'] - params['start_price']) * t
        data.append(_make_bar(close_p, len(data)))
    # 阶段2: 下跌到头部 (20%-40%)
    p2 = int(days * 0.20)
    for i in range(p2):
        t = i / p2
        close_p = params['left_shoulder'] + (params['head'] - params['left_shoulder']) * t
        data.append(_make_bar(close_p, len(data)))
    # 阶段3: 反弹到左颈线点 (40%-50%)
    p3 = int(days * 0.10)
    for i in range(p3):
        t = i / p3
        close_p = params['head'] + (params['neck_left_price'] - params['head']) * t
        data.append(_make_bar(close_p, len(data)))
    # 阶段4: 下跌到右肩 (50%-70%)
    p4 = int(days * 0.20)
    for i in range(p4):
        t = i / p4
        close_p = params['neck_left_price'] + (params['right_shoulder'] - params['neck_left_price']) * t
        data.append(_make_bar(close_p, len(data)))
    # 阶段5: 上涨到右颈线点（更高，形成向上倾斜颈线）(70%-80%)
    p5 = int(days * 0.10)
    for i in range(p5):
        t = i / p5
        close_p = params['right_shoulder'] + (params['neck_right_price'] - params['right_shoulder']) * t
        data.append(_make_bar(close_p, len(data)))
    # 阶段6: 突破 (80%-100%)
    p6 = days - len(data)
    for i in range(p6):
        t = i / p6
        close_p = params['neck_right_price'] + (params['breakout_price'] - params['neck_right_price']) * t
        data.append(_make_bar(close_p, len(data)))
    return data


def generate_hs_bottom_failed(params):
    """生成头肩底突破失败数据（突破后又跌破颈线超过3日）"""
    days = params['days']
    data = []
    # 阶段1-4: 标准头肩底构建 (0-70%)
    p1 = int(days * 0.15)
    for i in range(p1):
        t = i / p1
        close_p = params['start_price'] + (params['left_shoulder'] - params['start_price']) * t
        data.append(_make_bar(close_p, len(data)))
    p2 = int(days * 0.15)
    for i in range(p2):
        t = i / p2
        close_p = params['left_shoulder'] + (params['head'] - params['left_shoulder']) * t
        data.append(_make_bar(close_p, len(data)))
    p3 = int(days * 0.10)
    for i in range(p3):
        t = i / p3
        close_p = params['head'] + (params['neckline'] - params['head']) * t
        data.append(_make_bar(close_p, len(data)))
    p4 = int(days * 0.15)
    for i in range(p4):
        t = i / p4
        close_p = params['neckline'] + (params['right_shoulder'] - params['neckline']) * t
        data.append(_make_bar(close_p, len(data)))
    # 阶段5: 突破 (70%-80%)
    p5 = int(days * 0.10)
    for i in range(p5):
        t = i / p5
        close_p = params['right_shoulder'] + (params['breakout_price'] - params['right_shoulder']) * t
        data.append(_make_bar(close_p, len(data)))
    # 阶段6: 跌破颈线（失败）(80%-100%)
    p6 = days - len(data)
    for i in range(p6):
        t = i / p6
        close_p = params['breakout_price'] + (params['fail_price'] - params['breakout_price']) * t
        data.append(_make_bar(close_p, len(data)))
    return data


# 更新GENERATORS字典
GENERATORS['hs_bottom_sloped'] = generate_hs_bottom_sloped
GENERATORS['hs_bottom_failed'] = generate_hs_bottom_failed


def run_fixture(fixture):
    """运行单个fixture，返回分析结果"""
    # 生成数据
    if 'data' in fixture:
        data = fixture['data']
    elif 'data_generator' in fixture:
        gen = GENERATORS.get(fixture['data_generator'])
        if not gen:
            return {'error': f'Unknown generator: {fixture["data_generator"]}'}
        data = gen(fixture['params'])
    else:
        return {'error': 'No data or data_generator'}

    # 计算指标
    ind = calc_indicators(data)

    # 形态匹配
    bull = match_patterns(data, ind)
    bear = match_bear_patterns(data, ind)

    # 量价分析
    vol = analyze_volume(data, ind)

    # MACD背离
    macd_div = detect_macd_divergence(data, ind)

    # 大盘环境（用自身数据模拟）
    market_env = calc_market_env_from_data(data)

    # 找最强形态
    best_type = 'none'
    best_score = 0
    for k, v in bull.items():
        if v.get('score', 0) > best_score:
            best_score = v['score']
            best_type = k
    for k, v in bear.items():
        if v.get('score', 0) > best_score:
            best_score = v['score']
            best_type = k

    # 假信号风险
    fake = calc_fake_signal_risk(data, ind, best_type, best_score, market_env, macd_div, vol)

    return {
        'fixture_id': fixture['id'],
        'data_length': len(data),
        'bull_patterns': {k: {'score': v.get('score', 0), 'stage': v.get('stage', '')} for k, v in bull.items()},
        'bear_patterns': {k: {'score': v.get('score', 0), 'stage': v.get('stage', '')} for k, v in bear.items()},
        'volume': {
            'relation': vol.get('relation', ''),
            'volRatio': vol.get('volRatio', 0),
            'volStatus': vol.get('volStatus', '')
        },
        'fake_signal_risk': {
            'risk_score': fake.get('risk_score', 0),
            'risk_level': fake.get('risk_level', '')
        }
    }


def main():
    parser = argparse.ArgumentParser(description='JS/Python Parity Test Runner')
    parser.add_argument('--fixture', help='只运行指定fixture ID')
    parser.add_argument('--output', help='输出JSON文件路径')
    args = parser.parse_args()

    # 读取fixtures
    fixtures_path = os.path.join(os.path.dirname(__file__), 'parity_fixtures.json')
    with open(fixtures_path, 'r') as f:
        config = json.load(f)

    results = []
    for fixture in config['fixtures']:
        if args.fixture and fixture['id'] != args.fixture:
            continue
        print(f"Running fixture: {fixture['id']} ({fixture['name']})...")
        result = run_fixture(fixture)
        results.append(result)
        if 'error' in result:
            print(f"  ERROR: {result['error']}")
        else:
            print(f"  OK: data_length={result['data_length']}")

    output = {
        'version': config['version'],
        'generated_at': '2026-10-02',
        'results': results
    }

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"\nResults written to: {args.output}")
    else:
        print(json.dumps(output, indent=2, ensure_ascii=False))

    return 0


if __name__ == '__main__':
    sys.exit(main())
