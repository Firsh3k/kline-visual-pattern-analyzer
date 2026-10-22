"""
FIR-31 P0 Web Recovery — FastAPI 后端
Python Canonical Core 作为唯一算法 Source of Truth
前端只通过 API 获取分析结果，不包含任何形态检测算法
"""
import sys
import os
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).parent.parent
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
    title="K线形态分析工具",
    description="Python Canonical Core + FastAPI + Mobile Web",
    version="6.0.0-p0",
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

PATTERN_NAMES = {
    "w_bottom": "W底",
    "m_top": "M头",
    "hs_bottom": "头肩底",
    "hs_top": "头肩顶",
}


def klines_to_dict(klines):
    """将 OHLCV 对象转换为字典列表"""
    result = []
    for k in klines:
        if hasattr(k, 'to_dict'):
            result.append(k.to_dict())
        elif isinstance(k, dict):
            result.append(k)
        else:
            result.append({
                'date': getattr(k, 'date', getattr(k, 'day', '')),
                'open': getattr(k, 'open', 0),
                'high': getattr(k, 'high', 0),
                'low': getattr(k, 'low', 0),
                'close': getattr(k, 'close', 0),
                'volume': getattr(k, 'volume', 0),
            })
    return result


def pattern_to_dict(p):
    """将 PatternResult 转换为字典"""
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
    # 确保 key_points 格式统一
    if 'key_points' in d and d['key_points']:
        kps = []
        for kp in d['key_points']:
            if isinstance(kp, dict):
                kps.append(kp)
            elif hasattr(kp, 'to_dict'):
                kps.append(kp.to_dict())
            else:
                kps.append({
                    'name': getattr(kp, 'name', ''),
                    'price': getattr(kp, 'price', 0),
                    'date': getattr(kp, 'date', ''),
                })
        d['key_points'] = kps
    return d


def calc_attention(patterns: Dict[str, list]) -> Dict[str, Any]:
    """计算推荐度评分"""
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
        'score': score,
        'level': level,
        'advice': advice,
        'bull_score': bull_score,
        'bear_score': bear_score,
        'best_bull': {
            'id': best_bull[0],
            'name': best_bull[1][0]['pattern_name'],
            'score': best_bull[1][0]['score'],
            'stage': best_bull[1][0]['stage'],
        } if best_bull else None,
        'best_bear': {
            'id': best_bear[0],
            'name': best_bear[1][0]['pattern_name'],
            'score': best_bear[1][0]['score'],
            'stage': best_bear[1][0]['stage'],
        } if best_bear else None,
    }


def analyze_stock(code: str, days: int = 370) -> Dict[str, Any]:
    """执行完整的个股形态分析"""
    # 1. 获取数据（Python Canonical Core）
    stock_data = provider.get_stock_data(code, days=days)
    klines = stock_data.klines

    if len(klines) < 30:
        raise HTTPException(status_code=400, detail=f"K线数据不足，仅{len(klines)}根")

    # 2. Swing Pivot 检测
    pivots = pivot_engine.detect(klines)

    # 3. 形态检测（全部由 Python core 执行）
    patterns = {}
    for pattern_id, detector in detectors.items():
        results = detector.detect(klines, pivots)
        patterns[pattern_id] = [pattern_to_dict(p) for p in results]

    # 4. 推荐度评分
    attention = calc_attention(patterns)

    # 5. 统一输出
    return {
        'code': code,
        'name': getattr(stock_data, 'name', code),
        'data_length': len(klines),
        'latest_date': str(klines[-1].date) if hasattr(klines[-1], 'date') else '',
        'latest_close': klines[-1].close,
        'klines': klines_to_dict(klines),
        'patterns': patterns,
        'attention': attention,
        'engine': 'python-canonical-core',
        'core_version': '5.2.0',
        'analysis_time': datetime.now().isoformat(),
    }


# ============ API 端点 ============

@app.get("/api/health")
async def health():
    """健康检查"""
    return {
        'status': 'ok',
        'version': '6.0.0-p0',
        'architecture': 'FastAPI + Python Canonical Core',
        'engine': 'python-canonical-core',
        'core_version': '5.2.0',
        'time': datetime.now().isoformat(),
    }


@app.get("/api/analyze/{code}")
async def analyze(code: str, days: int = Query(370, ge=30, le=500)):
    """个股形态分析"""
    try:
        return analyze_stock(code, days)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")


@app.get("/api/market-env")
async def market_env():
    """大盘环境分析"""
    try:
        # 上证指数: sh000001
        # 注意: SinaDataProvider.fetch_klines 会 lstrip 前缀，导致 000001 被误判为平安银行
        # 这里直接用 provider.fetch_klines 并传入完整 symbol
        from core.market_data.sina_provider import _SINA_URL, _http_get, _parse_jsonp
        from core.types import MarketDataBar, OHLCV
        from datetime import datetime as dt

        url = _SINA_URL.format(market='sh', code='000001', datalen=250)
        raw_text = _http_get(url)
        records = _parse_jsonp(raw_text)
        if not records:
            raise HTTPException(status_code=400, detail="大盘数据获取失败")

        klines = []
        for rec in records[-250:]:
            klines.append(OHLCV(
                date=rec.get('day', ''),
                open=float(rec.get('open', 0)),
                high=float(rec.get('high', 0)),
                low=float(rec.get('low', 0)),
                close=float(rec.get('close', 0)),
                volume=float(rec.get('volume', 0)),
            ))

        if len(klines) < 60:
            raise HTTPException(status_code=400, detail="大盘数据不足")

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

        if score >= 70:
            stage, trend, advice = '强势上涨', 'bull', '大盘强势，可积极做多'
        elif score >= 50:
            stage, trend, advice = '震荡偏强', 'neutral_bull', '大盘偏强，精选个股'
        elif score >= 30:
            stage, trend, advice = '震荡偏弱', 'neutral_bear', '大盘偏弱，控制仓位'
        else:
            stage, trend, advice = '弱势下跌', 'bear', '大盘弱势，建议空仓或轻仓'

        return {
            'index': '上证指数',
            'score': score,
            'stage': stage,
            'trend': trend,
            'advice': advice,
            'current_price': round(cur, 2),
            'ma20': round(ma20, 2),
            'ma60': round(ma60, 2),
            'change20_pct': round(change20, 2),
            'volatility': round(volatility, 2),
            'above_ma20': above_ma20,
            'above_ma60': above_ma60,
            'ma_bull': ma_bull,
            'data_length': len(klines),
            'engine': 'python-canonical-core',
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"大盘环境分析失败: {str(e)}")


# 自选股池（本地JSON存储）
WATCHLIST_FILE = PROJECT_ROOT / 'web' / 'watchlist.json'

def load_watchlist():
    try:
        with open(WATCHLIST_FILE, 'r') as f:
            return json.load(f).get('stocks', [])
    except:
        return []

def save_watchlist(stocks):
    with open(WATCHLIST_FILE, 'w') as f:
        json.dump({'stocks': stocks}, f, ensure_ascii=False, indent=2)


@app.get("/api/watchlist")
async def get_watchlist():
    return {'stocks': load_watchlist()}


@app.post("/api/watchlist/{code}")
async def add_watchlist(code: str, name: Optional[str] = None):
    stocks = load_watchlist()
    if any(s['code'] == code for s in stocks):
        return {'status': 'exists', 'stocks': stocks}
    stocks.append({'code': code, 'name': name or code})
    save_watchlist(stocks)
    return {'status': 'added', 'stocks': stocks}


@app.delete("/api/watchlist/{code}")
async def remove_watchlist(code: str):
    stocks = [s for s in load_watchlist() if s['code'] != code]
    save_watchlist(stocks)
    return {'status': 'removed', 'stocks': stocks}


@app.post("/api/watchlist/analyze")
async def analyze_watchlist():
    stocks = load_watchlist()
    if not stocks:
        return {'results': [], 'message': '自选股池为空'}

    results = []
    for s in stocks:
        try:
            result = analyze_stock(s['code'])
            results.append({
                'code': s['code'],
                'name': s.get('name', s['code']),
                'latest_close': result['latest_close'],
                'latest_date': result['latest_date'],
                'attention': result['attention'],
            })
        except Exception as e:
            results.append({'code': s['code'], 'name': s.get('name', s['code']), 'error': str(e)})

    results.sort(key=lambda x: x.get('attention', {}).get('score', 0), reverse=True)
    return {'results': results, 'total': len(results)}


# 静态文件服务
static_dir = PROJECT_ROOT / 'web' / 'static'
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def index():
    """返回前端页面"""
    index_file = static_dir / 'index.html'
    if index_file.exists():
        return FileResponse(str(index_file))
    return JSONResponse({'message': 'K线形态分析工具 API', 'docs': '/docs'})


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
