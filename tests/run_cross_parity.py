#!/usr/bin/env python3
"""
Minimal Cross-Runtime Parity Gate
同时运行 Python 和 Node/JS 两端，自动 assert 关键 machine-readable outputs 一致。

覆盖领域（QA 优先级）：
1. Volume / volStatus / relation
2. Bollinger squeeze
3. Fake Signal Risk
4. H&S breakout / breakdown state

用法：
  python3 run_cross_parity.py [--fixture ID] [--verbose]
"""
import json
import os
import sys
import subprocess
import tempfile

# 添加 Python 模块路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skill', 'scripts'))

from analyze_stock import (
    calc_indicators, analyze_volume, detect_bollinger,
    detect_macd_divergence, calc_fake_signal_risk,
    match_patterns, match_bear_patterns
)


def generate_fixture_data(fixture):
    """根据fixture定义生成测试数据（与run_parity_tests.py一致）"""
    params = fixture.get('params', {})
    days = params.get('days', 100)
    data = []

    gen_type = fixture.get('data_generator', '')

    if 'uptrend' in gen_type or 'ma_bull' in gen_type:
        # 线性上涨
        for i in range(days):
            close = 10.0 + i * 0.1
            data.append(_make_bar(close, i))
    elif 'downtrend' in gen_type or 'ma_bear' in gen_type:
        # 线性下跌
        for i in range(days):
            close = 20.0 - i * 0.1
            data.append(_make_bar(close, i))
    elif 'w_bottom' in gen_type:
        # W底
        data = _generate_w_bottom(params, days)
    elif 'm_top' in gen_type:
        # M头
        data = _generate_m_top(params, days)
    elif 'hs_bottom' in gen_type:
        # 头肩底（各种颈线类型）
        data = _generate_hs_bottom(params, days, gen_type)
    elif 'hs_top' in gen_type:
        # 头肩顶
        data = _generate_hs_top(params, days)
    else:
        # 默认：横盘
        for i in range(days):
            close = 10.0 + (i % 5 - 2) * 0.2
            data.append(_make_bar(close, i))

    return data


def _make_bar(close, idx):
    return {
        'day': f'2026-{(idx // 30) + 1:02d}-{(idx % 28) + 1:02d}',
        'open': round(close - 0.1, 4),
        'high': round(close + 0.3, 4),
        'low': round(close - 0.3, 4),
        'close': round(close, 4),
        'volume': 1000000 + idx * 1000
    }


def _generate_w_bottom(params, days):
    data = []
    first_low = params.get('first_low', 8.0)
    second_low = params.get('second_low', 8.2)
    neckline = params.get('neckline', 12.0)
    breakout = params.get('breakout_price', 13.0)
    # 下跌到第一底
    for i in range(days // 4):
        t = i / (days // 4)
        close = 15.0 + (first_low - 15.0) * t
        data.append(_make_bar(close, len(data)))
    # 反弹到颈线
    for i in range(days // 4):
        t = i / (days // 4)
        close = first_low + (neckline - first_low) * t
        data.append(_make_bar(close, len(data)))
    # 下跌到第二底
    for i in range(days // 4):
        t = i / (days // 4)
        close = neckline + (second_low - neckline) * t
        data.append(_make_bar(close, len(data)))
    # 突破
    while len(data) < days:
        t = (len(data) - days * 3 // 4) / (days // 4)
        close = second_low + (breakout - second_low) * min(t, 1)
        data.append(_make_bar(close, len(data)))
    return data


def _generate_m_top(params, days):
    data = []
    first_high = params.get('first_high', 18.0)
    second_high = params.get('second_high', 17.6)
    neckline = params.get('neckline', 12.0)
    breakdown = params.get('breakdown_price', 11.0)
    for i in range(days // 4):
        t = i / (days // 4)
        close = 10.0 + (first_high - 10.0) * t
        data.append(_make_bar(close, len(data)))
    for i in range(days // 4):
        t = i / (days // 4)
        close = first_high + (neckline - first_high) * t
        data.append(_make_bar(close, len(data)))
    for i in range(days // 4):
        t = i / (days // 4)
        close = neckline + (second_high - neckline) * t
        data.append(_make_bar(close, len(data)))
    while len(data) < days:
        t = (len(data) - days * 3 // 4) / (days // 4)
        close = second_high + (breakdown - second_high) * min(t, 1)
        data.append(_make_bar(close, len(data)))
    return data


def _generate_hs_bottom(params, days, gen_type):
    data = []
    left_shoulder = params.get('left_shoulder', 12.0)
    head = params.get('head', 8.0)
    right_shoulder = params.get('right_shoulder', 11.5)
    neck_left = params.get('neck_left_price', params.get('neckline_price', 13.5))
    neck_right = params.get('neck_right_price', params.get('neckline_price', 13.5))
    breakout = params.get('breakout_price', 14.5)

    # 下跌到左肩
    p1 = days // 5
    for i in range(p1):
        t = i / p1
        close = 15.0 + (left_shoulder - 15.0) * t
        data.append(_make_bar(close, len(data)))
    # 下跌到头部
    p2 = days // 5
    for i in range(p2):
        t = i / p2
        close = left_shoulder + (head - left_shoulder) * t
        data.append(_make_bar(close, len(data)))
    # 反弹到左颈线
    p3 = days // 10
    for i in range(p3):
        t = i / p3
        close = head + (neck_left - head) * t
        data.append(_make_bar(close, len(data)))
    # 下跌到右肩
    p4 = days // 5
    for i in range(p4):
        t = i / p4
        close = neck_left + (right_shoulder - neck_left) * t
        data.append(_make_bar(close, len(data)))
    # 上涨到右颈线
    p5 = days // 10
    for i in range(p5):
        t = i / p5
        close = right_shoulder + (neck_right - right_shoulder) * t
        data.append(_make_bar(close, len(data)))
    # 突破
    while len(data) < days:
        t = (len(data) - days * 4 // 5) / (days // 5)
        close = neck_right + (breakout - neck_right) * min(t, 1)
        data.append(_make_bar(close, len(data)))
    return data


def _generate_hs_top(params, days):
    data = []
    left_shoulder = params.get('left_shoulder', 13.0)
    head = params.get('head', 17.0)
    right_shoulder = params.get('right_shoulder', 13.5)
    neckline = params.get('neckline', 12.0)
    breakdown = params.get('breakdown_price', 11.0)

    p1 = days // 5
    for i in range(p1):
        t = i / p1
        close = 10.0 + (left_shoulder - 10.0) * t
        data.append(_make_bar(close, len(data)))
    p2 = days // 5
    for i in range(p2):
        t = i / p2
        close = left_shoulder + (head - left_shoulder) * t
        data.append(_make_bar(close, len(data)))
    p3 = days // 10
    for i in range(p3):
        t = i / p3
        close = head + (neckline - head) * t
        data.append(_make_bar(close, len(data)))
    p4 = days // 5
    for i in range(p4):
        t = i / p4
        close = neckline + (right_shoulder - neckline) * t
        data.append(_make_bar(close, len(data)))
    p5 = days // 10
    for i in range(p5):
        t = i / p5
        close = right_shoulder + (neckline - right_shoulder) * t
        data.append(_make_bar(close, len(data)))
    while len(data) < days:
        t = (len(data) - days * 4 // 5) / (days // 5)
        close = neckline + (breakdown - neckline) * min(t, 1)
        data.append(_make_bar(close, len(data)))
    return data


def run_python_analysis(data):
    """运行Python端分析"""
    ind = calc_indicators(data)
    vol = analyze_volume(data, ind)
    boll = detect_bollinger(data, ind)
    macd_div = detect_macd_divergence(data, ind)
    bull_matches = match_patterns(data, ind)
    bear_matches = match_bear_patterns(data, ind)
    hs_bottom = bull_matches.get('hs_bottom', {'score': 0, 'stage': ''})
    hs_top = bear_matches.get('hs_top', {'score': 0, 'stage': ''})
    # fake_signal_risk使用固定bestScore测试（消除H&S检测差异影响，确保输入完全一致）
    fake_risk_bull_high = calc_fake_signal_risk(data, ind, 'bull', 70, None, macd_div, vol)
    fake_risk_bull_low = calc_fake_signal_risk(data, ind, 'bull', 30, None, macd_div, vol)
    fake_risk_bear_high = calc_fake_signal_risk(data, ind, 'bear', 70, None, macd_div, vol)

    return {
        'volume': {
            'volRatio': vol.get('volRatio'),
            'relation': vol.get('relation'),
            'volStatus': vol.get('volStatus')
        },
        'bollinger': {
            'isSqueeze': boll.get('is_squeeze', boll.get('isSqueeze', False)),
            'widthPercentile': str(boll.get('width_percentile', boll.get('widthPercentile', 0))),
            'signal': boll.get('signal', boll.get('detail', '')),
            'score': boll.get('score')
        },
        'fake_signal_risk_bull_high': {
            'riskScore': fake_risk_bull_high.get('risk_score'),
            'level': fake_risk_bull_high.get('level'),
            'riskItems': [r.get('label') for r in fake_risk_bull_high.get('risks', [])]
        },
        'fake_signal_risk_bull_low': {
            'riskScore': fake_risk_bull_low.get('risk_score'),
            'level': fake_risk_bull_low.get('level'),
            'riskItems': [r.get('label') for r in fake_risk_bull_low.get('risks', [])]
        },
        'fake_signal_risk_bear_high': {
            'riskScore': fake_risk_bear_high.get('risk_score'),
            'level': fake_risk_bear_high.get('level'),
            'riskItems': [r.get('label') for r in fake_risk_bear_high.get('risks', [])]
        },
        'hs_bottom': {
            'score': hs_bottom.get('score'),
            'breakoutState': hs_bottom.get('breakout_state', '')
        },
        'hs_top': {
            'score': hs_top.get('score'),
            'breakdownState': hs_top.get('breakdown_state', '')
        }
    }


def run_js_analysis(data):
    """运行JS端分析（通过Node.js）"""
    # 将数据写入临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(data, f)
        data_file = f.name

    # 创建JS运行脚本
    js_script = f"""
const core = require('{os.path.join(os.path.dirname(__file__), 'kline_core.js')}');
const fs = require('fs');
const data = JSON.parse(fs.readFileSync('{data_file}', 'utf8'));
const result = core.analyze(data, null);
const ind = core.calcIndicators(data);
const vol = core.analyzeVolume(data, ind);
const macdDiv = core.detectMACDDivergence(data, ind);
console.log(JSON.stringify({{
    volume: {{
        volRatio: result.volume.volRatio,
        relation: result.volume.relation,
        volStatus: result.volume.volStatus
    }},
    bollinger: {{
        isSqueeze: result.bollinger.isSqueeze,
        widthPercentile: result.bollinger.widthPercentile,
        signal: result.bollinger.signal,
        score: result.bollinger.score
    }},
    fake_signal_risk_bull_high: {{
        riskScore: core.calcFakeSignalRisk(data, ind, 'bull', 70, null, macdDiv, vol).riskScore,
        level: core.calcFakeSignalRisk(data, ind, 'bull', 70, null, macdDiv, vol).level,
        riskItems: core.calcFakeSignalRisk(data, ind, 'bull', 70, null, macdDiv, vol).risks
    }},
    fake_signal_risk_bull_low: {{
        riskScore: core.calcFakeSignalRisk(data, ind, 'bull', 30, null, macdDiv, vol).riskScore,
        level: core.calcFakeSignalRisk(data, ind, 'bull', 30, null, macdDiv, vol).level,
        riskItems: core.calcFakeSignalRisk(data, ind, 'bull', 30, null, macdDiv, vol).risks
    }},
    fake_signal_risk_bear_high: {{
        riskScore: core.calcFakeSignalRisk(data, ind, 'bear', 70, null, macdDiv, vol).riskScore,
        level: core.calcFakeSignalRisk(data, ind, 'bear', 70, null, macdDiv, vol).level,
        riskItems: core.calcFakeSignalRisk(data, ind, 'bear', 70, null, macdDiv, vol).risks
    }},
    hs_bottom: {{
        score: result.hs_bottom.score,
        breakoutState: result.hs_bottom.breakoutState
    }},
    hs_top: {{
        score: result.hs_top.score,
        breakdownState: result.hs_top.breakdownState
    }}
}}));
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(js_script)
        js_file = f.name

    try:
        result = subprocess.run(
            ['node', js_file],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            print(f"  JS执行错误: {result.stderr}")
            return None
        return json.loads(result.stdout.strip())
    finally:
        os.unlink(data_file)
        os.unlink(js_file)


def compare_results(py_result, js_result, fixture_id, verbose=False):
    """对比Python和JS结果，返回差异列表"""
    diffs = []

    # 1. Volume 对比
    py_vol = py_result['volume']
    js_vol = js_result['volume']
    if py_vol['volStatus'] != js_vol['volStatus']:
        diffs.append(f"volume.volStatus: Python={py_vol['volStatus']}, JS={js_vol['volStatus']}")
    if py_vol['relation'] != js_vol['relation']:
        diffs.append(f"volume.relation: Python={py_vol['relation']}, JS={js_vol['relation']}")

    # 2. Bollinger 对比（只对比isSqueeze，signal字段两端定义不同）
    py_boll = py_result['bollinger']
    js_boll = js_result['bollinger']
    if bool(py_boll['isSqueeze']) != bool(js_boll['isSqueeze']):
        diffs.append(f"bollinger.isSqueeze: Python={py_boll['isSqueeze']}, JS={js_boll['isSqueeze']}")

    # 3. Fake Signal Risk 对比（使用固定bestScore，要求100%一致：riskScore/level/riskItems）
    for risk_type in ['fake_signal_risk_bull_high', 'fake_signal_risk_bull_low', 'fake_signal_risk_bear_high']:
        py_risk = py_result[risk_type]
        js_risk = js_result[risk_type]
        # riskScore必须完全一致（输入相同，输出必须相同）
        if py_risk['riskScore'] != js_risk['riskScore']:
            diffs.append(f"{risk_type}.riskScore: Python={py_risk['riskScore']}, JS={js_risk['riskScore']}")
        # level必须完全一致
        if py_risk['level'] != js_risk['level']:
            diffs.append(f"{risk_type}.level: Python={py_risk['level']}, JS={js_risk['level']}")
        # riskItems必须完全一致（风险项列表）
        if py_risk.get('riskItems', []) != js_risk.get('riskItems', []):
            diffs.append(f"{risk_type}.riskItems: Python={py_risk.get('riskItems', [])}, JS={js_risk.get('riskItems', [])}")

    # 4. H&S breakout/breakdown state 对比（只对比状态机状态，不对比分数，因为JS端是简化版本）
    if py_result['hs_bottom'].get('breakoutState') and js_result['hs_bottom'].get('breakoutState'):
        if py_result['hs_bottom']['breakoutState'] != js_result['hs_bottom']['breakoutState']:
            diffs.append(f"hs_bottom.breakoutState: Python={py_result['hs_bottom']['breakoutState']}, JS={js_result['hs_bottom']['breakoutState']}")

    if py_result['hs_top'].get('breakdownState') and js_result['hs_top'].get('breakdownState'):
        if py_result['hs_top']['breakdownState'] != js_result['hs_top']['breakdownState']:
            diffs.append(f"hs_top.breakdownState: Python={py_result['hs_top']['breakdownState']}, JS={js_result['hs_top']['breakdownState']}")

    return diffs


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Cross-Runtime Parity Gate')
    parser.add_argument('--fixture', type=str, default=None, help='只运行指定fixture')
    parser.add_argument('--verbose', action='store_true', help='详细输出')
    args = parser.parse_args()

    # 加载fixtures
    fixtures_path = os.path.join(os.path.dirname(__file__), 'parity_fixtures.json')
    with open(fixtures_path, 'r') as f:
        fixtures_data = json.load(f)

    fixtures = fixtures_data['fixtures']
    if args.fixture:
        fixtures = [f for f in fixtures if f['id'] == args.fixture]

    print("=" * 70)
    print("Minimal Cross-Runtime Parity Gate")
    print("Python vs Node/JS - 关键输出一致性验证")
    print("=" * 70)
    print()

    total = 0
    passed = 0
    failed = 0

    for fixture in fixtures:
        fid = fixture['id']
        fname = fixture.get('name', fid)
        total += 1

        print(f"[{total}] {fid} - {fname}")

        # 生成数据（优先使用fixture中已有的data字段）
        if 'data' in fixture and fixture['data']:
            data = fixture['data']
        else:
            data = generate_fixture_data(fixture)
        print(f"  数据: {len(data)} 根K线")

        # 运行Python
        py_result = run_python_analysis(data)
        print(f"  Python: volStatus={py_result['volume']['volStatus']}, "
              f"bollSqueeze={py_result['bollinger']['isSqueeze']}, "
              f"riskBullHigh={py_result['fake_signal_risk_bull_high']['riskScore']}")

        # 运行JS
        js_result = run_js_analysis(data)
        if js_result is None:
            print(f"  ❌ JS执行失败")
            failed += 1
            continue
        print(f"  JS:     volStatus={js_result['volume']['volStatus']}, "
              f"bollSqueeze={js_result['bollinger']['isSqueeze']}, "
              f"riskBullHigh={js_result['fake_signal_risk_bull_high']['riskScore']}")

        # 对比
        diffs = compare_results(py_result, js_result, fid, args.verbose)
        if diffs:
            print(f"  ❌ 发现 {len(diffs)} 个差异:")
            for d in diffs:
                print(f"     - {d}")
            failed += 1
        else:
            print(f"  ✅ 一致")
            passed += 1
        print()

    print("=" * 70)
    print(f"结果: {passed}/{total} 通过, {failed} 失败")
    print("=" * 70)

    return failed == 0


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
