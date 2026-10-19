"""
大盘环境 — 从 V4 (scripts/analyze_stock.py: calc_market_env_from_data / extract_weekly_data /
extract_monthly_data) 迁移

迁移原则：评分规则、阈值、周/月线聚合方式与 V4 完全一致；输出用 V5 MarketRegime 数据类，
V4 原有的全部字段原样放在 details 中（键名不变）。

与 V4 唯一的区别：V4 周线聚合在日期无法解析时退回 hash(日期)%52（每次运行结果可能不同）；
这里改为直接报错 —— V5 的 OHLCV.date 契约要求 YYYY-MM-DD，非法日期本就不应进入计算。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from .indicators import ClassicIndicators
from .interfaces import MarketRegimeAnalyzer
from .types import OHLCV, MarketRegime, TrendDirection


def weekly_bars(klines: List[OHLCV]) -> List[Dict[str, Any]]:
    """按 ISO 周号聚合周线：相邻K线周号变化即开始新周（V4 extract_weekly_data）"""
    weekly: List[Dict[str, Any]] = []
    current_week = None
    for k in klines:
        week = datetime.strptime(k.date[:10], '%Y-%m-%d').isocalendar()[1]
        if current_week is None or week != current_week:
            current_week = week
            weekly.append({'date': k.date, 'open': k.open, 'high': k.high,
                           'low': k.low, 'close': k.close, 'volume': k.volume})
        else:
            weekly[-1]['high'] = max(weekly[-1]['high'], k.high)
            weekly[-1]['low'] = min(weekly[-1]['low'], k.low)
            weekly[-1]['close'] = k.close
            weekly[-1]['volume'] += k.volume
    return weekly


def monthly_bars(klines: List[OHLCV]) -> List[Dict[str, Any]]:
    """每 20 个交易日为一“月”，从最新一根往前切（V4 extract_monthly_data）"""
    monthly: List[Dict[str, Any]] = []
    n = len(klines)
    for i in range(n - 1, 19, -20):
        start_idx = max(0, i - 19)
        month = klines[start_idx:i + 1]
        monthly.insert(0, {
            'date': month[-1].date,
            'open': month[0].open,
            'high': max(k.high for k in month),
            'low': min(k.low for k in month),
            'close': month[-1].close,
            'volume': sum(k.volume for k in month),
        })
    return monthly


_TREND = {'bull': TrendDirection.BULL, 'bear': TrendDirection.BEAR, 'neutral': TrendDirection.NEUTRAL}


class ClassicMarketRegimeAnalyzer(MarketRegimeAnalyzer):
    """上证指数等指数日K → 大盘环境评分（0-100）与阶段"""

    def analyze(self, index_klines: List[OHLCV]) -> Optional[MarketRegime]:
        details = self.analyze_dict(index_klines)
        if details is None:
            return None
        return MarketRegime(
            regime=details['stage'],
            score=details['score'],
            trend=_TREND[details['trend']],
            stage=details['stage'],
            advice=details['advice'],
            impact_on_stock=details['impact'],
            details=details,
        )

    def analyze_dict(self, data: List[OHLCV]) -> Optional[Dict[str, Any]]:
        if not data or len(data) < 60:
            return None
        n = len(data)
        last = n - 1
        ind = ClassicIndicators().calculate(data)
        cur_price = data[last].close
        ma20 = ind['ma20'][last] or cur_price
        ma60 = ind['ma60'][last] or cur_price
        price20ago = data[max(0, last - 20)].close
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
        high20 = max(k.high for k in recent20)
        low20 = min(k.low for k in recent20)
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

        change5 = ((cur_price - data[max(0, last-5)].close) / data[max(0, last-5)].close * 100) if n >= 6 else 0
        change60 = ((cur_price - data[max(0, last-60)].close) / data[max(0, last-60)].close * 100) if n >= 61 else 0
        change120 = ((cur_price - data[max(0, last-120)].close) / data[max(0, last-120)].close * 100) if n >= 121 else 0
        change250 = ((cur_price - data[0].close) / data[0].close * 100) if n >= 250 else 0

        weekly = weekly_bars(data)
        weekly_trend = '数据不足'; weekly_change4 = 0
        if len(weekly) >= 10:
            w_last = len(weekly) - 1
            w_ma5 = sum(d['close'] for d in weekly[w_last-4:w_last+1]) / 5
            w_ma10 = sum(d['close'] for d in weekly[w_last-9:w_last+1]) / 10
            weekly_change4 = (weekly[w_last]['close'] - weekly[w_last-4]['close']) / weekly[w_last-4]['close'] * 100
            if w_ma5 > w_ma10 and weekly[w_last]['close'] > w_ma5: weekly_trend = '周线多头'
            elif w_ma5 < w_ma10 and weekly[w_last]['close'] < w_ma5: weekly_trend = '周线空头'
            else: weekly_trend = '周线震荡'

        monthly = monthly_bars(data)
        monthly_trend = '数据不足'; monthly_change3 = 0
        if len(monthly) >= 6:
            m_last = len(monthly) - 1
            m_ma3 = sum(d['close'] for d in monthly[m_last-2:m_last+1]) / 3
            m_ma6 = sum(d['close'] for d in monthly[m_last-5:m_last+1]) / 6
            monthly_change3 = (monthly[m_last]['close'] - monthly[m_last-3]['close']) / monthly[m_last-3]['close'] * 100
            if m_ma3 > m_ma6 and monthly[m_last]['close'] > m_ma3: monthly_trend = '月线多头'
            elif m_ma3 < m_ma6 and monthly[m_last]['close'] < m_ma3: monthly_trend = '月线空头'
            else: monthly_trend = '月线震荡'

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
            'lastDate': data[last].date[:10],
        }
