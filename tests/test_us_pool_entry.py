#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美股扫描池入口确定性测试（不依赖当天行情）。

覆盖：
1. _find_us_pools() 从 scripts/、skill/scripts/、根目录 legacy 三种入口深度均能定位
   sector_data/us_pools.json（修复 skill/scripts/batch_scan.py --us-pool 找不到文件的缺陷）。
2. skill/scripts/batch_scan.py scan_us_pool 对 sp500 / nasdaq100 两池用固定桩数据跑通，
   产出 JSON+MD，计数与池成分一致。analyze/fetch_market_env 全部 monkeypatch，零网络。

运行：python3 tests/test_us_pool_entry.py
"""
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ✓ {name}')
    else:
        FAIL += 1
        print(f'  ✗ {name} {detail}')


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


print('1) _find_us_pools 三入口深度定位')
expected = os.path.join(ROOT, 'sector_data', 'us_pools.json')
for entry in ['scripts', 'skill/scripts', '.']:
    d = os.path.join(ROOT, entry) if entry != '.' else ROOT
    src = os.path.join(d, 'analyze_stock.py')
    if not os.path.isfile(src):
        # 根目录 legacy 无 analyze_stock.py，用 scripts 副本但模拟 __file__ 在根目录
        mod = load_module(f'as_{entry.replace("/", "_")}', os.path.join(ROOT, 'scripts', 'analyze_stock.py'))
        mod.__file__ = os.path.join(ROOT, 'analyze_stock.py')
    else:
        mod = load_module(f'as_{entry.replace("/", "_")}', src)
    found = mod._find_us_pools()
    check(f'入口 {entry} 定位 us_pools.json', found == expected, f'found={found}')

print('2) skill 批扫入口两池固定数据跑通（零网络）')
sys.path.insert(0, os.path.join(ROOT, 'skill', 'scripts'))
import analyze_stock as skill_as  # noqa: E402  (skill/scripts 副本)

FAKE_ENV = {'score': 30, 'stage': '弱势震荡', 'close': 6000.0, 'change_pct': -0.5}


def fake_analyze(code, name=None, market_env=None):
    return {'code': code, 'name': name or code, 'close': 100.0, 'change_pct': 1.0,
            'best_pattern_name': 'W底', 'attention_score': 70, 'attention_level': '强烈关注',
            'buy_accuracy': 60, 'sell_accuracy': 50}


skill_as.analyze = fake_analyze
skill_as.fetch_market_env = lambda market='a': FAKE_ENV
skill_as._US_POOL_META = None  # 强制重新定位

import batch_scan as skill_bs  # noqa: E402
# scan_us_pool 内部 from analyze_stock import ... 会取到上面 monkeypatch 后的 skill 副本
check('skill batch_scan 与 analyze_stock 同源', skill_bs.__file__.startswith(os.path.join(ROOT, 'skill', 'scripts')))

with open(expected, encoding='utf-8') as f:
    pools = json.load(f)

with tempfile.TemporaryDirectory() as tmp:
    for key, cname in [('sp500', '标普500'), ('nasdaq100', '纳指100')]:
        n = len(pools['pools'][cname]['tickers'])
        jp = skill_bs.scan_us_pool(key, tmp, min_score=60)
        check(f'{key} 产出JSON', os.path.isfile(jp))
        with open(jp, encoding='utf-8') as f:
            doc = json.load(f)
        check(f'{key} 计数一致 total={n}', doc['total'] == n and doc['success'] == n and doc['failed'] == 0,
              f"total={doc['total']} success={doc['success']}")
        check(f'{key} 推荐度过滤生效', all(r['attention_score'] >= 60 for r in doc['results']) and len(doc['results']) == n)
        md = jp.replace('.json', '.md')
        check(f'{key} 产出MD', os.path.isfile(md))
        check(f'{key} 大盘环境注入', doc['market_env']['score'] == 30)

print(f'\n结果: {PASS} 通过, {FAIL} 失败')
sys.exit(1 if FAIL else 0)