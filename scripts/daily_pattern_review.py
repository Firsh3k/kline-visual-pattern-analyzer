#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日个股形态复盘脚本
使用 V5 Swing Pivot 算法批量分析自选股，生成形态复盘报告。

用法:
    python3 scripts/daily_pattern_review.py                    # 分析默认自选股
    python3 scripts/daily_pattern_review.py 603068,688105      # 分析指定股票
    python3 scripts/daily_pattern_review.py --watchlist       # 从GitHub配置读取自选股
    python3 scripts/daily_pattern_review.py --output report   # 指定输出目录
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

# 项目路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import FrequencyType, AdjustmentType
from core.market_data.sina_provider import SinaDataProvider
from core.pivot_patterns import (
    HSBottomDetector, WBottomDetector,
    HSTopDetector, MTopDetector,
)
from core.swing_pivot import PivotDetector


# ============================================================
# 股票名称映射（常用）
# ============================================================
STOCK_NAMES = {
    '603068': '博通集成',
    '688105': '诺唯赞',
    '002119': '康强电子',
    '600345': '长江通信',
    '300684': '中石科技',
    '301080': '百普赛斯',
    '300647': '超频三',
    '688432': '有研硅',
    '300170': '汉得信息',
    '002436': '兴森科技',
    '300602': '飞荣达',
    '301361': '众智科技',
    '601288': '农业银行',
    '002415': '海康威视',
    '000858': '五粮液',
    '600519': '贵州茅台',
}


def get_stock_name(code: str) -> str:
    return STOCK_NAMES.get(code, code)


# ============================================================
# 数据获取
# ============================================================
def fetch_klines(code: str, days: int = 250) -> List[Any]:
    """获取股票K线数据"""
    provider = SinaDataProvider()
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=days * 1.5)).strftime('%Y-%m-%d')

    try:
        result = provider.fetch_klines(
            code,
            start_date=start_date,
            end_date=end_date,
            frequency=FrequencyType.DAILY,
            adjustment=AdjustmentType.NONE,
        )
        return result.to_ohlcv_list()
    except Exception as e:
        print(f"  [WARN] {code} 数据获取失败: {e}")
        return []


# ============================================================
# 形态分析
# ============================================================
def analyze_patterns(klines: List[Any]) -> Dict[str, Any]:
    """分析单只股票的所有形态"""
    if not klines or len(klines) < 60:
        return {'error': '数据不足', 'patterns': []}

    detectors = {
        'head_shoulder_bottom': {
            'name': '头肩底',
            'detector': HSBottomDetector(),
            'direction': 'bull',
        },
        'w_bottom': {
            'name': 'W底',
            'detector': WBottomDetector(),
            'direction': 'bull',
        },
        'head_shoulder_top': {
            'name': '头肩顶',
            'detector': HSTopDetector(),
            'direction': 'bear',
        },
        'm_top': {
            'name': 'M头',
            'detector': MTopDetector(),
            'direction': 'bear',
        },
    }

    all_patterns = []
    for pattern_id, config in detectors.items():
        try:
            results = config['detector'].detect(klines)
            for r in results:
                # 转换 PatternResult 为字典
                if hasattr(r, '__dict__'):
                    pattern_dict = {
                        'pattern_id': r.pattern_id,
                        'pattern_name': r.pattern_name,
                        'direction': config['direction'],
                        'stage': r.stage.value if hasattr(r.stage, 'value') else str(r.stage),
                        'score': r.score,
                        'confidence': r.confidence.value if hasattr(r.confidence, 'value') else str(r.confidence),
                        'neckline_price': r.neckline_price,
                        'breakout_price': r.breakout_price,
                        'target_price': r.target_price,
                        'stop_loss': r.stop_loss,
                        'notes': r.notes,
                        'key_points': [
                            {
                                'name': kp.name,
                                'date': kp.date,
                                'price': kp.price,
                                'description': kp.description,
                            }
                            for kp in r.key_points
                        ] if hasattr(r, 'key_points') else [],
                    }
                    all_patterns.append(pattern_dict)
        except Exception as e:
            print(f"  [WARN] {config['name']} 检测失败: {e}")

    # 按评分排序
    all_patterns.sort(key=lambda x: x['score'], reverse=True)

    # 统计
    bull_patterns = [p for p in all_patterns if p['direction'] == 'bull']
    bear_patterns = [p for p in all_patterns if p['direction'] == 'bear']

    # 综合评分
    bull_score = max([p['score'] for p in bull_patterns], default=0)
    bear_score = max([p['score'] for p in bear_patterns], default=0)

    # 推荐度
    if bull_score >= 80 and bear_score < 60:
        recommendation = '强烈关注'
        rec_level = 'S'
    elif bull_score >= 60 and bear_score < 60:
        recommendation = '关注'
        rec_level = 'A'
    elif bear_score >= 80 and bull_score < 60:
        recommendation = '警惕下跌'
        rec_level = 'S-'
    elif bear_score >= 60 and bull_score < 60:
        recommendation = '谨慎'
        rec_level = 'B'
    elif bull_score >= 60 and bear_score >= 60:
        recommendation = '多空分歧'
        rec_level = 'C'
    else:
        recommendation = '观望'
        rec_level = 'D'

    return {
        'patterns': all_patterns,
        'bull_score': bull_score,
        'bear_score': bear_score,
        'recommendation': recommendation,
        'rec_level': rec_level,
        'bull_count': len(bull_patterns),
        'bear_count': len(bear_patterns),
    }


# ============================================================
# 报告生成
# ============================================================
def generate_report(results: List[Dict[str, Any]], output_dir: str) -> str:
    """生成复盘报告"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = datetime.now().strftime('%Y-%m-%d')

    # JSON 报告
    json_path = os.path.join(output_dir, f'pattern_review_{date_str}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({
            'date': date_str,
            'generated_at': datetime.now().isoformat(),
            'total_stocks': len(results),
            'results': results,
        }, f, ensure_ascii=False, indent=2, default=str)

    # 文本报告
    txt_path = os.path.join(output_dir, f'pattern_review_{date_str}.txt')
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write('=' * 70 + '\n')
        f.write(f'  每日个股形态复盘报告 - {date_str}\n')
        f.write('=' * 70 + '\n\n')

        # 汇总统计
        s_count = sum(1 for r in results if r.get('rec_level') == 'S')
        a_count = sum(1 for r in results if r.get('rec_level') == 'A')
        sb_count = sum(1 for r in results if r.get('rec_level') == 'S-')
        b_count = sum(1 for r in results if r.get('rec_level') == 'B')

        f.write(f'分析股票数: {len(results)}\n')
        f.write(f'S级(强烈关注): {s_count}  A级(关注): {a_count}  ')
        f.write(f'S-级(警惕下跌): {sb_count}  B级(谨慎): {b_count}\n\n')

        # 按推荐度排序
        sorted_results = sorted(results, key=lambda x: {
            'S': 0, 'A': 1, 'S-': 2, 'B': 3, 'C': 4, 'D': 5
        }.get(x.get('rec_level', 'D'), 5))

        for r in sorted_results:
            code = r['code']
            name = r['name']
            rec = r.get('recommendation', '未知')
            level = r.get('rec_level', 'D')
            bull = r.get('bull_score', 0)
            bear = r.get('bear_score', 0)

            f.write('-' * 70 + '\n')
            f.write(f'【{level}】{name} ({code})  {rec}\n')
            f.write(f'  底部信号: {bull}/100  顶部信号: {bear}/100\n')

            if r.get('error'):
                f.write(f'  错误: {r["error"]}\n')
                continue

            patterns = r.get('patterns', [])
            if not patterns:
                f.write('  未检测到典型形态\n')
            else:
                for p in patterns[:3]:  # 只显示前3个
                    pname = p['pattern_name']
                    score = p['score']
                    stage = p['stage']
                    neckline = p.get('neckline_price', '-')
                    direction = '↑' if p['direction'] == 'bull' else '↓'
                    f.write(f'  {direction} {pname} ({score}分) - {stage}\n')
                    f.write(f'    颈线: {neckline}  目标: {p.get("target_price", "-")}  止损: {p.get("stop_loss", "-")}\n')

                    # 关键点
                    kps = p.get('key_points', [])
                    if kps:
                        kp_str = '  '.join([f'{kp["name"]}:{kp["price"]}' for kp in kps[:4]])
                        f.write(f'    关键点: {kp_str}\n')

            f.write('\n')

        f.write('=' * 70 + '\n')
        f.write('报告结束\n')

    return txt_path, json_path


# ============================================================
# 自选股读取
# ============================================================
def load_watchlist_from_github() -> List[str]:
    """从GitHub配置读取自选股"""
    import urllib.request

    url = 'https://api.github.com/repos/Firsh3k/kline-visual-pattern-analyzer/contents/config/watchlist.json'
    token = os.environ.get('GITHUB_TOKEN', '')

    req = urllib.request.Request(url)
    if token:
        req.add_header('Authorization', f'token {token}')
    req.add_header('Accept', 'application/vnd.github.v3+json')

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            import base64
            content = base64.b64decode(data['content']).decode('utf-8')
            config = json.loads(content)
            return config.get('watchlist', [])
    except Exception as e:
        print(f"[WARN] 从GitHub读取自选股失败: {e}")
        return []


# ============================================================
# 主函数
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='每日个股形态复盘')
    parser.add_argument('codes', nargs='?', help='股票代码，逗号分隔（如 603068,688105）')
    parser.add_argument('--watchlist', action='store_true', help='从GitHub配置读取自选股')
    parser.add_argument('--output', default='reports', help='输出目录')
    parser.add_argument('--days', type=int, default=250, help='K线天数')

    args = parser.parse_args()

    # 确定股票列表
    if args.codes:
        codes = [c.strip() for c in args.codes.split(',') if c.strip()]
    elif args.watchlist:
        codes = load_watchlist_from_github()
        if not codes:
            print("未获取到自选股，使用默认列表")
            codes = list(STOCK_NAMES.keys())
    else:
        # 默认使用常用自选股
        codes = list(STOCK_NAMES.keys())

    print(f'=' * 60)
    print(f'  每日个股形态复盘')
    print(f'  日期: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'  分析股票数: {len(codes)}')
    print(f'=' * 60)
    print()

    results = []
    for i, code in enumerate(codes, 1):
        name = get_stock_name(code)
        print(f'[{i}/{len(codes)}] 分析 {name} ({code})...')

        # 获取数据
        klines = fetch_klines(code, args.days)
        if not klines:
            results.append({
                'code': code,
                'name': name,
                'error': '数据获取失败',
                'patterns': [],
                'bull_score': 0,
                'bear_score': 0,
                'recommendation': '数据缺失',
                'rec_level': 'D',
            })
            continue

        # 形态分析
        analysis = analyze_patterns(klines)
        analysis['code'] = code
        analysis['name'] = name
        analysis['latest_close'] = klines[-1].close
        analysis['latest_date'] = klines[-1].date
        analysis['data_count'] = len(klines)
        results.append(analysis)

        # 即时输出
        rec = analysis.get('recommendation', '')
        level = analysis.get('rec_level', '')
        bull = analysis.get('bull_score', 0)
        bear = analysis.get('bear_score', 0)
        top_pattern = analysis['patterns'][0]['pattern_name'] if analysis['patterns'] else '无'
        print(f'  → {level} {rec} | 底部{bull}/顶部{bear} | 最强形态: {top_pattern}')

    print()
    print('=' * 60)
    print('  生成报告...')

    # 生成报告
    output_dir = os.path.join(BASE_DIR, args.output)
    txt_path, json_path = generate_report(results, output_dir)

    print(f'  文本报告: {txt_path}')
    print(f'  JSON报告: {json_path}')
    print('=' * 60)

    # 输出S级股票
    s_stocks = [r for r in results if r.get('rec_level') == 'S']
    if s_stocks:
        print('\n【S级强烈关注】')
        for r in s_stocks:
            print(f"  {r['name']} ({r['code']}): 底部{r['bull_score']}分")

    sb_stocks = [r for r in results if r.get('rec_level') == 'S-']
    if sb_stocks:
        print('\n【S-级警惕下跌】')
        for r in sb_stocks:
            print(f"  {r['name']} ({r['code']}): 顶部{r['bear_score']}分")


if __name__ == '__main__':
    main()
