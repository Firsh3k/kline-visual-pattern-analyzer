"""
FIR-31 V6 — Python FastAPI Analysis Service
Python Canonical Core 作为唯一算法 Source of Truth
被 Node.js Express BFF (App Builder) 通过 HTTP API 调用
"""
import sys
import os
import json
import urllib.request
import ssl
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import (
    SwingPivotEngine,
    WBottomDetector,
    MTopDetector,
    HSBottomDetector,
    HSTopDetector,
)
from core.market_data import SinaDataProvider

app = FastAPI(
    title="K线形态分析 - Python Analysis Service",
    description="V6 Python Canonical Core Service",
    version="6.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化组件
provider = SinaDataProvider()
pivot_engine = SwingPivotEngine(left_bars=5, right_bars=5)
detectors = {
    "w_bottom": WBottomDetector(),
    "m_top": MTopDetector(),
    "hs_bottom": HSBottomDetector(),
    "hs_top": HSTopDetector(),
}

# 自选股存储
WATCHLIST_FILE = Path(__file__).parent / "watchlist.json"
GITHUB_CONFIG = {
    "repo": "Firsh3k/kline-visual-pattern-analyzer",
    "path": "config/watchlist.json",
    "branch": "main",
}


def klines_to_dict(klines):
    result = []
    for k in klines:
        if hasattr(k, 'to_dict'):
            result.append(k.to_dict())
        else:
            result.append({
                'date': str(getattr(k, 'date', getattr(k, 'day', ''))),
                'open': getattr(k, 'open', 0),
                'high': getattr(k, 'high', 0),
                'low': getattr(k, 'low', 0),
                'close': getattr(k, 'close', 0),
                'volume': getattr(k, 'volume', 0),
            })
    return result


def pattern_to_dict(p):
    if hasattr(p, 'to_dict'):
        d = p.to_dict()
    else:
        d = {
            'pattern_name': getattr(p, 'pattern_name', ''),
            'score': getattr(p, 'score', 0),
            'stage': getattr(p, 'stage', ''),
            'direction': getattr(p, 'direction', ''),
            'neckline_price': getattr(p, 'neckline_price', 0),
            'target_price': getattr(p, 'target_price', 0),
            'stop_loss': getattr(p, 'stop_loss', 0),
            'notes': getattr(p, 'notes', ''),
            'key_points': [],
        }
    if 'key_points' in d and d['key_points']:
        kps = []
        for kp in d['key_points']:
            if isinstance(kp, dict):
                kps.append(kp)
            elif hasattr(kp, 'to_dict'):
                kps.append(kp.to_dict())
            else:
                kps.append({'name': getattr(kp, 'name', ''), 'price': getattr(kp, 'price', 0), 'date': str(getattr(kp, 'date', ''))})
        d['key_points'] = kps
    return d


def calc_attention(patterns):
    bull_patterns = {k: v for k, v in patterns.items() if k in ('w_bottom', 'hs_bottom') and v}
    bear_patterns = {k: v for k, v in patterns.items() if k in ('m_top', 'hs_top') and v}
    bull_score = max((v[0]['score'] for v in bull_patterns.values()), default=0)
    bear_score = max((v[0]['score'] for v in bear_patterns.values()), default=0)

    if bear_score >= 60 and bear_score > bull_score:
        score, level, advice = max(0, 100 - bear_score), 'S级·风险警示', '见顶信号强烈，建议止盈止损'
    elif bull_score >= 80:
        score, level, advice = min(100, bull_score + 10), 'A级·强烈推荐', '形态明确，可重点关注'
    elif bull_score >= 60:
        score, level, advice = bull_score, 'B级·推荐关注', '形态较好，值得跟踪'
    elif bull_score >= 40:
        score, level, advice = bull_score, 'C级·值得观察', '形态初现，继续观察'
    else:
        score, level, advice = max(bull_score, 20), 'D级·一般', '无明显形态，观望为主'

    best_bull = max(bull_patterns.items(), key=lambda x: x[1][0]['score'], default=None)
    best_bear = max(bear_patterns.items(), key=lambda x: x[1][0]['score'], default=None)
    return {
        'score': score, 'level': level, 'advice': advice,
        'bull_score': bull_score, 'bear_score': bear_score,
        'best_bull': {'id': best_bull[0], 'name': best_bull[1][0]['pattern_name'], 'score': best_bull[1][0]['score'], 'stage': best_bull[1][0]['stage']} if best_bull else None,
        'best_bear': {'id': best_bear[0], 'name': best_bear[1][0]['pattern_name'], 'score': best_bear[1][0]['score'], 'stage': best_bear[1][0]['stage']} if best_bear else None,
    }


def analyze_stock(code: str, days: int = 370) -> Dict[str, Any]:
    stock_data = provider.get_stock_data(code, days=days)
    klines = stock_data.klines
    if len(klines) < 30:
        raise HTTPException(status_code=400, detail=f"K线数据不足，仅{len(klines)}根")
    pivots = pivot_engine.detect(klines)
    patterns = {}
    for pattern_id, detector in detectors.items():
        results = detector.detect(klines, pivots)
        patterns[pattern_id] = [pattern_to_dict(p) for p in results]
    attention = calc_attention(patterns)
    return {
        'code': code, 'name': getattr(stock_data, 'name', code),
        'data_length': len(klines),
        'latest_date': str(klines[-1].date) if hasattr(klines[-1], 'date') else '',
        'latest_close': klines[-1].close,
        'klines': klines_to_dict(klines),
        'patterns': patterns, 'attention': attention,
        'engine': 'python-canonical-core', 'core_version': '5.2.0',
        'analysis_time': datetime.now().isoformat(),
    }


# ============ API 端点 ============

@app.get("/health")
async def health():
    return {'status': 'ok', 'service': 'python-analysis-service', 'version': '6.0.0', 'engine': 'python-canonical-core', 'time': datetime.now().isoformat()}


@app.get("/analyze/{code}")
async def analyze(code: str, days: int = Query(370, ge=30, le=500)):
    try:
        return analyze_stock(code, days)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")


@app.get("/market-env")
async def market_env():
    try:
        from core.market_data.sina_provider import _SINA_URL, _http_get, _parse_jsonp
        from core.types import OHLCV
        url = _SINA_URL.format(market='sh', code='000001', datalen=250)
        raw_text = _http_get(url)
        records = _parse_jsonp(raw_text)
        if not records:
            raise HTTPException(status_code=400, detail="大盘数据获取失败")
        klines = [OHLCV(date=r.get('day',''), open=float(r.get('open',0)), high=float(r.get('high',0)), low=float(r.get('low',0)), close=float(r.get('close',0)), volume=float(r.get('volume',0))) for r in records[-250:]]
        closes = [k.close for k in klines]
        cur = closes[-1]
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60
        change20 = (cur - closes[-21]) / closes[-21] * 100
        above_ma20 = cur > ma20
        above_ma60 = cur > ma60
        ma_bull = ma20 > ma60
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        volatility = (sum(r*r for r in returns[-20:]) / 20) ** 0.5 * 100
        score = 0
        if above_ma20: score += 25
        if above_ma60: score += 25
        if ma_bull: score += 20
        if change20 > 5: score += 15
        elif change20 > 0: score += 10
        if volatility < 2: score += 15
        if score >= 70: stage, trend, advice = '强势上涨', 'bull', '大盘强势，可积极做多'
        elif score >= 50: stage, trend, advice = '震荡偏强', 'neutral_bull', '大盘偏强，精选个股'
        elif score >= 30: stage, trend, advice = '震荡偏弱', 'neutral_bear', '大盘偏弱，控制仓位'
        else: stage, trend, advice = '弱势下跌', 'bear', '大盘弱势，建议空仓或轻仓'
        return {'index': '上证指数', 'score': score, 'stage': stage, 'trend': trend, 'advice': advice,
                'current_price': round(cur, 2), 'ma20': round(ma20, 2), 'ma60': round(ma60, 2),
                'change20_pct': round(change20, 2), 'volatility': round(volatility, 2),
                'above_ma20': above_ma20, 'above_ma60': above_ma60, 'ma_bull': ma_bull,
                'data_length': len(klines), 'engine': 'python-canonical-core'}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"大盘环境分析失败: {str(e)}")


# 自选股
def load_watchlist():
    try:
        with open(WATCHLIST_FILE, 'r') as f:
            return json.load(f).get('stocks', [])
    except:
        return []

def save_watchlist(stocks):
    with open(WATCHLIST_FILE, 'w') as f:
        json.dump({'stocks': stocks}, f, ensure_ascii=False, indent=2)


@app.get("/watchlist")
async def get_watchlist():
    return {'stocks': load_watchlist()}


@app.post("/watchlist/{code}")
async def add_watchlist(code: str, name: Optional[str] = None):
    stocks = load_watchlist()
    if any(s['code'] == code for s in stocks):
        return {'status': 'exists', 'stocks': stocks}
    stocks.append({'code': code, 'name': name or code})
    save_watchlist(stocks)
    return {'status': 'added', 'stocks': stocks}


@app.delete("/watchlist/{code}")
async def remove_watchlist(code: str):
    stocks = [s for s in load_watchlist() if s['code'] != code]
    save_watchlist(stocks)
    return {'status': 'removed', 'stocks': stocks}


@app.post("/watchlist/analyze")
async def analyze_watchlist():
    stocks = load_watchlist()
    if not stocks:
        return {'results': [], 'message': '自选股池为空'}
    results = []
    for s in stocks:
        try:
            result = analyze_stock(s['code'])
            results.append({'code': s['code'], 'name': s.get('name', s['code']),
                           'latest_close': result['latest_close'], 'latest_date': result['latest_date'],
                           'attention': result['attention']})
        except Exception as e:
            results.append({'code': s['code'], 'name': s.get('name', s['code']), 'error': str(e)})
    results.sort(key=lambda x: x.get('attention', {}).get('score', 0), reverse=True)
    return {'results': results, 'total': len(results)}


# 市场扫描
class ScanRequest(BaseModel):
    codes: List[str] = []
    min_score: int = 60
    only_bull: bool = True


@app.post("/scan")
async def market_scan(req: ScanRequest):
    results = []
    for code in req.codes:
        try:
            result = analyze_stock(code)
            attention = result['attention']
            if req.only_bull and attention['bear_score'] > attention['bull_score']:
                continue
            if attention['score'] >= req.min_score:
                results.append({'code': code, 'name': result['name'],
                               'latest_close': result['latest_close'], 'attention': attention})
        except:
            continue
    results.sort(key=lambda x: x['attention']['score'], reverse=True)
    return {'results': results, 'total': len(results), 'scanned': len(req.codes)}


# 同花顺动态板块（内置板块列表）
THS_SECTORS = {
    '885955': {'name': '重组蛋白', 'codes': []},
    '886015': {'name': '创新药', 'codes': []},
    '885927': {'name': 'CRO概念', 'codes': []},
    '885769': {'name': '细胞免疫治疗', 'codes': []},
    '886068': {'name': 'AI视频', 'codes': []},
    '886020': {'name': 'PET铜箔', 'codes': []},
    '886112': {'name': 'MLCC概念', 'codes': []},
    '885959': {'name': 'PCB概念', 'codes': []},
}

@app.get("/sectors")
async def list_sectors():
    return {'sectors': [{'code': k, 'name': v['name']} for k, v in THS_SECTORS.items()]}


@app.post("/sector/{sector_code}/scan")
async def scan_sector(sector_code: str, min_score: int = 60):
    if sector_code not in THS_SECTORS:
        raise HTTPException(status_code=404, detail=f"板块 {sector_code} 不存在")
    sector = THS_SECTORS[sector_code]
    if not sector['codes']:
        return {'sector': sector_code, 'name': sector['name'], 'results': [], 'message': '板块成分股待配置'}
    results = []
    for code in sector['codes']:
        try:
            result = analyze_stock(code)
            if result['attention']['score'] >= min_score:
                results.append({'code': code, 'attention': result['attention']})
        except:
            continue
    return {'sector': sector_code, 'name': sector['name'], 'results': results, 'total': len(results)}


# 多设备同步（GitHub config）
@app.get("/sync/config")
async def get_sync_config():
    return {'github': GITHUB_CONFIG, 'local_watchlist': load_watchlist()}


@app.post("/sync/pull")
async def sync_pull():
    """从GitHub拉取自选股配置"""
    try:
        url = f"https://api.github.com/repos/{GITHUB_CONFIG['repo']}/contents/{GITHUB_CONFIG['path']}?ref={GITHUB_CONFIG['branch']}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            content = json.loads(__import__('base64').b64decode(data['content']).decode())
            remote_stocks = content.get('stocks', [])
            local_stocks = load_watchlist()
            merged = {s['code']: s for s in local_stocks}
            for s in remote_stocks:
                merged[s['code']] = s
            save_watchlist(list(merged.values()))
            return {'status': 'success', 'stocks': list(merged.values()), 'remote_count': len(remote_stocks)}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8001)
