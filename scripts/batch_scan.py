#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量K线形态扫描器
扫描data目录下所有CSV文件，汇总近期（默认最近10个交易日）出现上涨形态信号的股票。
"""

import os
import sys
import json
from datetime import datetime, timedelta

import pandas as pd

# 导入主模块的函数
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pattern_scanner import load_data, calc_indicators, detect_all_patterns


def batch_scan(data_dir, output_dir, recent_days=10):
    """批量扫描data目录下所有CSV文件"""
    results = []
    csv_files = sorted([f for f in os.listdir(data_dir) if f.endswith('.csv')])

    print(f"发现 {len(csv_files)} 个股票数据文件\n")

    for csv_file in csv_files:
        code = csv_file.replace('.csv', '')
        csv_path = os.path.join(data_dir, csv_file)

        try:
            df = load_data(csv_path)
            df = calc_indicators(df)
            signals = detect_all_patterns(df)

            # 股票名称（从CSV中无法获取，用代码代替）
            stock_name = code

            # 筛选最近N天的信号
            cutoff_date = df['date'].iloc[-1] - timedelta(days=recent_days * 2)  # 自然日约为交易日2倍
            recent_signals = [s for s in signals if s['date'] >= cutoff_date]

            result = {
                'code': code,
                'name': stock_name,
                'total_signals': len(signals),
                'recent_signals': recent_signals,
                'latest_close': df.iloc[-1]['close'],
                'latest_date': df.iloc[-1]['date'],
                'latest_pct': df.iloc[-1]['pct_change'],
                'data_days': len(df),
            }
            results.append(result)

            # 打印摘要
            signal_types = [s['type'] for s in recent_signals]
            signal_str = ', '.join(signal_types) if signal_types else '无'
            print(f"  {code} | 最新{df.iloc[-1]['close']:.2f}({df.iloc[-1]['pct_change']:+.2f}%) | "
                  f"近{recent_days}日信号: {signal_str}")

        except Exception as e:
            print(f"  {code} | 分析失败: {e}")
            results.append({'code': code, 'error': str(e)})

    # 按近期信号数量排序
    valid_results = [r for r in results if 'error' not in r]
    valid_results.sort(key=lambda x: len(x['recent_signals']), reverse=True)

    # 生成汇总报告
    generate_batch_report(valid_results, output_dir, recent_days)

    return valid_results


def generate_batch_report(results, output_dir, recent_days):
    """生成批量扫描汇总HTML报告"""
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, 'batch_scan_report.html')

    # 有信号的股票
    stocks_with_signals = [r for r in results if len(r['recent_signals']) > 0]
    stocks_without_signals = [r for r in results if len(r['recent_signals']) == 0]

    # 构建表格行
    rows_html = ''
    for r in stocks_with_signals:
        signal_badges = ' '.join([
            f'<span class="badge badge-{s["type_id"]}">{s["type"]}</span>'
            for s in r['recent_signals']
        ])
        signal_details = '<br>'.join([
            f'<small>{s["date"].strftime("%m-%d")} [{s["strength"]}] {s["desc"]}</small>'
            for s in r['recent_signals']
        ])
        pct_color = '#e74c3c' if r['latest_pct'] >= 0 else '#2ecc71'
        rows_html += f'''
        <tr>
            <td><strong>{r['code']}</strong></td>
            <td>{r['latest_close']:.2f}</td>
            <td style="color:{pct_color}">{r['latest_pct']:+.2f}%</td>
            <td>{signal_badges}</td>
            <td style="text-align:left;font-size:12px;">{signal_details}</td>
            <td><a href="{r['code']}_report.html" target="_blank">查看详情</a></td>
        </tr>'''

    if not rows_html:
        rows_html = f'<tr><td colspan="6" style="text-align:center;color:#999;padding:20px;">近{recent_days}个交易日内未检测到上涨形态信号</td></tr>'

    # 无信号股票
    no_signal_html = ''
    if stocks_without_signals:
        no_signal_codes = ', '.join([r['code'] for r in stocks_without_signals])
        no_signal_html = f'<div style="margin-top:16px;color:#95a5a6;font-size:13px;">近期无信号股票: {no_signal_codes}</div>'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>K线形态批量扫描报告</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; background:#f5f6fa; color:#2c3e50; line-height:1.6; }}
.container {{ max-width:1200px; margin:0 auto; padding:20px; }}
.header {{ background:linear-gradient(135deg, #2c3e50, #34495e); color:white; padding:30px; border-radius:12px; margin-bottom:20px; }}
.header h1 {{ font-size:24px; margin-bottom:8px; }}
.header .meta {{ font-size:14px; opacity:0.85; }}
.summary {{ display:flex; gap:20px; margin-top:15px; flex-wrap:wrap; }}
.summary-item {{ background:rgba(255,255,255,0.1); padding:12px 20px; border-radius:8px; }}
.summary-item .label {{ font-size:12px; opacity:0.7; }}
.summary-item .value {{ font-size:22px; font-weight:bold; }}
.section {{ background:white; border-radius:12px; padding:24px; margin-bottom:20px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
.section h2 {{ font-size:18px; margin-bottom:16px; padding-bottom:10px; border-bottom:2px solid #ecf0f1; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th {{ background:#f8f9fa; padding:12px 8px; text-align:center; font-weight:600; border-bottom:2px solid #dee2e6; }}
td {{ padding:10px 8px; text-align:center; border-bottom:1px solid #f1f2f6; }}
tr:hover {{ background:#f8f9fa; }}
.badge {{ display:inline-block; padding:3px 10px; border-radius:12px; font-size:11px; font-weight:bold; color:white; margin:2px; }}
.badge-1 {{ background:#e74c3c; }}
.badge-2 {{ background:#3498db; }}
.badge-3 {{ background:#9b59b6; }}
.badge-4 {{ background:#1abc9c; }}
.badge-5 {{ background:#f39c12; }}
.badge-6 {{ background:#e67e22; }}
.disclaimer {{ background:#fff3cd; border-left:4px solid #ffc107; padding:16px; border-radius:8px; font-size:13px; color:#856404; margin-top:20px; }}
.footer {{ text-align:center; padding:20px; color:#95a5a6; font-size:12px; }}
a {{ color:#3498db; text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>K线形态批量扫描报告</h1>
        <div class="meta">扫描时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 扫描范围: 最近{recent_days}个交易日 | 共扫描{len(results)}只股票</div>
        <div class="summary">
            <div class="summary-item"><div class="label">扫描股票数</div><div class="value">{len(results)}</div></div>
            <div class="summary-item"><div class="label">有信号股票</div><div class="value" style="color:#f39c12;">{len(stocks_with_signals)}</div></div>
            <div class="summary-item"><div class="label">信号总数</div><div class="value">{sum(len(r['recent_signals']) for r in results)}</div></div>
        </div>
    </div>

    <div class="section">
        <h2>近期出现上涨形态的股票</h2>
        <table>
            <thead><tr><th>股票代码</th><th>最新价</th><th>涨跌幅</th><th>形态信号</th><th>信号详情</th><th>操作</th></tr></thead>
            <tbody>{rows_html}</tbody>
        </table>
        {no_signal_html}
    </div>

    <div class="section">
        <h2>六种上涨形态说明</h2>
        <table>
            <thead><tr><th>形态</th><th>量化特征</th><th>对应场景</th><th>强度</th></tr></thead>
            <tbody>
                <tr><td><span class="badge badge-1">均线多头突破</span></td><td>MA5>MA10>MA20>MA60，突破20日新高</td><td>主升浪启动</td><td>强</td></tr>
                <tr><td><span class="badge badge-2">MACD零上金叉</span></td><td>DIF上穿DEA，二者均在零轴上方</td><td>强势反弹/续涨</td><td>中强</td></tr>
                <tr><td><span class="badge badge-3">放量突破平台</span></td><td>突破20日高点，量能>5日均量1.5倍，涨幅>3%</td><td>盘整后启动</td><td>强</td></tr>
                <tr><td><span class="badge badge-4">W底突破颈线</span></td><td>双底不创新低，放量突破颈线</td><td>超跌反弹</td><td>中强</td></tr>
                <tr><td><span class="badge badge-5">回踩均线企稳</span></td><td>上升趋势中回调至MA20/60，收阳缩量</td><td>趋势中继买点</td><td>中</td></tr>
                <tr><td><span class="badge badge-6">均线粘合发散</span></td><td>MA5/10/20/60纠缠后向上发散，放量</td><td>长期盘整后主升浪</td><td>强</td></tr>
            </tbody>
        </table>
    </div>

    <div class="disclaimer">
        <strong>免责声明：</strong>本报告基于历史K线数据的技术形态量化识别，仅供学习和研究参考，不构成任何投资建议。
        技术形态存在失败可能，市场有风险，投资需谨慎。
    </div>

    <div class="footer">K线形态识别扫描器 v1.0 | 批量扫描模式</div>
</div>
</body>
</html>'''

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\n批量扫描报告已生成: {report_path}")
    print(f"有信号股票: {len(stocks_with_signals)}/{len(results)}")
    return report_path


def scan_us_pool(pool_key, output_dir, min_score=0, workers=5):
    """美股指数成分股批量扫描（实时拉取K线，与网页端/analyze_stock同一算法）。
    pool_key: 'sp500'（标普500）或 'nasdaq100'（纳指100），成分股来自 sector_data/us_pools.json。
    """
    import concurrent.futures
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        from analyze_stock import analyze, fetch_market_env
    except ImportError:
        # 根目录 legacy 副本运行时，回退到 scripts/ 目录
        sys.path.insert(0, os.path.join(script_dir, 'scripts'))
        from analyze_stock import analyze, fetch_market_env

    pools_path = None
    for candidate in [
        os.path.join(os.path.dirname(script_dir), 'sector_data', 'us_pools.json'),
        os.path.join(script_dir, 'sector_data', 'us_pools.json'),
    ]:
        if os.path.isfile(candidate):
            pools_path = candidate
            break
    if pools_path is None:
        raise FileNotFoundError('未找到 sector_data/us_pools.json')
    with open(pools_path, encoding='utf-8') as f:
        pools = json.load(f)
    pool_map = {'sp500': '标普500', 'nasdaq100': '纳指100'}
    pool_name = pool_map[pool_key]
    tickers = pools['pools'][pool_name]['tickers']
    names = pools.get('names', {})

    print(f"美股扫描池: {pool_name}（{len(tickers)}只，数据日期 {pools.get('updatedAt')}）")
    market_env = fetch_market_env('us')
    if market_env:
        print(f"大盘环境: 标普500 {market_env['score']}分 {market_env['stage']}")
    else:
        print("警告: 标普500大盘数据获取失败，个股置信度将不含大盘环境")

    results, errors = [], []

    def work(ticker):
        try:
            return analyze(ticker, names.get(ticker), market_env=market_env)
        except Exception as e:
            return {'code': ticker, 'error': str(e)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(work, tickers), 1):
            if 'error' in r:
                errors.append(r)
            else:
                results.append(r)
            if i % 25 == 0 or i == len(tickers):
                print(f"进度 {i}/{len(tickers)}，成功 {len(results)}，失败 {len(errors)}")

    results.sort(key=lambda r: r.get('attention_score', 0), reverse=True)
    filtered = [r for r in results if r.get('attention_score', 0) >= min_score]
    os.makedirs(output_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d')
    json_path = os.path.join(output_dir, f'us_scan_{pool_key}_{stamp}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({'pool': pool_name, 'date': stamp, 'market_env': market_env,
                   'total': len(tickers), 'success': len(results), 'failed': len(errors),
                   'min_score': min_score, 'results': filtered, 'errors': errors},
                  f, ensure_ascii=False, indent=1)

    md_path = os.path.join(output_dir, f'us_scan_{pool_key}_{stamp}.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f"# 美股{pool_name}形态扫描 {stamp}\n\n")
        f.write(f"扫描 {len(tickers)} 只，成功 {len(results)}，失败 {len(errors)}，推荐度≥{min_score} 共 {len(filtered)} 只\n\n")
        if market_env:
            f.write(f"大盘环境：标普500 {market_env['score']}分 {market_env['stage']}\n\n")
        f.write("| 排名 | 代码 | 名称 | 现价$ | 涨跌% | 最佳形态 | 推荐度 | 等级 | 买入可信度 | 卖出可信度 |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for i, r in enumerate(filtered, 1):
            f.write(f"| {i} | {r['code']} | {r['name']} | {r['close']} | {r['change_pct']} | "
                    f"{r.get('best_pattern_name', '')} | {r.get('attention_score', '')} | {r.get('attention_level', '')} | "
                    f"{r.get('buy_accuracy', '')}% | {r.get('sell_accuracy', '')}% |\n")
    print(f"\n扫描完成: {json_path}\n报告: {md_path}")
    return json_path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description='批量K线形态扫描器 - A股扫描data目录CSV；美股实时扫描标普500/纳指100成分股',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''示例：
  python3 batch_scan.py                    # A股：扫描最近10个交易日
  python3 batch_scan.py --days 20         # A股：扫描最近20个交易日
  python3 batch_scan.py -d 5 --data ./mydata  # A股：指定数据目录
  python3 batch_scan.py --us-pool sp500      # 美股：标普500成分股实时扫描
  python3 batch_scan.py --us-pool nasdaq100 --min-score 60  # 美股：纳指100，推荐度≥60
'''
    )
    parser.add_argument('-d', '--days', type=int, default=10,
                        help='扫描最近N个交易日的信号（默认10）')
    parser.add_argument('--data', type=str, default=None,
                        help='指定数据目录（默认自动向上查找data/目录）')
    parser.add_argument('--output', type=str, default=None,
                        help='指定输出目录（默认data目录同级的output/）')
    parser.add_argument('--us-pool', type=str, choices=['sp500', 'nasdaq100'], default=None,
                        help='美股扫描池：sp500=标普500，nasdaq100=纳指100（实时拉取K线）')
    parser.add_argument('--min-score', type=int, default=0,
                        help='美股扫描最低推荐度过滤（默认0不过滤）')
    args = parser.parse_args()

    # 美股扫描模式
    if args.us_pool:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        default_out = os.path.join(os.path.dirname(script_dir), 'output')
        if os.path.isdir(os.path.join(script_dir, 'sector_data')):
            default_out = os.path.join(script_dir, 'output')
        output_dir = args.output or default_out
        scan_us_pool(args.us_pool, output_dir, args.min_score)
        sys.exit(0)

    # 查找data目录
    if args.data:
        data_dir = os.path.abspath(args.data)
        if not os.path.isdir(data_dir):
            print(f"错误：指定的数据目录不存在: {data_dir}")
            sys.exit(1)
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = None
        for candidate in [
            os.path.join(script_dir, 'data'),
            os.path.join(os.path.dirname(script_dir), 'data'),
            os.path.join(os.path.dirname(os.path.dirname(script_dir)), 'data'),
        ]:
            if os.path.isdir(candidate):
                data_dir = candidate
                break

        if data_dir is None:
            print("错误：未找到data目录。请确保项目结构包含data/目录，其中存放CSV股票数据文件。")
            print("或使用 --data 参数指定数据目录。")
            sys.exit(1)

    output_dir = args.output or os.path.join(os.path.dirname(data_dir), 'output')
    os.makedirs(output_dir, exist_ok=True)

    print(f"数据目录: {data_dir}")
    print(f"输出目录: {output_dir}")
    print(f"扫描最近 {args.days} 个交易日的信号\n")
    batch_scan(data_dir, output_dir, args.days)
