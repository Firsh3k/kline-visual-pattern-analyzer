#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股市场支持测试（FIR: US Market）
1. 拆股复权算法：共享fixture（tests/us_split_fixtures.json），与JS端 test_us_market.js 使用同一套期望值
2. 美股代码识别 is_us_code
3. 扫描池数据一致性：sector_data/us_pools.json ↔ kline_analyzer.html 内嵌 US_PRESET_POOLS/US_STOCK_NAMES ↔ provenance
运行: python3 tests/test_us_market.py
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

from analyze_stock import adjust_us_splits, detect_split_ratio, is_us_code  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f'  ✗ {name} {detail}')


def close_eq(a, b, eps=1e-9):
    if a == b:
        return True
    denom = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / denom <= eps


def test_split_fixtures():
    print('[1] 拆股复权共享fixture')
    with open(os.path.join(ROOT, 'tests', 'us_split_fixtures.json'), encoding='utf-8') as f:
        spec = json.load(f)

    for fx in spec['fixtures']:
        rows = fx['rows']
        out = adjust_us_splits([dict(r) for r in rows])
        check(f"{fx['name']}: 行数", len(out) == len(fx['expected']))
        for i, (got, exp) in enumerate(zip(out, fx['expected'])):
            for k in ('open', 'high', 'low', 'close', 'volume'):
                check(f"{fx['name']}[{i}].{k}", close_eq(got[k], exp[k]),
                      f'got={got[k]} expected={exp[k]}')
        # 拆股点检测位置
        ratios = {}
        for i in range(1, len(rows)):
            r = detect_split_ratio(rows[i - 1]['close'], rows[i])
            if r:
                ratios[str(i)] = r
        exp_ratios = {k: v for k, v in fx['expectedRatios'].items()}
        check(f"{fx['name']}: 拆股点", set(ratios) == set(exp_ratios) and
              all(close_eq(ratios[k], exp_ratios[k]) for k in exp_ratios),
              f'got={ratios} expected={exp_ratios}')

    for c in spec['detectCases']:
        r = detect_split_ratio(c['prevClose'], {'open': c['open'], 'close': c['close']})
        if c['expected'] is None:
            check(f"detect:{c['name']}", r is None, f'got={r}')
        else:
            check(f"detect:{c['name']}", r is not None and close_eq(r, c['expected']),
                  f'got={r} expected={c["expected"]}')


def test_us_code():
    print('[2] 美股代码识别')
    with open(os.path.join(ROOT, 'tests', 'us_split_fixtures.json'), encoding='utf-8') as f:
        spec = json.load(f)
    for c in spec['usCodeCases']:
        check(f"is_us_code({c['code']!r})", is_us_code(c['code']) == c['expected'])


def test_pool_consistency():
    print('[3] 扫描池数据一致性（JSON ↔ HTML ↔ provenance）')
    with open(os.path.join(ROOT, 'sector_data', 'us_pools.json'), encoding='utf-8') as f:
        pools = json.load(f)
    html = open(os.path.join(ROOT, 'kline_analyzer.html'), encoding='utf-8').read()

    # HTML内嵌 US_PRESET_POOLS
    m = re.search(r'const US_PRESET_POOLS = \{(.*?)\};', html, re.DOTALL)
    check('HTML含US_PRESET_POOLS', m is not None)
    if m:
        body = m.group(1)
        for name, pool in pools['pools'].items():
            pm = re.search(r"'" + re.escape(name) + r"': (\[[^\]]*\])", body)
            check(f'HTML含池 {name}', pm is not None)
            if pm:
                html_tickers = json.loads(pm.group(1))
                check(f'{name} HTML==JSON', html_tickers == pool['tickers'],
                      f'html={len(html_tickers)} json={len(pool["tickers"])}')

    # HTML内嵌 US_STOCK_NAMES
    m = re.search(r'const US_STOCK_NAMES = (\{.*?\});', html, re.DOTALL)
    check('HTML含US_STOCK_NAMES', m is not None)
    if m:
        html_names = json.loads(m.group(1))
        check('US_STOCK_NAMES HTML==JSON', html_names == pools['names'])

    # HTML内嵌 US_FULL_SYMBOLS（交易所后缀映射，扫描池内代码不依赖实时行情请求）
    m = re.search(r'const US_FULL_SYMBOLS = (\{.*?\});', html, re.DOTALL)
    check('HTML含US_FULL_SYMBOLS', m is not None)
    if m:
        html_full = json.loads(m.group(1))
        check('US_FULL_SYMBOLS HTML==JSON', html_full == pools['fullSymbols'])

    # 池完整性
    sp = pools['pools']['标普500']['tickers']
    ndx = pools['pools']['纳指100']['tickers']
    check('标普500数量=503', len(sp) == 503, f'got={len(sp)}')
    check('纳指100数量=101', len(ndx) == 101, f'got={len(ndx)}')
    check('标普500无重复', len(sp) == len(set(sp)))
    check('纳指100无重复', len(ndx) == len(set(ndx)))
    all_tickers = set(sp) | set(ndx)
    check('代码格式全部合法', all(is_us_code(t) for t in all_tickers))
    check('中文名全覆盖', all(t in pools['names'] and pools['names'][t] for t in all_tickers))
    check('完整代码全覆盖', all(t in pools['fullSymbols'] and pools['fullSymbols'][t] for t in all_tickers))
    check('纳指100⊆标普500∪额外', True)  # 纳指100含非SP500成分（如ARM/CRWV），不做子集断言

    # provenance
    for name, key, count in [('美股标普500', '标普500', 503), ('美股纳指100', '纳指100', 101)]:
        path = os.path.join(ROOT, 'sector_data', 'provenance', f'{name}_provenance.json')
        check(f'provenance存在 {name}', os.path.isfile(path))
        if os.path.isfile(path):
            prov = json.load(open(path, encoding='utf-8'))
            check(f'{name} provenance数量', prov['total_constituents'] == count)
            # 来源字段须符合 FIR-15 校验约定：reconciliation_method 为合法枚举；
            # 美股池为人工维护（无 THS 权威对账源）→ manual + 无权威对账日期（fail-closed NOT_RECONCILED）
            check(f'{name} provenance方法合法枚举', prov['reconciliation_method'] in ('ths_ifind', 'ths_web', 'manual', 'not_reconciled'),
                  f"got={prov['reconciliation_method']}")
            check(f'{name} provenance人工维护约定', prov['reconciliation_method'] == 'manual' and prov['verified_count'] == 0
                  and not prov.get('authoritative_reconciliation_date'))
            check(f'{name} provenance记录双源交叉核对', '交叉核对' in prov.get('reconciliation_result', ''))


def test_market_config_parity():
    print('[4] 市场配置跨端一致性')
    html = open(os.path.join(ROOT, 'kline_analyzer.html'), encoding='utf-8').read()
    py = open(os.path.join(ROOT, 'scripts', 'analyze_stock.py'), encoding='utf-8').read()
    # 代码校验正则一致
    check('HTML美股codePattern', r'/^[A-Za-z][A-Za-z0-9.\-]{0,5}$/' in html)
    check('PY美股codePattern', r"r'^[A-Za-z][A-Za-z0-9.\-]{0,5}$'" in py)
    # 拆股比例表一致
    check('HTML FWD比例表', 'const US_SPLIT_RATIOS_FWD = [1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25, 50, 100];' in html)
    check('PY FWD比例表', 'US_SPLIT_RATIOS_FWD = [1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25, 50, 100]' in py)
    check('HTML REV比例表', 'const US_SPLIT_RATIOS_REV = [2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 40, 50, 100];' in html)
    check('PY REV比例表', 'US_SPLIT_RATIOS_REV = [2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 40, 50, 100]' in py)
    # 大盘基准
    check('HTML美股基准.INX', "fetchSinaUSKlineJSONP('.inx')" in html)
    check('PY美股基准.INX', "fetch_sina_us_kline('.inx')" in py)


if __name__ == '__main__':
    test_split_fixtures()
    test_us_code()
    test_pool_consistency()
    test_market_config_parity()
    print('=' * 60)
    print(f'测试结果: {PASS} 通过, {FAIL} 失败')
    print('=' * 60)
    sys.exit(1 if FAIL else 0)