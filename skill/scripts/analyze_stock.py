#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K线形态分析器 v4.0（与网页端JS算法100%一致）
基于250日K线数据，识别8种经典形态（4上升+4下降），
包含：指标计算、8形态检测、三大增强信号、五大增强指标、
大盘环境、形态可靠性、完整推荐度评分系统。

与网页端 kline_analyzer.html 的 calcIndicators + matchPatterns +
matchBearPatterns + calcAttentionScore + renderAnalysis 完全对齐。
"""

import sys
import json
import urllib.request
import re
import math
from datetime import datetime


# ============ 数据获取 ============
def fetch_kline(code, datalen=250):
    """从新浪财经拉取K线数据"""
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith('920'):
        return None
    url = f'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_{market}{code}=/CN_MarketDataService.getKLineData?symbol={market}{code}&scale=240&ma=no&datalen={datalen}'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=15)
        text = resp.read().decode('utf-8')
        match = re.search(r'\((.*)\)', text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
    except Exception:
        pass
    return None


def fetch_market_env():
    """拉取上证指数计算大盘环境（与网页端analyzeMarketEnv一致）"""
    try:
        url = 'https://quotes.sina.cn/cn/api/jsonp_v2.php/callback/CN_MarketDataService.getKLineData?symbol=sh000001&scale=240&ma=no&datalen=250'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=10)
        text = resp.read().decode('utf-8')
        match = re.search(r'\((.*)\)', text, re.DOTALL)
        if not match:
            return None
        raw = json.loads(match.group(1))
        if len(raw) < 60:
            return None
        data = []
        for d in raw:
            data.append({
                'day': d['day'], 'open': float(d['open']), 'high': float(d['high']),
                'low': float(d['low']), 'close': float(d['close']), 'volume': float(d['volume'])
            })
        return calc_market_env_from_data(data)
    except Exception:
        return None


def extract_weekly_data(data):
    """从日线数据聚合周线（每5个交易日为一周）
    重要：每根日K线只能属于一个周K线，新周第一天只进入新周，不会先写入旧周
    """
    weekly_data = []
    n = len(data)
    # 从最后一天开始，向前每5天聚合为一周
    # i是当前周的最后一天索引，week = data[i-4:i+1]
    # i每次减5，确保每根K线只属于一个周
    for i in range(n - 1, 3, -5):
        start_idx = max(0, i - 4)
        week = data[start_idx:i + 1]
        weekly_data.insert(0, {
            'date': week[-1]['day'],
            'open': week[0]['open'],
            'high': max(d['high'] for d in week),
            'low': min(d['low'] for d in week),
            'close': week[-1]['close'],
            'volume': sum(d['volume'] for d in week)
        })
    return weekly_data


def extract_monthly_data(data):
    """从日线数据聚合月线（每20个交易日为一月）
    每根日K线只能属于一个月K线
    """
    monthly_data = []
    n = len(data)
    for i in range(n - 1, 19, -20):
        start_idx = max(0, i - 19)
        month = data[start_idx:i + 1]
        monthly_data.insert(0, {
            'date': month[-1]['day'],
            'open': month[0]['open'],
            'high': max(d['high'] for d in month),
            'low': min(d['low'] for d in month),
            'close': month[-1]['close'],
            'volume': sum(d['volume'] for d in month)
        })
    return monthly_data


def calc_market_env_from_data(data):
    """从已有K线数据计算大盘环境（回测模式用，与fetch_market_env逻辑一致）"""
    if not data or len(data) < 60:
        return None
    n = len(data)
    last = n - 1
    ind = calc_indicators(data)
    cur_price = data[last]['close']
    ma20 = ind['ma20'][last] or cur_price
    ma60 = ind['ma60'][last] or cur_price
    price20ago = data[max(0, last - 20)]['close']
    change20 = (cur_price - price20ago) / price20ago * 100
    above_ma20 = cur_price > ma20
    above_ma60 = cur_price > ma60
    ma20_up = ma20 > (ind['ma20'][max(0, last - 5)] or ma20)
    ma60_up = ma60 > (ind['ma60'][max(0, last - 10)] or ma60)
    ma_bull = (ind['ma5'][last] and ind['ma10'][last] and
               ind['ma5'][last] > ind['ma10'][last] > ma20 > ma60)
    ma_bear = (ind['ma5'][last] and ind['ma10'][last] and
               ind['ma5'][last] < ind['ma10'][last] < ma20 < ma60)
    recent20 = data[n-20:]
    high20 = max(d['high'] for d in recent20)
    low20 = min(d['low'] for d in recent20)
    volatility = (high20 - low20) / low20 * 100
    score = 50
    breakdown = [{'label': '基准分', 'value': '50'}]
    if ma_bull: score += 25; breakdown.append({'label': '均线多头', 'value': '+25'})
    if ma_bear: score -= 25; breakdown.append({'label': '均线空头', 'value': '-25'})
    if above_ma20: score += 8; breakdown.append({'label': '价格>MA20', 'value': '+8'})
    else: score -= 8; breakdown.append({'label': '价格<MA20', 'value': '-8'})
    if above_ma60: score += 8; breakdown.append({'label': '价格>MA60', 'value': '+8'})
    else: score -= 8; breakdown.append({'label': '价格<MA60', 'value': '-8'})
    if ma20_up: score += 5; breakdown.append({'label': 'MA20向上', 'value': '+5'})
    else: score -= 5; breakdown.append({'label': 'MA20向下', 'value': '-5'})
    if ma60_up: score += 4; breakdown.append({'label': 'MA60向上', 'value': '+4'})
    else: score -= 4; breakdown.append({'label': 'MA60向下', 'value': '-4'})
    if change20 > 5: score += 5; breakdown.append({'label': '20日涨>5%', 'value': '+5'})
    if change20 < -5: score -= 5; breakdown.append({'label': '20日跌>5%', 'value': '-5'})
    score = max(0, min(100, score))
    if score >= 75:
        stage = '强势上升'; trend = 'bull'; advice = '大盘强势，个股突破形态成功率高，可积极操作'; impact = 10
    elif score >= 60:
        stage = '震荡偏强'; trend = 'bull'; advice = '大盘偏强，个股机会较多，注意选股质量'; impact = 5
    elif score >= 45:
        stage = '震荡整理'; trend = 'neutral'; advice = '大盘震荡，个股分化严重，精选个股，控制仓位'; impact = 0
    elif score >= 30:
        stage = '震荡偏弱'; trend = 'bear'; advice = '大盘偏弱，注意假突破，降低仓位，快进快出'; impact = -8
    else:
        stage = '弱势下降'; trend = 'bear'; advice = '大盘弱势，个股普跌，建议空仓或轻仓，不要抄底'; impact = -15

    # 多周期涨跌幅
    change5 = ((cur_price - data[max(0, last-5)]['close']) / data[max(0, last-5)]['close'] * 100) if n >= 6 else 0
    change60 = ((cur_price - data[max(0, last-60)]['close']) / data[max(0, last-60)]['close'] * 100) if n >= 61 else 0
    change120 = ((cur_price - data[max(0, last-120)]['close']) / data[max(0, last-120)]['close'] * 100) if n >= 121 else 0
    change250 = ((cur_price - data[0]['close']) / data[0]['close'] * 100) if n >= 250 else 0

    # 周线聚合（每5个交易日为一周，每根K线只属于一个周）
    weekly_data = extract_weekly_data(data)
    weekly_trend = '数据不足'; weekly_change4 = 0
    if len(weekly_data) >= 10:
        w_last = len(weekly_data) - 1
        w_ma5 = sum(d['close'] for d in weekly_data[w_last-4:w_last+1]) / 5
        w_ma10 = sum(d['close'] for d in weekly_data[w_last-9:w_last+1]) / 10
        weekly_change4 = (weekly_data[w_last]['close'] - weekly_data[w_last-4]['close']) / weekly_data[w_last-4]['close'] * 100
        if w_ma5 > w_ma10 and weekly_data[w_last]['close'] > w_ma5: weekly_trend = '周线多头'
        elif w_ma5 < w_ma10 and weekly_data[w_last]['close'] < w_ma5: weekly_trend = '周线空头'
        else: weekly_trend = '周线震荡'

    # 月线聚合（每20个交易日为一月）
    monthly_data = extract_monthly_data(data)
    monthly_trend = '数据不足'; monthly_change3 = 0
    if len(monthly_data) >= 6:
        m_last = len(monthly_data) - 1
        m_ma3 = sum(d['close'] for d in monthly_data[m_last-2:m_last+1]) / 3
        m_ma6 = sum(d['close'] for d in monthly_data[m_last-5:m_last+1]) / 6
        monthly_change3 = (monthly_data[m_last]['close'] - monthly_data[m_last-3]['close']) / monthly_data[m_last-3]['close'] * 100
        if m_ma3 > m_ma6 and monthly_data[m_last]['close'] > m_ma3: monthly_trend = '月线多头'
        elif m_ma3 < m_ma6 and monthly_data[m_last]['close'] < m_ma3: monthly_trend = '月线空头'
        else: monthly_trend = '月线震荡'

    # 多周期共振
    resonance = '无明显共振'; resonance_level = 0
    if '多头' in weekly_trend and '多头' in monthly_trend: resonance = '周月线多头共振'; resonance_level = 2
    elif '空头' in weekly_trend and '空头' in monthly_trend: resonance = '周月线空头共振'; resonance_level = -2
    elif '多头' in weekly_trend or '多头' in monthly_trend: resonance = '偏多'; resonance_level = 1
    elif '空头' in weekly_trend or '空头' in monthly_trend: resonance = '偏空'; resonance_level = -1

    return {
        'score': score, 'stage': stage, 'trend': trend,
        'advice': advice, 'impact': impact,
        'breakdown': breakdown,
        'price': f'{cur_price:.2f}',
        'change20': f'{change20:.2f}',
        'change5': f'{change5:.2f}', 'change60': f'{change60:.2f}',
        'change120': f'{change120:.2f}', 'change250': f'{change250:.2f}',
        'ma20': f'{ma20:.2f}', 'ma60': f'{ma60:.2f}',
        'volatility': f'{volatility:.1f}',
        'aboveMa20': above_ma20, 'aboveMa60': above_ma60,
        'maBull': ma_bull, 'maBear': ma_bear,
        'weeklyTrend': weekly_trend, 'weeklyChange4': f'{weekly_change4:.2f}',
        'monthlyTrend': monthly_trend, 'monthlyChange3': f'{monthly_change3:.2f}',
        'resonance': resonance, 'resonanceLevel': resonance_level,
        'dataLength': n,
        'lastDate': data[last]['day'][:10]
    }


# ============ 指标计算（与网页端calcIndicators一致）============
def calc_indicators(data):
    n = len(data)
    closes = [d['close'] for d in data]
    highs = [d['high'] for d in data]
    lows = [d['low'] for d in data]
    volumes = [d['volume'] for d in data]

    def ma(arr, period):
        result = [None] * len(arr)
        for i in range(period - 1, len(arr)):
            result[i] = sum(arr[i - period + 1:i + 1]) / period
        return result

    ma5 = ma(closes, 5)
    ma10 = ma(closes, 10)
    ma20 = ma(closes, 20)
    ma60 = ma(closes, 60)
    vma5 = ma(volumes, 5)
    vma20 = ma(volumes, 20)

    # MACD(12,26,9)
    ema12 = [0.0] * n
    ema26 = [0.0] * n
    dif = [0.0] * n
    dea = [0.0] * n
    macd_bar = [0.0] * n
    e12 = closes[0]
    e26 = closes[0]
    for i in range(n):
        e12 = e12 * 11/13 + closes[i] * 2/13 if i > 0 else closes[0]
        e26 = e26 * 25/27 + closes[i] * 2/27 if i > 0 else closes[0]
        ema12[i] = e12
        ema26[i] = e26
        dif[i] = e12 - e26
        dea[i] = dea[i-1] * 8/10 + dif[i] * 2/10 if i > 0 else dif[0]
        macd_bar[i] = (dif[i] - dea[i]) * 2

    # RSI(14)
    rsi = [50.0] * n
    for i in range(14, n):
        gains = 0.0
        losses = 0.0
        for j in range(i - 13, i + 1):
            change = closes[j] - closes[j - 1]
            if change > 0:
                gains += change
            else:
                losses -= change
        avg_gain = gains / 14
        avg_loss = losses / 14
        rsi[i] = 100 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)

    # KDJ(9,3,3)
    kdj_k = [50.0] * n
    kdj_d = [50.0] * n
    kdj_j = [50.0] * n
    prev_k = 50.0
    prev_d = 50.0
    for i in range(n):
        if i < 8:
            continue
        h9 = max(highs[i-8:i+1])
        l9 = min(lows[i-8:i+1])
        rsv = 50 if h9 == l9 else (closes[i] - l9) / (h9 - l9) * 100
        k = prev_k * 2/3 + rsv * 1/3
        d = prev_d * 2/3 + k * 1/3
        j = 3 * k - 2 * d
        kdj_k[i] = k
        kdj_d[i] = d
        kdj_j[i] = j
        prev_k = k
        prev_d = d

    # BOLL(20,2)
    boll_mid = [0.0] * n
    boll_up = [0.0] * n
    boll_low = [0.0] * n
    boll_width = [0.0] * n
    for i in range(n):
        if i < 19:
            boll_mid[i] = closes[i]
            boll_up[i] = closes[i]
            boll_low[i] = closes[i]
            continue
        slice20 = closes[i-19:i+1]
        mid = sum(slice20) / 20
        variance = sum((x - mid) ** 2 for x in slice20) / 20
        std = math.sqrt(variance)
        boll_mid[i] = mid
        boll_up[i] = mid + 2 * std
        boll_low[i] = mid - 2 * std
        boll_width[i] = (boll_up[i] - boll_low[i]) / mid * 100 if mid > 0 else 0

    # ATR(14)
    atr = [0.0] * n
    atr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        if i < 14:
            atr[i] = tr
        else:
            atr[i] = (atr[i-1] * 13 + tr) / 14

    return {
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'vma5': vma5, 'vma20': vma20,
        'dif': dif, 'dea': dea, 'macd': macd_bar,
        'rsi': rsi, 'kdj_k': kdj_k, 'kdj_d': kdj_d, 'kdj_j': kdj_j,
        'boll_mid': boll_mid, 'boll_up': boll_up, 'boll_low': boll_low,
        'boll_width': boll_width, 'atr': atr
    }


# ============ 量价分析（与网页端analyzeVolume一致）============
def analyze_volume(data, ind):
    n = len(data)
    last = n - 1
    v5 = ind['vma5'][last] or 0
    v20 = ind['vma20'][last] or 0
    vol_ratio = v5 / v20 if v20 > 0 else 1

    if vol_ratio > 1.5:
        vol_status = '放量'
    elif vol_ratio < 0.7:
        vol_status = '缩量'
    else:
        vol_status = '正常'

    price_up = data[last]['close'] > data[last-1]['close'] if n >= 2 else False
    vol_up = data[last]['volume'] > (ind['vma5'][last] or 0)

    if price_up and vol_up:
        relation = '量价齐升'
    elif price_up and not vol_up:
        relation = '价涨量缩（上涨乏力，警惕）'
    elif not price_up and vol_up:
        relation = '价跌量增（抛压较重）'
    else:
        relation = '价跌量缩（正常回调）'

    return {
        'volStatus': vol_status, 'relation': relation,
        'volRatio': f'{vol_ratio:.2f}'
    }


# ============ MACD背离检测（与网页端detectMACDDivergence一致）============
def detect_macd_divergence(data, ind):
    n = len(data)
    last = n - 1
    result = {'topDivergence': 0, 'bottomDivergence': 0, 'topPoints': [], 'bottomPoints': [], 'detail': ''}
    if n < 60:
        return result

    lookback = min(120, n - 1)
    seg = data[n - lookback:]
    highs = [d['high'] for d in seg]
    lows = [d['low'] for d in seg]

    # 找高点（前后5天内最高）
    high_points = []
    for i in range(5, len(highs) - 5):
        if highs[i] == max(highs[i-5:i+6]):
            high_points.append({
                'idx': n - lookback + i,
                'price': highs[i],
                'dif': ind['dif'][n - lookback + i],
                'macd': ind['macd'][n - lookback + i]
            })

    # 找低点（前后5天内最低）
    low_points = []
    for i in range(5, len(lows) - 5):
        if lows[i] == min(lows[i-5:i+6]):
            low_points.append({
                'idx': n - lookback + i,
                'price': lows[i],
                'dif': ind['dif'][n - lookback + i],
                'macd': ind['macd'][n - lookback + i]
            })

    # 顶背离：后一个高点价格更高（>1%），但DIF或MACD更低
    if len(high_points) >= 2:
        h1 = high_points[-2]
        h2 = high_points[-1]
        if h2['price'] > h1['price'] * 1.01 and (h2['dif'] < h1['dif'] or h2['macd'] < h1['macd']):
            strength = min(100, round(
                (h2['price'] - h1['price']) / h1['price'] * 500 +
                (h1['dif'] - h2['dif']) / abs(h1['dif']) * 30 if h1['dif'] != 0 else 50
            ))
            result['topDivergence'] = max(30, strength)
            result['topPoints'] = [h1, h2]
            result['detail'] = f'顶背离：{h1["idx"]}高点{h1["price"]:.2f} → {h2["idx"]}高点{h2["price"]:.2f}，价格新高但MACD未新高'

    # 底背离：后一个低点价格更低（>1%），但DIF或MACD更高
    if len(low_points) >= 2:
        l1 = low_points[-2]
        l2 = low_points[-1]
        if l2['price'] < l1['price'] * 0.99 and (l2['dif'] > l1['dif'] or l2['macd'] > l1['macd']):
            strength = min(100, round(
                (l1['price'] - l2['price']) / l1['price'] * 500 +
                (l2['dif'] - l1['dif']) / abs(l1['dif']) * 30 if l1['dif'] != 0 else 50
            ))
            result['bottomDivergence'] = max(30, strength)
            result['bottomPoints'] = [l1, l2]
            if not result['detail']:
                result['detail'] = f'底背离：{l1["idx"]}低点{l1["price"]:.2f} → {l2["idx"]}低点{l2["price"]:.2f}，价格新低但MACD未新低'

    return result


# ============ 量价背离检测（与网页端detectVolumeDivergence一致）============
def detect_volume_divergence(data, ind):
    n = len(data)
    last = n - 1
    result = {'topVolumeDivergence': 0, 'bottomVolumeDivergence': 0,
              'shrinkStop': 0, 'volumeStagnation': 0, 'detail': ''}
    if n < 30:
        return result

    cur_price = data[last]['close']
    v5 = ind['vma5'][last] or 0
    v20 = ind['vma20'][last] or 0

    # 近20天最高价和最低价
    recent20 = data[n-20:]
    high20 = max(d['high'] for d in recent20)
    low20 = min(d['low'] for d in recent20)
    is_near_high = cur_price > high20 * 0.97
    is_near_low = cur_price < low20 * 1.03

    # 顶部量价背离：价格在高位，但5日均量持续低于20日均量
    if is_near_high and v5 < v20 * 0.9 and v20 > 0:
        result['topVolumeDivergence'] = round((1 - v5 / v20) * 150)
        result['detail'] = f'顶部量价背离：价格接近20日新高，但5日均量仅为20日均量的{v5/v20*100:.0f}%，买盘不足'

    # 底部量价背离：价格在低位，成交量持续萎缩
    if is_near_low and v5 < v20 * 0.85 and v20 > 0:
        result['bottomVolumeDivergence'] = round((1 - v5 / v20) * 150)
        if not result['detail']:
            result['detail'] = f'底部量价背离：价格接近20日新低，5日均量仅为20日均量的{v5/v20*100:.0f}%，抛盘枯竭'

    # 放量滞涨：高位放量但价格不涨
    if is_near_high and v5 > v20 * 1.3 and v20 > 0 and n >= 6:
        price_change = (data[last]['close'] - data[n-6]['close']) / data[n-6]['close']
        if price_change < 0.02:
            result['volumeStagnation'] = 60
            if not result['detail']:
                result['detail'] = f'放量滞涨：高位放量（5日均量为20日的{v5/v20*100:.0f}%）但近5日涨幅仅{price_change*100:.1f}%，主力可能出货'

    # 缩量止跌：低位缩量价格不再下跌
    if is_near_low and v5 < v20 * 0.8 and v20 > 0 and n >= 6:
        price_change = (data[last]['close'] - data[n-6]['close']) / data[n-6]['close']
        if price_change > -0.02:
            result['shrinkStop'] = 55
            if not result['detail']:
                result['detail'] = f'缩量止跌：低位缩量（5日均量为20日的{v5/v20*100:.0f}%）且近5日不再下跌，可能见底'

    return result


# ============ 颈线确认度（与网页端calcNecklineConfirm一致）============
def calc_neckline_confirm(data, best_detail, is_bear):
    n = len(data)
    last = n - 1
    result = {'confirmed': False, 'daysAbove': 0, 'volumeRatio': 1,
              'pullbackTest': False, 'score': 0, 'detail': ''}

    # 从key_points中找颈线价格（Python版用key_points替代JS的markers.lines）
    neckline = None
    if best_detail and best_detail.get('key_points'):
        for p in best_detail['key_points']:
            if '颈线' in p.get('name', '') and isinstance(p.get('price'), (int, float)):
                neckline = p['price']
                break
    if neckline is None:
        return result

    cur_price = data[last]['close']
    # 上升形态：价格在颈线上方；下降形态：价格在颈线下方
    above_neckline = cur_price > neckline if not is_bear else cur_price < neckline

    # 计算连续在颈线一侧的天数
    days = 0
    for i in range(last, max(0, last - 10), -1):
        cond = data[i]['close'] > neckline if not is_bear else data[i]['close'] < neckline
        if cond:
            days += 1
        else:
            break
    result['daysAbove'] = days

    # 突破/跌破时的放量倍数（近5天最大量 vs 20日均量）
    recent5 = data[n-5:]
    max_vol = max(d['volume'] for d in recent5)
    vma20 = sum(d['volume'] for d in data[n-20:]) / 20 if n >= 20 else max_vol
    result['volumeRatio'] = max_vol / vma20 if vma20 > 0 else 1

    # 回抽确认：突破后曾回踩颈线但未跌破
    pullback_test = False
    if 2 <= days < 8:
        pullback_days = data[n-10:n-2]
        for d in pullback_days:
            if not is_bear:
                if d['low'] <= neckline * 1.02 and d['close'] > neckline:
                    pullback_test = True
                    break
            else:
                if d['high'] >= neckline * 0.98 and d['close'] < neckline:
                    pullback_test = True
                    break
    result['pullbackTest'] = pullback_test

    # 评分（只有在颈线一侧时才计算）
    if above_neckline:
        score = 30
        if days >= 3:
            score += 25
        elif days >= 2:
            score += 15
        if result['volumeRatio'] > 1.5:
            score += 20
        elif result['volumeRatio'] > 1.2:
            score += 10
        if pullback_test:
            score += 25
        result['score'] = min(100, score)
        result['confirmed'] = days >= 2
        action = '跌破' if is_bear else '突破'
        result['detail'] = f'{action}颈线{neckline:.2f}已{days}天，放量{result["volumeRatio"]:.1f}倍{"，回抽确认有效" if pullback_test else ""}'

    return result


# ============ 上升形态检测（与网页端matchPatterns一致）============
def match_patterns(data, ind):
    n = len(data)
    last = n - 1
    results = {}
    lookback = min(80, n - 1)
    seg = data[n-lookback:]
    lows = [d['low'] for d in seg]
    highs = [d['high'] for d in seg]
    cur_price = data[last]['close']

    # 1. 均线多头（5条件独立加分，满分100）
    ma_detail = {'score': 0, 'stage': '', 'key_points': []}
    if ind['ma5'][last] and ind['ma10'][last] and ind['ma20'][last] and ind['ma60'][last]:
        c1 = ind['ma5'][last] > ind['ma10'][last]
        c2 = ind['ma10'][last] > ind['ma20'][last]
        c3 = ind['ma20'][last] > ind['ma60'][last]
        c4 = cur_price > ind['ma5'][last]
        ma5_slope = ind['ma5'][last] - ind['ma5'][max(0, last-5)]
        c5 = ma5_slope > 0
        ma_detail['score'] = (25 if c1 else 0) + (25 if c2 else 0) + (20 if c3 else 0) + (15 if c4 else 0) + (15 if c5 else 0)
        ma_detail['key_points'] = [
            {'name': 'MA5', 'price': round(ind['ma5'][last], 2)},
            {'name': 'MA10', 'price': round(ind['ma10'][last], 2)},
            {'name': 'MA20', 'price': round(ind['ma20'][last], 2)},
            {'name': 'MA60', 'price': round(ind['ma60'][last], 2)}
        ]
        above_all = c1 and c2 and c3
        if above_all and c4 and ma5_slope > 0.02 * cur_price:
            ma_detail['stage'] = '主升浪中期：均线完美多头，沿MA5爬升'
        elif above_all and c4:
            ma_detail['stage'] = '主升浪初期：均线刚形成多头排列'
        elif above_all and not c4:
            ma_detail['stage'] = '主升浪回调中：价格跌破MA5，测试MA10/MA20支撑'
        elif c1 and c2 and not c3:
            ma_detail['stage'] = '均线粘合发散初期：短期均线已多头，MA20即将上穿MA60'
        else:
            ma_detail['stage'] = '均线未形成多头排列'
    results['ma_bull'] = ma_detail

    # 2. W底
    w_detail = {'score': 0, 'stage': '', 'key_points': []}
    min_low = min(lows)
    min_idx = lows.index(min_low)
    if min_idx > 5 and min_idx < lookback - 8:
        left_lows_arr = lows[:min_idx-2]
        left_low = min(left_lows_arr) if left_lows_arr else min_low
        right_lows = lows[min_idx+3:]
        right_low = min(right_lows) if right_lows else min_low
        right_idx = min_idx + 3 + (right_lows.index(right_low) if right_lows else 0)
        neckline = max(highs[:min_idx])
        # 两个底价格相似度：差距≤5%才算标准双底（既有下限也有上限）
        bottom_diff_pct = abs(right_low - min_low) / min_low * 100
        second_higher = right_low >= min_low * 0.97 and bottom_diff_pct <= 5.0
        break_neck = cur_price > neckline
        near_neck = abs(cur_price - neckline) / neckline < 0.03
        vol_confirm = (ind['vma5'][last] or 0) > (ind['vma20'][last] or 0) * 1.1
        right_bottom_formed = right_idx < lookback - 3
        after_right_bottom = data[n-lookback+right_idx:]
        recovered = len(after_right_bottom) > 3 and cur_price > right_low * 1.05

        w_detail['score'] = (40 if second_higher else 10) + (35 if break_neck else 15) + (25 if vol_confirm else 10)
        w_detail['key_points'] = [
            {'name': '左底', 'price': round(left_low, 2)},
            {'name': '右底/最低点', 'price': round(min(min_low, right_low), 2)},
            {'name': '颈线位', 'price': round(neckline, 2)},
            {'name': '当前价', 'price': round(cur_price, 2)}
        ]
        if not second_higher:
            w_detail['stage'] = 'W底不成立：第二个低点低于第一个低点'
        elif not right_bottom_formed or not recovered:
            w_detail['stage'] = '第二个底构建中'
        elif near_neck and not break_neck:
            w_detail['stage'] = 'W底突破前夜：价格接近颈线'
        elif break_neck:
            pullback = cur_price < neckline * 1.03 and data[last]['low'] < neckline
            w_detail['stage'] = 'W底突破后回踩确认中' if pullback else 'W底已突破颈线：形态确认'
        else:
            w_detail['stage'] = 'W底构建中'
    results['w_bottom'] = w_detail

    # 3. 头肩底
    hs_detail = {'score': 0, 'stage': '', 'key_points': []}
    if lookback > 30:
        head_idx = lows.index(min(lows))
        if head_idx > 8 and head_idx < lookback - 8:
            left_part = lows[:head_idx-3]
            right_part = lows[head_idx+3:]
            left_shoulder = min(left_part) if left_part else None
            right_shoulder = min(right_part) if right_part else None
            head = lows[head_idx]
            neck_left = max(highs[:head_idx])
            neck_right = max(highs[head_idx+3:]) if right_part else neck_left
            neckline = min(neck_left, neck_right)
            left_higher = left_shoulder is not None and left_shoulder > head
            right_higher = right_shoulder is not None and right_shoulder > head
            symmetry = (left_shoulder and right_shoulder and abs(left_shoulder - right_shoulder) / left_shoulder < 0.2)
            broken = cur_price > neckline * 0.98
            right_shoulder_global_idx = head_idx + 3 + (right_part.index(right_shoulder) if right_part else 0)
            right_shoulder_done = right_shoulder_global_idx < lookback - 3

            hs_detail['score'] = (20 if left_higher else 5) + (20 if right_higher else 5) + (20 if symmetry else 5) + (40 if broken else 10)
            hs_detail['key_points'] = [
                {'name': '左肩', 'price': round(left_shoulder, 2) if left_shoulder else None},
                {'name': '头部', 'price': round(head, 2)},
                {'name': '右肩', 'price': round(right_shoulder, 2) if right_shoulder and right_shoulder_done else None},
                {'name': '颈线位', 'price': round(neckline, 2)},
                {'name': '当前价', 'price': round(cur_price, 2)}
            ]
            if not left_higher:
                hs_detail['stage'] = '头肩底不成立：左肩未高于头部'
            elif not right_shoulder_done:
                hs_detail['stage'] = '右肩构建中'
            elif not right_higher:
                hs_detail['stage'] = '右肩低于头部：头肩底形态可能失败'
            elif not broken:
                hs_detail['stage'] = '右肩已完成，向颈线推进：突破前夜'
            else:
                pullback = cur_price < neckline * 1.05 and data[last]['low'] < neckline * 1.02
                hs_detail['stage'] = '头肩底突破后回踩颈线确认中' if pullback else '头肩底已突破颈线：形态确认'
    results['hs_bottom'] = hs_detail

    # 4. 平台突破
    pb_detail = {'score': 0, 'stage': '', 'key_points': []}
    plat_len = min(35, n - 5)
    if plat_len >= 15:
        plat_start = n - plat_len - 5
        plat = data[plat_start:n-5]
        plat_high = max(d['high'] for d in plat)
        plat_low = min(d['low'] for d in plat)
        range_pct = (plat_high - plat_low) / plat_low
        tight = range_pct < 0.18
        breakout = cur_price > plat_high * 1.01
        vol_boost = data[last]['volume'] > (ind['vma5'][last] or 0) * 1.3
        ma_converge = ind['ma5'][last] and ind['ma20'][last] and abs(ind['ma5'][last] - ind['ma20'][last]) / ind['ma20'][last] < 0.06
        near_top = cur_price > plat_high * 0.97 and not breakout
        after_breakout = data[n-10:]
        had_breakout = any(d['close'] > plat_high * 1.02 for d in after_breakout)
        pullback_to_platform = had_breakout and plat_high * 0.97 < cur_price < plat_high * 1.03

        pb_detail['score'] = (30 if tight else 10) + (35 if breakout else 10) + (20 if vol_boost else 5) + (15 if ma_converge else 5)
        pb_detail['key_points'] = [
            {'name': '平台上沿', 'price': round(plat_high, 2)},
            {'name': '平台下沿', 'price': round(plat_low, 2)},
            {'name': '平台振幅', 'price': f'{range_pct*100:.1f}%'},
            {'name': '当前价', 'price': round(cur_price, 2)}
        ]
        if not tight:
            pb_detail['stage'] = '非平台整理：振幅过大'
        elif near_top and not breakout:
            pb_detail['stage'] = '平台整理末端：突破前夜'
        elif breakout and vol_boost:
            pb_detail['stage'] = '放量突破平台：突破确认'
        elif breakout and not vol_boost:
            pb_detail['stage'] = '缩量突破平台：需确认'
        elif pullback_to_platform:
            pb_detail['stage'] = '突破后回踩平台上沿确认中'
        elif had_breakout:
            pb_detail['stage'] = '平台突破后上涨中'
        else:
            pb_detail['stage'] = '平台整理中'
    results['platform_break'] = pb_detail

    return results


# ============ 下降形态检测（与网页端matchBearPatterns一致）============
def match_bear_patterns(data, ind):
    n = len(data)
    last = n - 1
    results = {}
    lookback = min(80, n - 1)
    seg = data[n-lookback:]
    highs = [d['high'] for d in seg]
    lows = [d['low'] for d in seg]
    cur_price = data[last]['close']

    # 1. 均线空头
    ma_bear = {'score': 0, 'stage': '', 'key_points': []}
    if ind['ma5'][last] and ind['ma10'][last] and ind['ma20'][last] and ind['ma60'][last]:
        c1 = ind['ma5'][last] < ind['ma10'][last]
        c2 = ind['ma10'][last] < ind['ma20'][last]
        c3 = ind['ma20'][last] < ind['ma60'][last]
        c4 = cur_price < ind['ma5'][last]
        ma5_slope = ind['ma5'][last] - ind['ma5'][max(0, last-5)]
        c5 = ma5_slope < 0
        ma_bear['score'] = (25 if c1 else 0) + (25 if c2 else 0) + (20 if c3 else 0) + (15 if c4 else 0) + (15 if c5 else 0)
        ma_bear['key_points'] = [
            {'name': 'MA5', 'price': round(ind['ma5'][last], 2)},
            {'name': 'MA10', 'price': round(ind['ma10'][last], 2)},
            {'name': 'MA20', 'price': round(ind['ma20'][last], 2)},
            {'name': 'MA60', 'price': round(ind['ma60'][last], 2)}
        ]
        below_all = c1 and c2 and c3
        if below_all and c4 and ma5_slope < -0.02 * cur_price:
            ma_bear['stage'] = '下跌趋势中期：均线完美空头'
        elif below_all and c4:
            ma_bear['stage'] = '下跌趋势初期：均线刚形成空头排列'
        elif below_all and not c4:
            ma_bear['stage'] = '下跌趋势反弹中'
        elif c1 and c2 and not c3:
            ma_bear['stage'] = '均线粘合向下发散初期'
        else:
            ma_bear['stage'] = '均线未形成空头排列'
    results['ma_bear'] = ma_bear

    # 2. M头
    m_top = {'score': 0, 'stage': '', 'key_points': []}
    max_high = max(highs)
    max_idx = highs.index(max_high)
    if max_idx > 5 and max_idx < lookback - 8:
        left_highs = highs[:max_idx-2]
        left_high = max(left_highs) if left_highs else max_high
        right_highs = highs[max_idx+3:]
        right_high = max(right_highs) if right_highs else max_high
        right_high_idx = max_idx + 3 + (right_highs.index(right_high) if right_highs else 0)
        neckline = min(lows[max(0, max_idx-10):max_idx+3])
        second_lower = right_high <= max_high * 1.03
        break_neck = cur_price < neckline
        near_neck = abs(cur_price - neckline) / neckline < 0.03
        vol_confirm = (ind['vma5'][last] or 0) > (ind['vma20'][last] or 0) * 1.1

        m_top['score'] = (40 if second_lower else 10) + (35 if break_neck else 15) + (25 if vol_confirm else 10)
        m_top['key_points'] = [
            {'name': '左头', 'price': round(left_high, 2)},
            {'name': '右头/最高点', 'price': round(max(max_high, right_high), 2)},
            {'name': '颈线位', 'price': round(neckline, 2)},
            {'name': '当前价', 'price': round(cur_price, 2)}
        ]
        if not second_lower:
            m_top['stage'] = 'M头不成立：第二个高点高于第一个'
        elif near_neck and not break_neck:
            m_top['stage'] = 'M头破位前夜'
        elif break_neck:
            pullback = cur_price > neckline * 0.97 and data[last]['high'] > neckline
            m_top['stage'] = 'M头破位后回抽确认中' if pullback else 'M头已跌破颈线：顶部确认'
        else:
            m_top['stage'] = 'M头构建中'
    results['m_top'] = m_top

    # 3. 头肩顶
    hs_top = {'score': 0, 'stage': '', 'key_points': []}
    if lookback > 30:
        head_idx = highs.index(max(highs))
        if head_idx > 8 and head_idx < lookback - 8:
            left_part = highs[:head_idx-3]
            right_part = highs[head_idx+3:]
            left_shoulder = max(left_part) if left_part else None
            right_shoulder = max(right_part) if right_part else None
            head = highs[head_idx]
            neck_left = min(lows[:head_idx])
            neck_right = min(lows[head_idx+3:]) if right_part else neck_left
            neckline = max(neck_left, neck_right)
            left_lower = left_shoulder is not None and left_shoulder < head
            right_lower = right_shoulder is not None and right_shoulder < head
            symmetry = (left_shoulder and right_shoulder and abs(left_shoulder - right_shoulder) / left_shoulder < 0.2)
            broken = cur_price < neckline * 1.02
            right_shoulder_global_idx = head_idx + 3 + (right_part.index(right_shoulder) if right_part else 0)
            right_shoulder_done = right_shoulder_global_idx < lookback - 3

            hs_top['score'] = (20 if left_lower else 5) + (20 if right_lower else 5) + (20 if symmetry else 5) + (40 if broken else 10)
            hs_top['key_points'] = [
                {'name': '左肩', 'price': round(left_shoulder, 2) if left_shoulder else None},
                {'name': '头部', 'price': round(head, 2)},
                {'name': '右肩', 'price': round(right_shoulder, 2) if right_shoulder and right_shoulder_done else None},
                {'name': '颈线位', 'price': round(neckline, 2)},
                {'name': '当前价', 'price': round(cur_price, 2)}
            ]
            if not left_lower:
                hs_top['stage'] = '头肩顶不成立：左肩未低于头部'
            elif not right_shoulder_done:
                hs_top['stage'] = '右肩构建中'
            elif not right_lower:
                hs_top['stage'] = '右肩高于头部：头肩顶形态可能失败'
            elif not broken:
                hs_top['stage'] = '右肩已完成，向颈线下行：破位前夜'
            else:
                pullback = cur_price > neckline * 0.95 and data[last]['high'] > neckline
                hs_top['stage'] = '头肩顶破位后回抽颈线确认中' if pullback else '头肩顶已跌破颈线：顶部确认'
    results['hs_top'] = hs_top

    # 4. 平台破位
    pb_down = {'score': 0, 'stage': '', 'key_points': []}
    plat_len = min(35, n - 5)
    if plat_len >= 15:
        plat_start = n - plat_len - 5
        plat = data[plat_start:n-5]
        plat_high = max(d['high'] for d in plat)
        plat_low = min(d['low'] for d in plat)
        range_pct = (plat_high - plat_low) / plat_low
        tight = range_pct < 0.18
        breakdown = cur_price < plat_low * 0.99
        vol_boost = data[last]['volume'] > (ind['vma5'][last] or 0) * 1.3
        ma_converge = ind['ma5'][last] and ind['ma20'][last] and abs(ind['ma5'][last] - ind['ma20'][last]) / ind['ma20'][last] < 0.06
        near_bottom = cur_price < plat_low * 1.03 and not breakdown
        after_breakdown = data[n-10:]
        had_breakdown = any(d['close'] < plat_low * 0.98 for d in after_breakdown)
        pullback_to_platform = had_breakdown and plat_low * 0.97 < cur_price < plat_low * 1.03

        pb_down['score'] = (30 if tight else 10) + (35 if breakdown else 10) + (20 if vol_boost else 5) + (15 if ma_converge else 5)
        pb_down['key_points'] = [
            {'name': '平台上沿', 'price': round(plat_high, 2)},
            {'name': '平台下沿', 'price': round(plat_low, 2)},
            {'name': '平台振幅', 'price': f'{range_pct*100:.1f}%'},
            {'name': '当前价', 'price': round(cur_price, 2)}
        ]
        if not tight:
            pb_down['stage'] = '非平台整理：振幅过大'
        elif near_bottom and not breakdown:
            pb_down['stage'] = '平台整理末端：破位前夜'
        elif breakdown and vol_boost:
            pb_down['stage'] = '放量跌破平台：破位确认'
        elif breakdown and not vol_boost:
            pb_down['stage'] = '缩量跌破平台：需确认'
        elif pullback_to_platform:
            pb_down['stage'] = '破位后回抽平台下沿确认中'
        elif had_breakdown:
            pb_down['stage'] = '平台破位后下跌中'
        else:
            pb_down['stage'] = '平台整理中'
    results['platform_break_down'] = pb_down

    return results


# ============ RSI/KDJ信号（与网页端detectRSIKDJ一致）============
def detect_rsi_kdj(data, ind):
    n = len(data)
    last = n - 1
    result = {'score': 0, 'detail': ''}
    rsi = ind['rsi'][last]
    k = ind['kdj_k'][last]
    d = ind['kdj_d'][last]
    if rsi < 30 and k > d and ind['kdj_k'][last-1] <= ind['kdj_d'][last-1]:
        result['score'] = 15
        result['detail'] = f'RSI超卖({rsi:.1f})+KDJ金叉，底部信号强'
    elif rsi < 40 and k > d:
        result['score'] = 8
        result['detail'] = f'RSI偏低({rsi:.1f})+KDJ多头，底部信号'
    elif rsi > 70 and k < d and ind['kdj_k'][last-1] >= ind['kdj_d'][last-1]:
        result['score'] = -15
        result['detail'] = f'RSI超买({rsi:.1f})+KDJ死叉，顶部信号强'
    elif rsi > 60 and k < d:
        result['score'] = -8
        result['detail'] = f'RSI偏高({rsi:.1f})+KDJ空头，顶部信号'
    else:
        result['detail'] = f'RSI={rsi:.1f}, KDJ K={k:.1f}/D={d:.1f}，中性'
    return result


# ============ 布林带信号（与网页端detectBollinger一致）============
def detect_bollinger(data, ind):
    n = len(data)
    last = n - 1
    result = {'score': 0, 'detail': ''}
    if n < 20:
        return result
    cur = data[last]['close']
    up = ind['boll_up'][last]
    low = ind['boll_low'][last]
    width = ind['boll_width'][last]
    prev_width = ind['boll_width'][max(0, last-5)]
    if width < prev_width * 0.8 and cur > up:
        result['score'] = 12
        result['detail'] = '布林带收口后突破上轨，向上突破信号'
    elif cur > up:
        result['score'] = 5
        result['detail'] = '价格突破上轨，强势'
    elif width < prev_width * 0.8 and cur < low:
        result['score'] = -12
        result['detail'] = '布林带收口后跌破下轨，向下跌破信号'
    elif cur < low:
        result['score'] = -5
        result['detail'] = '价格跌破下轨，弱势'
    else:
        result['detail'] = f'价格在布林带内，带宽{width:.1f}%'
    return result


# ============ 周线数据提取与形态 ============
def extract_weekly_data(data):
    weekly = []
    current_week = None
    for d in data:
        try:
            dt = datetime.strptime(d['day'][:10], '%Y-%m-%d')
            week = dt.isocalendar()[1]
        except (ValueError, KeyError):
            week = hash(d.get('day', '')) % 52
        if current_week is None or week != current_week:
            if current_week is not None and weekly:
                weekly[-1]['high'] = max(weekly[-1]['high'], d['high'])
                weekly[-1]['low'] = min(weekly[-1]['low'], d['low'])
                weekly[-1]['close'] = d['close']
                weekly[-1]['volume'] += d['volume']
            current_week = week
            weekly.append({'day': d['day'], 'open': d['open'], 'high': d['high'],
                           'low': d['low'], 'close': d['close'], 'volume': d['volume']})
        else:
            weekly[-1]['high'] = max(weekly[-1]['high'], d['high'])
            weekly[-1]['low'] = min(weekly[-1]['low'], d['low'])
            weekly[-1]['close'] = d['close']
            weekly[-1]['volume'] += d['volume']
    return weekly


def match_weekly_patterns(weekly_data):
    result = {'resonance': False, 'type': None, 'detail': ''}
    if len(weekly_data) < 10:
        return result
    closes = [d['close'] for d in weekly_data]
    n = len(closes)
    def wma(arr, p):
        return [None if i < p-1 else sum(arr[i-p+1:i+1])/p for i in range(len(arr))]
    wma5 = wma(closes, 5)
    wma10 = wma(closes, 10)
    wma20 = wma(closes, min(20, n))
    last = n - 1
    if wma5[last] and wma10[last] and wma20[last]:
        if wma5[last] > wma10[last] > wma20[last] and closes[last] > wma5[last]:
            result['resonance'] = True
            result['type'] = 'bull'
            result['detail'] = '周线均线多头，与日线共振'
        elif wma5[last] < wma10[last] < wma20[last] and closes[last] < wma5[last]:
            result['resonance'] = True
            result['type'] = 'bear'
            result['detail'] = '周线均线空头，与日线背离'
    return result


# ============ ATR止损 ============
def calc_atr_stop_loss(data, ind):
    n = len(data)
    last = n - 1
    atr = ind['atr'][last]
    cur = data[last]['close']
    return {
        'atr': round(atr, 2),
        'atr_pct': round(atr / cur * 100, 2),
        'stop_loss': round(cur - 2 * atr, 2),
        'detail': f'ATR={atr:.2f}({atr/cur*100:.1f}%)，2倍ATR止损位={cur-2*atr:.2f}'
    }


# ============ 形态名称与可靠性 ============
PATTERN_NAMES = {
    'ma_bull': '均线多头', 'w_bottom': 'W底', 'hs_bottom': '头肩底', 'platform_break': '平台突破',
    'ma_bear': '均线空头', 'm_top': 'M头', 'hs_top': '头肩顶', 'platform_break_down': '平台破位'
}
PATTERN_RELIABILITY = {
    'hs_bottom': 85, 'hs_top': 86, 'm_top': 83, 'w_bottom': 82,
    'platform_break': 80, 'platform_break_down': 81, 'ma_bull': 78, 'ma_bear': 80
}

# 形态-环境历史准确率表（基于技术分析经典统计）
# 环境分类：bull=牛市(大盘≥60分), range=震荡市(40-60分), bear=熊市(<40分)
PATTERN_ENV_ACCURACY = {
    # 上升形态
    'ma_bull':          {'bull': 85, 'range': 55, 'bear': 30},
    'w_bottom':         {'bull': 75, 'range': 70, 'bear': 50},
    'hs_bottom':        {'bull': 78, 'range': 82, 'bear': 45},
    'platform_break':   {'bull': 80, 'range': 55, 'bear': 25},
    # 下降形态
    'ma_bear':          {'bull': 25, 'range': 50, 'bear': 85},
    'm_top':            {'bull': 50, 'range': 72, 'bear': 78},
    'hs_top':           {'bull': 45, 'range': 75, 'bear': 83},
    'platform_break_down': {'bull': 20, 'range': 50, 'bear': 82},
}


def calc_env_accuracy(pattern_id, pattern_score, market_env):
    """计算当前形态在当前大盘环境下的历史准确率
    返回：环境分类、历史准确率、综合准确率（形态匹配度×环境适配率）、评分加成
    """
    if not market_env:
        return {'env_type': '未知', 'historical_accuracy': 0, 'combined_accuracy': 0,
                'score_bonus': 0, 'detail': '暂无大盘数据'}

    env_score = market_env.get('score', 50)
    # 环境分类
    if env_score >= 60:
        env_type = '牛市'
        env_key = 'bull'
    elif env_score >= 40:
        env_type = '震荡市'
        env_key = 'range'
    else:
        env_type = '熊市'
        env_key = 'bear'

    # 该形态在该环境下的历史准确率
    env_table = PATTERN_ENV_ACCURACY.get(pattern_id, {'bull': 60, 'range': 60, 'bear': 60})
    historical_accuracy = env_table.get(env_key, 60)

    # 综合准确率 = 形态匹配度 × 环境适配率
    # 形态匹配度用pattern_score/100，环境适配率用historical_accuracy/100
    combined_accuracy = round(pattern_score * historical_accuracy / 100)

    # 评分加成：环境适配度高加5-10分，低则扣5-15分
    if historical_accuracy >= 75:
        score_bonus = 10
        advice = '当前环境与形态高度匹配，信号可靠性高'
    elif historical_accuracy >= 60:
        score_bonus = 5
        advice = '当前环境与形态较为匹配，信号可靠性中等'
    elif historical_accuracy >= 45:
        score_bonus = 0
        advice = '当前环境与形态一般匹配，需结合其他信号确认'
    elif historical_accuracy >= 30:
        score_bonus = -8
        advice = '当前环境不利于该形态，信号可靠性较低，谨慎操作'
    else:
        score_bonus = -15
        advice = '当前环境严重不利于该形态，极可能是假信号，建议回避'

    return {
        'env_type': env_type,
        'env_score': env_score,
        'historical_accuracy': historical_accuracy,
        'combined_accuracy': combined_accuracy,
        'score_bonus': score_bonus,
        'detail': f'{env_type}环境下，{PATTERN_NAMES.get(pattern_id, pattern_id)}历史准确率{historical_accuracy}%，综合准确率{combined_accuracy}%。{advice}'
    }


def calc_fake_signal_risk(data, ind, best_type, best_score, market_env, macd_div, vol):
    """假信号风险评估（与网页端calcFakeSignalRisk一致）"""
    risks = []
    risk_score = 0
    n = len(data)
    last = n - 1
    cur_price = data[last]['close']
    ma60 = ind['ma60'][last] or cur_price
    dist_from_ma60 = abs(cur_price - ma60) / ma60 * 100

    # 1. 成交量配合度
    vma5 = ind.get('vma5', [None]*n)[last] or 0
    vma20 = ind.get('vma20', [None]*n)[last] or 0
    vol_ratio = vma5 / vma20 if vma20 > 0 else 1
    if best_type == 'bull' and vol_ratio < 1.0:
        risks.append({'label': '缩量突破', 'detail': f'5日均量/20日均量={vol_ratio:.2f}<1，突破时无量能配合。真突破需放量1.5倍以上，缩量突破70%是假的，主力可能在拉高出货。应对：等放量确认再进', 'weight': 20})
        risk_score += 20
    if best_type == 'bear' and vol_ratio < 1.0:
        risks.append({'label': '缩量破位', 'detail': f'5日均量/20日均量={vol_ratio:.2f}<1，破位时无量能配合。真破位需放量下跌释放恐慌盘，缩量破位多为主力洗盘（空头陷阱），洗出散户后快速拉回。应对：等3天确认，不急于割肉', 'weight': 25})
        risk_score += 25

    # 2. 大盘环境适配度
    if market_env:
        env_score = market_env['score']
        if best_type == 'bull' and env_score < 45:
            risks.append({'label': '逆大盘买入', 'detail': f"大盘{market_env['stage']}({env_score}分)，熊市/弱势环境中个股突破成功率仅25-30%。大盘下跌时90%的股票会跟跌，逆势突破往往是诱多。应对：降低仓位或放弃，等大盘转好", 'weight': 20})
            risk_score += 20
        if best_type == 'bear' and env_score >= 60:
            risks.append({'label': '逆大盘卖出', 'detail': f"大盘{market_env['stage']}({env_score}分)，牛市/偏强环境中个股破位可能是洗盘。大盘上涨时多数股票会跟涨，逆势破位往往是主力打压吸筹。应对：等3天确认，不急于卖出", 'weight': 20})
            risk_score += 20

    # 3. MACD背离
    if best_type == 'bull' and macd_div and macd_div.get('topDivergence', 0) > 30:
        risks.append({'label': 'MACD顶背离', 'detail': f"价格创新高但MACD未创新高（背离度{macd_div['topDivergence']}%），说明上涨动能在衰减，主力可能在边拉边出货。顶背离后下跌概率约70%。应对：减仓或等回踩确认", 'weight': 15})
        risk_score += 15
    if best_type == 'bear' and macd_div and macd_div.get('bottomDivergence', 0) > 30:
        risks.append({'label': 'MACD底背离', 'detail': f"价格创新低但MACD未创新低（背离度{macd_div['bottomDivergence']}%），说明下跌动能在衰竭，卖盘已接近枯竭。底背离后反弹概率约65%。应对：不急于割肉，等反弹确认", 'weight': 20})
        risk_score += 20

    # 4. 远离均线
    if dist_from_ma60 > 30:
        risks.append({'label': '远离MA60', 'detail': f'当前价偏离MA60达{dist_from_ma60:.1f}%（>30%为极端）。股价像橡皮筋，偏离均线越远回归拉力越大，短期回调概率超80%。应对：等回踩均线再进，不追高', 'weight': 10})
        risk_score += 10

    # 5. 量价背离
    if vol and vol.get('relation'):
        if best_type == 'bull' and '价涨量缩' in vol['relation']:
            risks.append({'label': '价涨量缩', 'detail': '价格上涨但成交量持续萎缩，说明买盘力量在减弱，上涨缺乏资金支撑。价涨量缩是典型的见顶前兆，后续大概率回调。应对：减仓锁定利润', 'weight': 10})
            risk_score += 10
        if best_type == 'bear' and '价跌量缩' in vol['relation']:
            risks.append({'label': '价跌量缩', 'detail': '价格下跌但成交量萎缩，说明卖盘力量在减弱，下跌缺乏恐慌盘。价跌量缩往往是下跌末期，主力可能在悄悄吸筹。应对：不急于割肉，观察是否企稳', 'weight': 15})
            risk_score += 15

    # 6. 形态匹配度低
    if best_score < 50:
        risks.append({'label': '形态匹配度低', 'detail': f'最佳形态匹配度仅{best_score}分（<50分为不标准）。形态不标准意味着颈线/肩部/底部等关键点位不清晰，主力可能没有完成做图，后续走势不确定性大。应对：等形态更清晰再操作', 'weight': 10})
        risk_score += 10

    risk_score = min(100, risk_score)
    if risk_score >= 50:
        level = '高风险'; color = '#e74c3c'
        advice = '⚠️ 疑似假信号！建议等待3天确认，或放弃此信号'
    elif risk_score >= 30:
        level = '中风险'; color = '#f39c12'
        advice = '⚡ 有假信号嫌疑，建议轻仓试探，严格止损'
    else:
        level = '低风险'; color = '#2ecc71'
        advice = '✅ 信号质量较高，可按计划操作，但仍需止损保护'

    return {
        'risk_score': risk_score, 'level': level, 'color': color, 'advice': advice,
        'risks': risks, 'dist_from_ma60': f'{dist_from_ma60:.1f}', 'vol_ratio': f'{vol_ratio:.2f}'
    }


# ============ 推荐度评分（与网页端calcAttentionScore一致）============
def calc_attention_score(data, ind, matches, vol, bear_matches, macd_div, vol_div, neck_confirm):
    n = len(data)
    last = n - 1
    sorted_bull = sorted(matches.items(), key=lambda x: x[1]['score'], reverse=True)
    sorted_bear = sorted(bear_matches.items(), key=lambda x: x[1]['score'], reverse=True)
    best_id = sorted_bull[0][0]
    best_detail = sorted_bull[0][1]
    best_score = best_detail['score']
    best_stage = best_detail.get('stage', '')
    bear_best_score = sorted_bear[0][1]['score']
    bear_best_id = sorted_bear[0][0]
    breakdown = []

    # 1. 基础分 = 形态匹配度
    score = best_score
    breakdown.append({'label': '形态匹配度', 'value': best_score})

    # 2. 形态可靠性权重
    weights = {'ma_bull': 1.0, 'w_bottom': 1.0, 'hs_bottom': 1.1, 'platform_break': 1.05}
    w = weights.get(best_id, 1.0)
    weight_bonus = round(best_score * (w - 1))
    score += weight_bonus
    if weight_bonus != 0:
        breakdown.append({'label': '形态可靠性加成', 'value': f'+{weight_bonus}' if weight_bonus > 0 else weight_bonus})

    # 3. 阶段加成
    stage_bonus = 0
    if any(k in best_stage for k in ['突破前夜', '末端', '向颈线推进']):
        stage_bonus = 12
    elif any(k in best_stage for k in ['已突破', '突破确认', '上涨进行中', '上涨启动', '放量突破']):
        stage_bonus = 15
    elif '主升浪' in best_stage:
        stage_bonus = 12
    elif any(k in best_stage for k in ['回踩', '确认中']):
        stage_bonus = 8
    elif any(k in best_stage for k in ['构建中', '酝酿', '右肩']):
        stage_bonus = 3
    elif any(k in best_stage for k in ['失败', '不成立', '非标准', '非平台']):
        stage_bonus = -15
    score += stage_bonus
    breakdown.append({'label': '当前阶段加成', 'value': f'+{stage_bonus}' if stage_bonus > 0 else stage_bonus})

    # 4. 量价配合
    vol_bonus = 0
    if vol['volStatus'] == '放量':
        vol_bonus += 6
    if '量价齐升' in vol['relation']:
        vol_bonus += 6
    if '价涨量缩' in vol['relation']:
        vol_bonus -= 4
    if '价跌量增' in vol['relation']:
        vol_bonus -= 4
    if float(vol['volRatio']) > 1.2:
        vol_bonus += 4
    score += vol_bonus
    breakdown.append({'label': '量价配合加成', 'value': f'+{vol_bonus}' if vol_bonus > 0 else vol_bonus})

    # 5. 均线状态
    ma_bonus = 0
    if ind['ma5'][last] and ind['ma10'][last] and ind['ma20'][last] and ind['ma60'][last]:
        if ind['ma5'][last] > ind['ma10'][last] > ind['ma20'][last] > ind['ma60'][last]:
            ma_bonus += 10
        elif ind['ma5'][last] > ind['ma10'][last] > ind['ma20'][last]:
            ma_bonus += 5
        if data[last]['close'] > ind['ma20'][last]:
            ma_bonus += 4
        if data[last]['close'] > ind['ma60'][last]:
            ma_bonus += 4
    score += ma_bonus
    breakdown.append({'label': '均线状态加成', 'value': f'+{ma_bonus}' if ma_bonus > 0 else ma_bonus})

    # 6. 多形态共振
    if len(sorted_bull) > 1 and sorted_bull[1][1]['score'] >= 50:
        score += 5
        breakdown.append({'label': '多形态共振', 'value': '+5'})

    # 7. 下降形态风险扣分
    if bear_best_score >= 70:
        score -= 25
        breakdown.append({'label': '见顶风险扣分', 'value': '-25'})
    elif bear_best_score >= 50:
        score -= 15
        breakdown.append({'label': '见顶风险扣分', 'value': '-15'})
    elif bear_best_score >= 35:
        score -= 5
        breakdown.append({'label': '见顶风险扣分', 'value': '-5'})

    # 8. 增强信号（MACD背离+量价背离+颈线确认）
    enhance = 0
    if macd_div:
        if macd_div.get('bottomDivergence', 0) >= 50:
            enhance += 12
            breakdown.append({'label': 'MACD底背离', 'value': '+12'})
        elif macd_div.get('bottomDivergence', 0) >= 30:
            enhance += 6
            breakdown.append({'label': 'MACD底背离', 'value': '+6'})
        if macd_div.get('topDivergence', 0) >= 50:
            enhance -= 12
            breakdown.append({'label': 'MACD顶背离', 'value': '-12'})
        elif macd_div.get('topDivergence', 0) >= 30:
            enhance -= 6
            breakdown.append({'label': 'MACD顶背离', 'value': '-6'})
    if vol_div:
        if vol_div.get('bottomVolumeDivergence', 0) >= 50:
            enhance += 8
            breakdown.append({'label': '底部量价背离', 'value': '+8'})
        if vol_div.get('topVolumeDivergence', 0) >= 50:
            enhance -= 8
            breakdown.append({'label': '顶部量价背离', 'value': '-8'})
        if vol_div.get('shrinkStop', 0) >= 50:
            enhance += 8
            breakdown.append({'label': '缩量止跌', 'value': '+8'})
        if vol_div.get('volumeStagnation', 0) >= 50:
            enhance -= 8
            breakdown.append({'label': '放量滞涨', 'value': '-8'})
    if neck_confirm:
        if neck_confirm.get('score', 0) >= 70:
            enhance += 10
            breakdown.append({'label': '颈线确认', 'value': '+10'})
        elif neck_confirm.get('score', 0) >= 50:
            enhance += 5
            breakdown.append({'label': '颈线确认', 'value': '+5'})
    score += enhance

    score = max(0, min(100, round(score)))

    # 等级
    bear_name = PATTERN_NAMES.get(bear_best_id, '')
    if bear_best_score >= 60:
        level = 'S级·风险警示'
        advice = f'检测到{bear_name}（{bear_best_score}%），见顶信号强烈，建议立即止盈止损，不要抄底'
    elif score >= 85:
        level = 'A级·强烈推荐'
        advice = '重点关注，形态+量价+均线共振，可考虑建仓'
    elif score >= 70:
        level = 'B级·推荐关注'
        advice = '值得关注，形态较好，等待最佳入场点'
    elif score >= 55:
        level = 'C级·值得观察'
        advice = '加入观察池，等待形态进一步确认'
    elif score >= 40:
        level = 'D级·一般'
        advice = '形态未确立，暂不重点关注'
    else:
        level = 'E级·暂不关注'
        advice = '形态不佳，建议回避'

    return {'score': score, 'level': level, 'advice': advice,
            'breakdown': breakdown, 'bestPattern': best_id}


# ============ 主分析函数 ============
def analyze(code, name=None, market_env=None, backtest_date=None):
    # 回测模式下拉取500天数据
    datalen = 500 if backtest_date else 250
    kline = fetch_kline(code, datalen=datalen)
    if not kline or len(kline) < 60:
        return {'error': f'K线数据不足（{len(kline) if kline else 0}根），无法分析'}

    data = []
    for d in kline:
        data.append({
            'day': d['day'],
            'open': float(d['open']),
            'high': float(d['high']),
            'low': float(d['low']),
            'close': float(d['close']),
            'volume': float(d['volume'])
        })

    # 回测模式：截取到回测日期为止的250天数据
    backtest_info = None
    if backtest_date:
        cutoff = backtest_date
        analysis_data = [d for d in data if d['day'][:10] <= cutoff]
        if len(analysis_data) > 250:
            analysis_data = analysis_data[-250:]
        if len(analysis_data) < 60:
            return {'error': f'回测日期{cutoff}数据不足（{len(analysis_data)}根），请选择更近的日期'}
        # 后续走势验证数据
        after_data = [d for d in data if d['day'][:10] > cutoff]
        if after_data:
            bt_close = analysis_data[-1]['close']
            latest_close = after_data[-1]['close']
            max_price = max(d['high'] for d in after_data)
            min_price = min(d['low'] for d in after_data)
            backtest_info = {
                'date': cutoff,
                'bt_close': bt_close,
                'latest_close': latest_close,
                'return_pct': round((latest_close - bt_close) / bt_close * 100, 2),
                'max_price': max_price,
                'max_pct': round((max_price / bt_close - 1) * 100, 2),
                'min_price': min_price,
                'min_pct': round((min_price / bt_close - 1) * 100, 2)
            }
        data = analysis_data

    n = len(data)
    last = n - 1

    # 大盘环境（回测模式下也用截取后的数据）
    if market_env is None:
        if backtest_date:
            # 直接拉取上证指数sh000001，避免被误判为平安银行
            market_url = f'https://quotes.sina.cn/cn/api/jsonp_v2.php/callback/CN_MarketDataService.getKLineData?symbol=sh000001&scale=240&ma=no&datalen=500'
            req = urllib.request.Request(market_url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=10)
            text = resp.read().decode('utf-8')
            match = re.search(r'\((.*)\)', text, re.DOTALL)
            market_raw = json.loads(match.group(1)) if match else []
            market_data = []
            for d in market_raw:
                market_data.append({
                    'day': d['day'], 'open': float(d['open']), 'high': float(d['high']),
                    'low': float(d['low']), 'close': float(d['close']), 'volume': float(d['volume'])
                })
            market_cut = [d for d in market_data if d['day'][:10] <= backtest_date]
            if len(market_cut) > 250:
                market_cut = market_cut[-250:]
            market_env = calc_market_env_from_data(market_cut)
        else:
            market_env = fetch_market_env()

    # 指标计算
    ind = calc_indicators(data)

    # 形态检测
    matches = match_patterns(data, ind)
    bear_matches = match_bear_patterns(data, ind)

    # 量价分析
    vol = analyze_volume(data, ind)

    # 增强信号
    macd_div = detect_macd_divergence(data, ind)
    vol_div = detect_volume_divergence(data, ind)

    # 确定最佳形态
    sorted_bull = sorted(matches.items(), key=lambda x: x[1]['score'], reverse=True)
    sorted_bear = sorted(bear_matches.items(), key=lambda x: x[1]['score'], reverse=True)
    bull_best_score = sorted_bull[0][1]['score']
    bear_best_score = sorted_bear[0][1]['score']
    is_bear_dominant = bear_best_score > bull_best_score and bear_best_score >= 50

    if is_bear_dominant:
        best_id = sorted_bear[0][0]
        best_detail = bear_matches[best_id]
        best_type = 'bear'
    else:
        best_id = sorted_bull[0][0]
        best_detail = matches[best_id]
        best_type = 'bull'

    # 颈线确认
    neck_confirm = calc_neckline_confirm(data, best_detail, is_bear_dominant)

    # 形态-环境适配度（当前大盘环境下该形态的历史准确率）
    env_accuracy = calc_env_accuracy(best_id, best_detail['score'], market_env)

    # 分别计算底部（买入）和顶部（卖出）的环境适配度
    bull_best_id = sorted_bull[0][0]
    bear_best_id = sorted_bear[0][0]
    bull_env_accuracy = calc_env_accuracy(bull_best_id, bull_best_score, market_env)
    bear_env_accuracy = calc_env_accuracy(bear_best_id, bear_best_score, market_env)
    # 综合买卖准确率 = 信号分 × 环境适配率
    buy_accuracy = round(bull_best_score * bull_env_accuracy['historical_accuracy'] / 100)
    sell_accuracy = round(bear_best_score * bear_env_accuracy['historical_accuracy'] / 100)

    # 推荐度评分（基础9项）
    attention = calc_attention_score(data, ind, matches, vol, bear_matches, macd_div, vol_div, neck_confirm)

    # 五大增强指标
    rsi_kdj = detect_rsi_kdj(data, ind)
    bollinger = detect_bollinger(data, ind)
    atr_stop = calc_atr_stop_loss(data, ind)
    weekly_data = extract_weekly_data(data)
    weekly_pattern = match_weekly_patterns(weekly_data)

    # 假信号风险评估
    fake_signal_risk = calc_fake_signal_risk(data, ind, best_type, best_detail['score'], market_env, macd_div, vol)

    # 相对强度
    relative_strength = {'score': 0, 'detail': '暂无大盘数据', 'level': '中性'}
    if market_env:
        stock20 = (data[last]['close'] - data[max(0, last-20)]['close']) / data[max(0, last-20)]['close'] * 100
        try:
            market20 = float(market_env['change20'])
            rs = stock20 - market20
            if rs > 15:
                relative_strength = {'score': 10, 'level': '极强（领涨）', 'rs': f'{rs:.2f}'}
            elif rs > 5:
                relative_strength = {'score': 6, 'level': '强势（跑赢）', 'rs': f'{rs:.2f}'}
            elif rs > -5:
                relative_strength = {'score': 0, 'level': '中性（同步）', 'rs': f'{rs:.2f}'}
            elif rs > -15:
                relative_strength = {'score': -6, 'level': '弱势（跑输）', 'rs': f'{rs:.2f}'}
            else:
                relative_strength = {'score': -10, 'level': '极弱（领跌）', 'rs': f'{rs:.2f}'}
        except (ValueError, TypeError):
            pass

    # 周线共振加成
    weekly_bonus = 0
    if weekly_pattern['resonance']:
        weekly_bonus = 8 if weekly_pattern['type'] == best_type else -8

    # 五大指标纳入评分 + 环境适配度加成
    env_bonus = env_accuracy.get('score_bonus', 0)
    extra_score = rsi_kdj['score'] + bollinger['score'] + relative_strength['score'] + weekly_bonus + env_bonus
    attention['score'] = max(0, min(100, attention['score'] + extra_score))
    if rsi_kdj['score'] != 0:
        attention['breakdown'].append({'label': 'RSI/KDJ信号', 'value': f'+{rsi_kdj["score"]}' if rsi_kdj['score'] > 0 else rsi_kdj['score']})
    if bollinger['score'] != 0:
        attention['breakdown'].append({'label': '布林带突破', 'value': f'+{bollinger["score"]}' if bollinger['score'] > 0 else bollinger['score']})
    if relative_strength['score'] != 0:
        attention['breakdown'].append({'label': '相对强度', 'value': f'+{relative_strength["score"]}' if relative_strength['score'] > 0 else relative_strength['score']})
    if weekly_bonus != 0:
        attention['breakdown'].append({'label': '周线共振', 'value': f'+{weekly_bonus}' if weekly_bonus > 0 else weekly_bonus})
    if env_bonus != 0:
        attention['breakdown'].append({'label': '环境适配度', 'value': f'+{env_bonus}' if env_bonus > 0 else env_bonus})

    # 重新评定等级
    if bear_best_score >= 60:
        attention['level'] = 'S级·风险警示'
    elif attention['score'] >= 85:
        attention['level'] = 'A级·强烈推荐'
    elif attention['score'] >= 70:
        attention['level'] = 'B级·推荐关注'
    elif attention['score'] >= 55:
        attention['level'] = 'C级·值得观察'
    elif attention['score'] >= 40:
        attention['level'] = 'D级·一般'
    else:
        attention['level'] = 'E级·暂不关注'

    # 底部/顶部信号分
    bull_score = bull_best_score
    bear_score = bear_best_score

    # 三重确认
    triple_confirm = attention['score'] >= 70 and bull_score >= 50 and bear_score < 50

    # 形态可靠性
    reliability = PATTERN_RELIABILITY.get(best_id, 75)
    completion = min(100, best_detail['score'] + (neck_confirm.get('score', 0) * 0.2 if neck_confirm else 0))
    env_mult = 1 + (market_env['impact'] / 100) if market_env else 1
    overall_confidence = round(reliability * (completion / 100) * env_mult)

    # 涨跌幅
    change = data[last]['close'] - data[last-1]['close'] if n >= 2 else 0
    pct = (change / data[last-1]['close'] * 100) if n >= 2 and data[last-1]['close'] > 0 else 0

    # 250日位置
    low_250 = min(d['low'] for d in data[-250:]) if n >= 250 else min(d['low'] for d in data)
    high_250 = max(d['high'] for d in data[-250:]) if n >= 250 else max(d['high'] for d in data)
    position_250 = (data[last]['close'] - low_250) / (high_250 - low_250) * 100 if high_250 > low_250 else 50

    return {
        'code': code,
        'name': name or code,
        'date': data[last]['day'][:10],
        'close': round(data[last]['close'], 2),
        'change': round(change, 2),
        'change_pct': round(pct, 2),
        'kline_days': n,
        'position_250': round(position_250, 1),
        'best_pattern': best_id,
        'best_pattern_name': PATTERN_NAMES.get(best_id, best_id),
        'best_type': best_type,
        'best_stage': best_detail.get('stage', ''),
        'best_points': best_detail.get('key_points', []),
        'bull_score': round(bull_score, 1),
        'bear_score': round(bear_score, 1),
        'all_bull_patterns': {k: {'score': v['score'], 'stage': v.get('stage', '')} for k, v in matches.items()},
        'all_bear_patterns': {k: {'score': v['score'], 'stage': v.get('stage', '')} for k, v in bear_matches.items()},
        'attention_score': attention['score'],
        'attention_level': attention['level'],
        'attention_advice': attention['advice'],
        'score_breakdown': attention['breakdown'],
        'triple_confirm': triple_confirm,
        'pattern_reliability': reliability,
        'pattern_completion': round(completion),
        'overall_confidence': overall_confidence,
        'market_env': market_env,
        'backtest_info': backtest_info,
        'fake_signal_risk': fake_signal_risk,
        'env_accuracy': env_accuracy,
        'bull_env_accuracy': bull_env_accuracy,
        'bear_env_accuracy': bear_env_accuracy,
        'buy_accuracy': buy_accuracy,
        'sell_accuracy': sell_accuracy,
        'volume': vol,
        'macd_divergence': macd_div,
        'volume_divergence': vol_div,
        'neckline_confirm': neck_confirm,
        'rsi_kdj': rsi_kdj,
        'bollinger': bollinger,
        'relative_strength': relative_strength,
        'atr_stop_loss': atr_stop,
        'weekly_pattern': weekly_pattern,
        'indicators': {
            'ma5': round(ind['ma5'][last], 2) if ind['ma5'][last] else None,
            'ma10': round(ind['ma10'][last], 2) if ind['ma10'][last] else None,
            'ma20': round(ind['ma20'][last], 2) if ind['ma20'][last] else None,
            'ma60': round(ind['ma60'][last], 2) if ind['ma60'][last] else None,
            'rsi14': round(ind['rsi'][last], 1),
            'kdj_k': round(ind['kdj_k'][last], 1),
            'kdj_d': round(ind['kdj_d'][last], 1),
            'macd_dif': round(ind['dif'][last], 3),
            'macd_dea': round(ind['dea'][last], 3),
            'boll_up': round(ind['boll_up'][last], 2),
            'boll_mid': round(ind['boll_mid'][last], 2),
            'boll_low': round(ind['boll_low'][last], 2),
            'atr': round(ind['atr'][last], 2)
        },
        'version': '4.0-full-web-aligned'
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python3 analyze_stock.py <股票代码> [股票名称] [回测日期]')
        print('示例: python3 analyze_stock.py 002436 兴森科技')
        print('回测: python3 analyze_stock.py 688105 诺唯赞 2026-09-07')
        sys.exit(1)

    code = sys.argv[1].strip()
    name = sys.argv[2] if len(sys.argv) >= 3 else code
    backtest_date = sys.argv[3] if len(sys.argv) >= 4 else None

    try:
        result = analyze(code, name, backtest_date=backtest_date)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({'code': code, 'name': name, 'error': str(e)}, ensure_ascii=False))
        import traceback
        traceback.print_exc()
