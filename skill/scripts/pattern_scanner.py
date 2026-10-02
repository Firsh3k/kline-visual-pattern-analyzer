#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K线形态识别扫描器
基于OHLCV数据识别6种经典上涨形态，生成带标注的K线图和HTML报告。
"""

import os
import sys
import json
import base64
from datetime import datetime
from io import BytesIO

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch
import matplotlib.font_manager as fm

# ============ 中文字体配置 ============
plt.rcParams['font.sans-serif'] = ['Noto Sans CJK SC', 'Noto Sans CJK JP', 'WenQuanYi Micro Hei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# ============ 颜色配置（A股习惯：红涨绿跌） ============
COLOR_UP = '#e74c3c'
COLOR_DOWN = '#2ecc71'
COLOR_MA5 = '#f39c12'
COLOR_MA10 = '#3498db'
COLOR_MA20 = '#9b59b6'
COLOR_MA60 = '#1abc9c'
COLOR_GRID = '#ecf0f1'
COLOR_BG = '#ffffff'


# ============================================================
# 1. 数据加载
# ============================================================
def load_data(csv_path):
    """加载CSV格式的K线数据，要求列: date,open,high,low,close,volume"""
    df = pd.read_csv(csv_path)
    df['date'] = pd.to_datetime(df['date'].astype(str), format='%Y%m%d')
    df = df.sort_values('date').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['close'])
    return df


# ============================================================
# 2. 技术指标计算
# ============================================================
def calc_indicators(df):
    """计算均线、MACD、量能等技术指标"""
    # 均线
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma60'] = df['close'].rolling(60).mean()

    # MACD (12, 26, 9)
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['dif'] = ema12 - ema26
    df['dea'] = df['dif'].ewm(span=9, adjust=False).mean()
    df['macd'] = (df['dif'] - df['dea']) * 2

    # 成交量均线
    df['vma5'] = df['volume'].rolling(5).mean()
    df['vma10'] = df['volume'].rolling(10).mean()

    # 涨跌幅
    df['pct_change'] = df['close'].pct_change() * 100

    # 20日最高最低（用于平台突破判断）
    df['high_20'] = df['high'].rolling(20).max().shift(1)
    df['low_20'] = df['low'].rolling(20).min().shift(1)

    return df


# ============================================================
# 3. 六种上涨形态识别引擎
# ============================================================
def detect_ma_bull_breakout(df):
    """① 均线多头排列+突破
    条件：MA5>MA10>MA20>MA60，且收盘价突破前20日高点，当日收阳
    """
    signals = []
    for i in range(60, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        if (pd.notna(row['ma5']) and pd.notna(row['ma10']) and
            pd.notna(row['ma20']) and pd.notna(row['ma60']) and
            row['ma5'] > row['ma10'] > row['ma20'] > row['ma60'] and
            row['close'] > row['high_20'] and
            row['close'] > row['open']):
            signals.append({
                'index': i,
                'date': row['date'],
                'type': '均线多头突破',
                'type_id': 1,
                'strength': '强',
                'desc': f"MA5({row['ma5']:.1f})>MA10({row['ma10']:.1f})>MA20({row['ma20']:.1f})>MA60({row['ma60']:.1f})，收盘价{row['close']:.1f}突破20日高点{row['high_20']:.1f}",
                'action': '主升浪启动信号，可关注回踩5日线的加仓机会'
            })
    return signals


def detect_macd_golden_above_zero(df):
    """② MACD零上金叉
    条件：DIF上穿DEA（金叉），且DIF和DEA都在零轴上方，MACD柱由负转正或放大
    """
    signals = []
    last_golden_idx = -999
    for i in range(35, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        if (prev['dif'] <= prev['dea'] and row['dif'] > row['dea'] and
            row['dif'] > 0 and row['dea'] > 0):
            # 去重：10天内的连续金叉只记一次
            if i - last_golden_idx > 10:
                signals.append({
                    'index': i,
                    'date': row['date'],
                    'type': 'MACD零上金叉',
                    'type_id': 2,
                    'strength': '中强',
                    'desc': f"DIF({row['dif']:.3f})上穿DEA({row['dea']:.3f})，二者均在零轴上方，MACD柱{row['macd']:.3f}",
                    'action': '强势反弹/续涨信号，零上金叉可靠性高于零下金叉'
                })
                last_golden_idx = i
    return signals


def detect_volume_breakout(df):
    """③ 放量突破平台
    条件：收盘价突破前20日高点，成交量>5日均量的1.5倍，当日涨幅>3%
    """
    signals = []
    for i in range(25, len(df)):
        row = df.iloc[i]
        if (pd.notna(row['high_20']) and pd.notna(row['vma5']) and
            row['close'] > row['high_20'] and
            row['volume'] > row['vma5'] * 1.5 and
            row['pct_change'] > 3):
            signals.append({
                'index': i,
                'date': row['date'],
                'type': '放量突破平台',
                'type_id': 3,
                'strength': '强',
                'desc': f"收盘价{row['close']:.1f}突破20日高点{row['high_20']:.1f}，成交量{row['volume']:.0f}是5日均量{row['vma5']:.0f}的{row['volume']/row['vma5']:.1f}倍，涨幅{row['pct_change']:.1f}%",
                'action': '资金进场信号，放量突破后若缩量回踩不破平台则确认有效'
            })
    return signals


def detect_double_bottom(df):
    """④ 底部W底（双底）
    条件：在过去60日内出现两个低点，第二个低点不低于第一个低点的97%，
    且当前价格突破两个低点之间的高点（颈线），第二个低点后成交量萎缩
    """
    signals = []
    window = 60
    for i in range(window, len(df)):
        segment = df.iloc[i - window:i + 1].copy()
        if len(segment) < 30:
            continue
        closes = segment['close'].values
        lows = segment['low'].values
        highs = segment['high'].values

        # 找局部最低点（用5日窗口）
        min_idx = np.argmin(lows)
        if min_idx < 5 or min_idx > len(lows) - 10:
            continue

        first_low = lows[min_idx]
        first_low_idx = min_idx

        # 在第一个低点之后找第二个低点
        after_first = lows[first_low_idx + 5:]
        if len(after_first) < 5:
            continue
        second_low_idx_rel = np.argmin(after_first)
        second_low_idx = first_low_idx + 5 + second_low_idx_rel
        second_low = lows[second_low_idx]

        # 第二个低点不低于第一个低点的97%（允许3%误差），且两底差异不超过5%
        if second_low < first_low * 0.97:
            continue
        bottom_diff_pct = abs(second_low - first_low) / first_low * 100
        if bottom_diff_pct > 5.0:
            continue
        # 两个低点之间至少间隔10天
        if second_low_idx - first_low_idx < 10:
            continue

        # 颈线 = 两个低点之间的最高点（用highs而非lows）
        neckline = np.max(highs[first_low_idx:second_low_idx + 1])

        # 当前价格突破颈线
        current_close = closes[-1]
        if current_close > neckline * 1.01:
            # 第二个低点后成交量萎缩
            vol_before = segment['volume'].iloc[first_low_idx:first_low_idx + 5].mean()
            vol_after = segment['volume'].iloc[second_low_idx:second_low_idx + 5].mean()
            if vol_after < vol_before * 1.2:
                signals.append({
                    'index': i,
                    'date': segment.iloc[-1]['date'],
                    'type': 'W底突破颈线',
                    'type_id': 4,
                    'strength': '中强',
                    'desc': f"第一低点{first_low:.1f}，第二低点{second_low:.1f}（不创新低），颈线位{neckline:.1f}，当前收盘{current_close:.1f}突破颈线",
                    'action': '超跌反弹信号，W底确认后最小涨幅约为颈线到低点的距离'
                })
    # 去重（同一底部可能连续多日触发）
    unique = []
    last_date = None
    for s in signals:
        if last_date is None or (s['date'] - last_date).days > 10:
            unique.append(s)
            last_date = s['date']
    return unique


def detect_pullback_support(df):
    """⑤ 回踩均线企稳
    条件：前期处于上升趋势（MA20向上），股价回调至MA20或MA60附近（±2%），
    当日收阳且下影线较长（探底回升），成交量萎缩
    """
    signals = []
    for i in range(25, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        if pd.isna(row['ma20']) or pd.isna(row['ma60']):
            continue

        # MA20向上（过去5天MA20上升）
        if i < 30 or df.iloc[i - 5]['ma20'] >= row['ma20']:
            continue

        # 收盘价在MA20或MA60的±2%范围内
        near_ma20 = abs(row['close'] - row['ma20']) / row['ma20'] < 0.02
        near_ma60 = abs(row['low'] - row['ma60']) / row['ma60'] < 0.02
        if not (near_ma20 or near_ma60):
            continue

        # 当日收阳
        if row['close'] <= row['open']:
            continue

        # 下影线较长（最低价远低于开盘价和收盘价的较小值）
        body_low = min(row['open'], row['close'])
        lower_shadow = (body_low - row['low']) / row['low']
        if lower_shadow < 0.005:
            continue

        # 成交量萎缩（小于5日均量）
        if pd.notna(row['vma5']) and row['volume'] > row['vma5']:
            continue

        support_line = 'MA20' if near_ma20 else 'MA60'
        signals.append({
            'index': i,
            'date': row['date'],
            'type': f'回踩{support_line}企稳',
            'type_id': 5,
            'strength': '中',
            'desc': f"上升趋势中回调至{support_line}({row['ma20'] if near_ma20 else row['ma60']:.1f})附近，收阳{row['close']:.1f}，下影线探底{row['low']:.1f}，缩量",
            'action': '趋势中继买点，若次日不破今日低点则确认企稳'
        })
    return signals


def detect_ma_convergence_divergence(df):
    """⑥ 均线粘合后向上发散
    条件：MA5/MA10/MA20/MA60在过去10日内纠缠（最大差值<3%），
    近期MA5上穿MA10/MA20，且成交量放大
    """
    signals = []
    for i in range(70, len(df)):
        row = df.iloc[i]
        if any(pd.isna(df.iloc[i][f'ma{n}']) for n in [5, 10, 20, 60]):
            continue

        # 检查过去10天内是否有粘合期
        converged = False
        for j in range(max(60, i - 20), i - 2):
            r = df.iloc[j]
            if any(pd.isna(r[f'ma{n}']) for n in [5, 10, 20, 60]):
                continue
            mas = [r['ma5'], r['ma10'], r['ma20'], r['ma60']]
            spread = (max(mas) - min(mas)) / min(mas)
            if spread < 0.03:
                converged = True
                break

        if not converged:
            continue

        # 当前MA5 > MA10 > MA20（开始发散）
        if not (row['ma5'] > row['ma10'] > row['ma20']):
            continue

        # MA5在过去5天内上穿MA20
        crossed = False
        for j in range(i - 5, i):
            if df.iloc[j]['ma5'] <= df.iloc[j]['ma20'] and df.iloc[j + 1]['ma5'] > df.iloc[j + 1]['ma20']:
                crossed = True
                break
        if not crossed:
            continue

        # 成交量放大
        if pd.notna(row['vma5']) and row['volume'] < row['vma5'] * 1.2:
            continue

        # 当日必须收阳（上涨信号）
        if row['close'] <= row['open']:
            continue

        signals.append({
            'index': i,
            'date': row['date'],
            'type': '均线粘合发散',
            'type_id': 6,
            'strength': '强',
            'desc': f"MA5/10/20/60前期纠缠（粘合），MA5({row['ma5']:.1f})上穿MA20({row['ma20']:.1f})开始向上发散，放量{row['volume']:.0f}",
            'action': '长期盘整后主升浪启动信号，粘合时间越长爆发力越强'
        })
    # 去重
    unique = []
    last_date = None
    for s in signals:
        if last_date is None or (s['date'] - last_date).days > 15:
            unique.append(s)
            last_date = s['date']
    return unique


def detect_all_patterns(df):
    """运行所有形态检测器，返回合并后的信号列表"""
    all_signals = []
    all_signals.extend(detect_ma_bull_breakout(df))
    all_signals.extend(detect_macd_golden_above_zero(df))
    all_signals.extend(detect_volume_breakout(df))
    all_signals.extend(detect_double_bottom(df))
    all_signals.extend(detect_pullback_support(df))
    all_signals.extend(detect_ma_convergence_divergence(df))
    all_signals.sort(key=lambda x: x['index'])
    return all_signals


# ============================================================
# 4. K线图绘制
# ============================================================
def plot_candlestick(ax, df, signals):
    """绘制K线蜡烛图"""
    dates = df['date'].values
    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values

    for i in range(len(df)):
        color = COLOR_UP if closes[i] >= opens[i] else COLOR_DOWN
        # 影线
        ax.plot([i, i], [lows[i], highs[i]], color=color, linewidth=0.8, zorder=2)
        # 实体
        body_bottom = min(opens[i], closes[i])
        body_height = abs(closes[i] - opens[i])
        if body_height < 0.001:
            body_height = (highs[i] - lows[i]) * 0.05
        rect = plt.Rectangle((i - 0.35, body_bottom), 0.7, body_height,
                              facecolor=color, edgecolor=color, linewidth=0.5, zorder=3)
        ax.add_patch(rect)

    # 均线
    ax.plot(range(len(df)), df['ma5'], color=COLOR_MA5, linewidth=1, label='MA5', alpha=0.9)
    ax.plot(range(len(df)), df['ma10'], color=COLOR_MA10, linewidth=1, label='MA10', alpha=0.9)
    ax.plot(range(len(df)), df['ma20'], color=COLOR_MA20, linewidth=1, label='MA20', alpha=0.9)
    if df['ma60'].notna().any():
        ax.plot(range(len(df)), df['ma60'], color=COLOR_MA60, linewidth=1.2, label='MA60', alpha=0.9)

    # 标注形态信号
    for sig in signals:
        idx = sig['index']
        if idx < len(df):
            price = df.iloc[idx]['high'] * 1.02
            ax.annotate('', xy=(idx, df.iloc[idx]['low'] * 0.98),
                        xytext=(idx, df.iloc[idx]['low'] * 0.95),
                        arrowprops=dict(arrowstyle='->', color='#e67e22', lw=1.5))
            ax.text(idx, price, sig['type'], fontsize=7, ha='center', va='bottom',
                    color='#c0392b', fontweight='bold',
                    bbox=dict(boxstyle='round,pad=0.2', facecolor='#fef9e7', edgecolor='#f39c12', alpha=0.9))

    ax.set_xlim(-1, len(df))
    ax.legend(loc='upper left', fontsize=8, ncol=4, framealpha=0.9)
    ax.grid(True, alpha=0.3, color=COLOR_GRID)
    ax.set_facecolor(COLOR_BG)


def plot_volume(ax, df):
    """绘制成交量柱状图"""
    colors = [COLOR_UP if df.iloc[i]['close'] >= df.iloc[i]['open'] else COLOR_DOWN
              for i in range(len(df))]
    ax.bar(range(len(df)), df['volume'], color=colors, alpha=0.7, width=0.7)
    ax.plot(range(len(df)), df['vma5'], color=COLOR_MA5, linewidth=0.8, label='VMA5')
    ax.plot(range(len(df)), df['vma10'], color=COLOR_MA10, linewidth=0.8, label='VMA10')
    ax.set_ylabel('成交量', fontsize=9)
    ax.legend(loc='upper left', fontsize=7, ncol=2)
    ax.grid(True, alpha=0.3, color=COLOR_GRID)
    ax.set_xlim(-1, len(df))


def plot_macd(ax, df):
    """绘制MACD副图"""
    ax.plot(range(len(df)), df['dif'], color='#e67e22', linewidth=0.8, label='DIF')
    ax.plot(range(len(df)), df['dea'], color='#2980b9', linewidth=0.8, label='DEA')
    macd_colors = [COLOR_UP if v >= 0 else COLOR_DOWN for v in df['macd']]
    ax.bar(range(len(df)), df['macd'], color=macd_colors, alpha=0.6, width=0.7)
    ax.axhline(y=0, color='gray', linewidth=0.5, linestyle='--')
    ax.set_ylabel('MACD', fontsize=9)
    ax.legend(loc='upper left', fontsize=7, ncol=2)
    ax.grid(True, alpha=0.3, color=COLOR_GRID)
    ax.set_xlim(-1, len(df))


def generate_chart(df, signals, stock_name, stock_code, output_path):
    """生成完整的K线分析图"""
    fig, axes = plt.subplots(3, 1, figsize=(16, 10), gridspec_kw={'height_ratios': [3, 1, 1]})
    fig.suptitle(f'{stock_name}({stock_code}) K线形态分析  |  数据区间: {df["date"].iloc[0].strftime("%Y-%m-%d")} ~ {df["date"].iloc[-1].strftime("%Y-%m-%d")}  |  共{len(df)}个交易日',
                 fontsize=13, fontweight='bold', y=0.98)

    # K线主图
    plot_candlestick(axes[0], df, signals)
    axes[0].set_ylabel('价格', fontsize=10)

    # 成交量
    plot_volume(axes[1], df)

    # MACD
    plot_macd(axes[2], df)

    # x轴日期标签（只在底部显示）
    tick_positions = list(range(0, len(df), max(1, len(df) // 10)))
    tick_labels = [df.iloc[i]['date'].strftime('%m-%d') for i in tick_positions]
    for ax in axes:
        ax.set_xticks(tick_positions)
        ax.set_xticklabels(tick_labels, fontsize=8, rotation=0)
    axes[0].set_xticklabels([])
    axes[1].set_xticklabels([])

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    return output_path


# ============================================================
# 5. HTML报告生成
# ============================================================
def generate_html_report(df, signals, chart_path, stock_name, stock_code, output_path):
    """生成交互式HTML分析报告"""
    # 将图片转为base64嵌入
    with open(chart_path, 'rb') as f:
        img_b64 = base64.b64encode(f.read()).decode()

    # 最新行情
    latest = df.iloc[-1]
    latest_date = latest['date'].strftime('%Y-%m-%d')

    # 形态统计
    pattern_counts = {}
    for s in signals:
        pattern_counts[s['type']] = pattern_counts.get(s['type'], 0) + 1

    # 最近信号（最近5个）
    recent_signals = sorted(signals, key=lambda x: x['index'], reverse=True)[:5]

    # 当前技术状态
    current_state = []
    if pd.notna(latest['ma5']) and pd.notna(latest['ma10']):
        if latest['ma5'] > latest['ma10'] > latest['ma20']:
            current_state.append(('均线排列', '多头排列', 'good'))
        elif latest['ma5'] < latest['ma10'] < latest['ma20']:
            current_state.append(('均线排列', '空头排列', 'bad'))
        else:
            current_state.append(('均线排列', '交叉缠绕', 'neutral'))

    if latest['dif'] > latest['dea']:
        current_state.append(('MACD', f"DIF({latest['dif']:.3f})>DEA({latest['dea']:.3f}) 多头", 'good'))
    else:
        current_state.append(('MACD', f"DIF({latest['dif']:.3f})<DEA({latest['dea']:.3f}) 空头", 'bad'))

    if latest['volume'] > latest['vma5'] * 1.3:
        current_state.append(('成交量', '放量', 'good'))
    elif latest['volume'] < latest['vma5'] * 0.7:
        current_state.append(('成交量', '缩量', 'neutral'))
    else:
        current_state.append(('成交量', '正常', 'neutral'))

    # 构建信号表格HTML
    signals_html = ''
    for s in recent_signals:
        signals_html += f'''
        <tr>
            <td>{s['date'].strftime('%Y-%m-%d')}</td>
            <td><span class="badge badge-{s['type_id']}">{s['type']}</span></td>
            <td>{s['strength']}</td>
            <td style="text-align:left;font-size:13px;">{s['desc']}</td>
            <td style="text-align:left;font-size:13px;color:#2980b9;">{s['action']}</td>
        </tr>'''

    if not recent_signals:
        signals_html = '<tr><td colspan="5" style="text-align:center;color:#999;padding:20px;">近期未检测到经典上涨形态信号</td></tr>'

    # 形态统计HTML
    stats_html = ''
    for ptype, count in sorted(pattern_counts.items(), key=lambda x: -x[1]):
        stats_html += f'<div class="stat-item"><span class="stat-label">{ptype}</span><span class="stat-value">{count}次</span></div>'

    # 当前状态HTML
    state_html = ''
    for label, value, status in current_state:
        state_html += f'<div class="state-card state-{status}"><div class="state-label">{label}</div><div class="state-value">{value}</div></div>'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{stock_name}({stock_code}) K线形态分析报告</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; background:#f5f6fa; color:#2c3e50; line-height:1.6; }}
.container {{ max-width:1200px; margin:0 auto; padding:20px; }}
.header {{ background:linear-gradient(135deg, #2c3e50, #34495e); color:white; padding:30px; border-radius:12px; margin-bottom:20px; }}
.header h1 {{ font-size:24px; margin-bottom:8px; }}
.header .meta {{ font-size:14px; opacity:0.85; }}
.price-box {{ display:flex; gap:30px; margin-top:15px; flex-wrap:wrap; }}
.price-item {{ }}
.price-item .label {{ font-size:12px; opacity:0.7; }}
.price-item .value {{ font-size:22px; font-weight:bold; }}
.section {{ background:white; border-radius:12px; padding:24px; margin-bottom:20px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
.section h2 {{ font-size:18px; margin-bottom:16px; padding-bottom:10px; border-bottom:2px solid #ecf0f1; }}
.state-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin-bottom:16px; }}
.state-card {{ padding:16px; border-radius:8px; text-align:center; }}
.state-good {{ background:#eafaf1; border-left:4px solid #27ae60; }}
.state-bad {{ background:#fdedec; border-left:4px solid #e74c3c; }}
.state-neutral {{ background:#fef9e7; border-left:4px solid #f39c12; }}
.state-label {{ font-size:12px; color:#7f8c8d; margin-bottom:4px; }}
.state-value {{ font-size:15px; font-weight:bold; }}
.chart-container {{ text-align:center; }}
.chart-container img {{ max-width:100%; border-radius:8px; border:1px solid #ecf0f1; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th {{ background:#f8f9fa; padding:12px 8px; text-align:center; font-weight:600; border-bottom:2px solid #dee2e6; }}
td {{ padding:10px 8px; text-align:center; border-bottom:1px solid #f1f2f6; }}
tr:hover {{ background:#f8f9fa; }}
.badge {{ display:inline-block; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:bold; color:white; }}
.badge-1 {{ background:#e74c3c; }}
.badge-2 {{ background:#3498db; }}
.badge-3 {{ background:#9b59b6; }}
.badge-4 {{ background:#1abc9c; }}
.badge-5 {{ background:#f39c12; }}
.badge-6 {{ background:#e67e22; }}
.stats-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; }}
.stat-item {{ display:flex; justify-content:space-between; align-items:center; padding:12px 16px; background:#f8f9fa; border-radius:8px; }}
.stat-label {{ font-size:14px; }}
.stat-value {{ font-size:18px; font-weight:bold; color:#e74c3c; }}
.disclaimer {{ background:#fff3cd; border-left:4px solid #ffc107; padding:16px; border-radius:8px; font-size:13px; color:#856404; margin-top:20px; }}
.footer {{ text-align:center; padding:20px; color:#95a5a6; font-size:12px; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>{stock_name}（{stock_code}）K线形态分析报告</h1>
        <div class="meta">报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')} | 数据区间：{df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {latest_date} | 共{len(df)}个交易日</div>
        <div class="price-box">
            <div class="price-item"><div class="label">最新收盘价</div><div class="value">{latest['close']:.2f}</div></div>
            <div class="price-item"><div class="label">当日涨跌幅</div><div class="value" style="color:{'#e74c3c' if latest['pct_change']>=0 else '#2ecc71'}">{latest['pct_change']:+.2f}%</div></div>
            <div class="price-item"><div class="label">区间最高</div><div class="value">{df['high'].max():.2f}</div></div>
            <div class="price-item"><div class="label">区间最低</div><div class="value">{df['low'].min():.2f}</div></div>
            <div class="price-item"><div class="label">检测到信号</div><div class="value" style="color:#f39c12;">{len(signals)}个</div></div>
        </div>
    </div>

    <div class="section">
        <h2>当前技术状态</h2>
        <div class="state-grid">{state_html}</div>
    </div>

    <div class="section">
        <h2>K线走势图（含形态标注）</h2>
        <div class="chart-container">
            <img src="data:image/png;base64,{img_b64}" alt="K线图">
        </div>
    </div>

    <div class="section">
        <h2>近期形态信号（最近5个）</h2>
        <table>
            <thead><tr><th>信号日期</th><th>形态类型</th><th>强度</th><th>形态描述</th><th>操作建议</th></tr></thead>
            <tbody>{signals_html}</tbody>
        </table>
    </div>

    <div class="section">
        <h2>形态统计（全区间）</h2>
        <div class="stats-grid">{stats_html if stats_html else '<div style="color:#999;padding:10px;">未检测到形态信号</div>'}</div>
    </div>

    <div class="disclaimer">
        <strong>免责声明：</strong>本报告基于历史K线数据的技术形态量化识别，仅供学习和研究参考，不构成任何投资建议。
        技术形态存在失败可能，市场有风险，投资需谨慎。过往表现不代表未来收益。
    </div>

    <div class="footer">K线形态识别扫描器 v1.0 | 基于通达信行情数据</div>
</div>
</body>
</html>'''

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path


# ============================================================
# 主程序
# ============================================================
def analyze_stock(csv_path, stock_name, stock_code, output_dir):
    """分析单只股票的完整流程"""
    print(f"[1/5] 加载数据: {csv_path}")
    df = load_data(csv_path)
    print(f"      共 {len(df)} 个交易日，{df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d')}")

    print("[2/5] 计算技术指标...")
    df = calc_indicators(df)

    print("[3/5] 识别6种上涨形态...")
    signals = detect_all_patterns(df)
    print(f"      共检测到 {len(signals)} 个形态信号")
    for s in signals:
        print(f"      - {s['date'].strftime('%Y-%m-%d')} [{s['type']}] {s['strength']}")

    os.makedirs(output_dir, exist_ok=True)
    chart_path = os.path.join(output_dir, f'{stock_code}_chart.png')
    report_path = os.path.join(output_dir, f'{stock_code}_report.html')

    print("[4/5] 生成K线图...")
    generate_chart(df, signals, stock_name, stock_code, chart_path)

    print("[5/5] 生成HTML报告...")
    generate_html_report(df, signals, chart_path, stock_name, stock_code, report_path)

    print(f"\n完成！报告已保存至: {report_path}")
    return report_path, chart_path, signals


if __name__ == '__main__':
    # 默认分析贵州茅台
    base_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(base_dir, 'data', '600519.csv')
    output_dir = os.path.join(base_dir, 'output')

    if len(sys.argv) >= 2:
        csv_path = sys.argv[1]
    if len(sys.argv) >= 3:
        stock_name = sys.argv[2]
    else:
        stock_name = '贵州茅台'
    if len(sys.argv) >= 4:
        stock_code = sys.argv[3]
    else:
        stock_code = '600519'

    analyze_stock(csv_path, stock_name, stock_code, output_dir)
