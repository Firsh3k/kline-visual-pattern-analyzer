"""
经典技术指标 — 从 V4 (scripts/analyze_stock.py: calc_indicators / analyze_volume) 迁移

迁移原则：
- 公式、参数、初始值、运算顺序与 V4 完全一致，保证逐根K线数值相等
  （tests/v5/test_v4_migration_parity.py 以 V4 为对照逐根断言）。
- 输入改为 V5 标准 OHLCV 列表。

口径（FIR-37，PO 2026-10-14 决定：保留现有口径并注明，另给行情软件常用口径的对照值）：
- RSI(14) 使用最近 14 日涨跌的简单平均，不是 Wilder 平滑；前 14 根固定为 50。
- ATR(14) 前 14 根直接取当日 TR，第 15 根起才做 Wilder 平滑。
- BOLL(20,2) 使用总体标准差；前 19 根上中下轨均等于收盘价，带宽为 0。
- MACD / KDJ 从分析窗口第一根起算初值。
机器可读口径见 INDICATOR_CONVENTIONS；对照值（Wilder RSI / Wilder ATR / 样本标准差 BOLL）
由 ReferenceIndicators 计算，仅供对照显示，不参与任何形态、信号或评分。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .interfaces import IndicatorCalculator
from .registry import registry
from .types import OHLCV


def _ma(arr: List[float], period: int) -> List[Optional[float]]:
    result: List[Optional[float]] = [None] * len(arr)
    for i in range(period - 1, len(arr)):
        result[i] = sum(arr[i - period + 1:i + 1]) / period
    return result


@registry.indicator("classic")
class ClassicIndicators(IndicatorCalculator):
    """MA5/10/20/60、VMA5/20、MACD(12,26,9)、RSI(14)、KDJ(9,3,3)、BOLL(20,2)、ATR(14)"""

    def calculate(self, klines: List[OHLCV], **kwargs) -> Dict[str, Any]:
        n = len(klines)
        closes = [k.close for k in klines]
        highs = [k.high for k in klines]
        lows = [k.low for k in klines]
        volumes = [k.volume for k in klines]

        ma5 = _ma(closes, 5)
        ma10 = _ma(closes, 10)
        ma20 = _ma(closes, 20)
        ma60 = _ma(closes, 60)
        vma5 = _ma(volumes, 5)
        vma20 = _ma(volumes, 20)

        # MACD(12,26,9) —— 首根以收盘价为 EMA 初值
        dif = [0.0] * n
        dea = [0.0] * n
        macd_bar = [0.0] * n
        e12 = closes[0] if n else 0.0
        e26 = closes[0] if n else 0.0
        for i in range(n):
            e12 = e12 * 11/13 + closes[i] * 2/13 if i > 0 else closes[0]
            e26 = e26 * 25/27 + closes[i] * 2/27 if i > 0 else closes[0]
            dif[i] = e12 - e26
            dea[i] = dea[i-1] * 8/10 + dif[i] * 2/10 if i > 0 else dif[0]
            macd_bar[i] = (dif[i] - dea[i]) * 2

        # RSI(14) —— 简单平均
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

        # BOLL(20,2) —— 总体标准差
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
        if n:
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
            'boll_width': boll_width, 'atr': atr,
        }


# ------------------------------------------------------------------
# FIR-37：口径说明（机器可读）与对照值
# ------------------------------------------------------------------

INDICATOR_CONVENTIONS: Dict[str, Dict[str, str]] = {
    "ma": {"label": "MA5/10/20/60", "convention": "收盘价简单移动平均；不足周期为 null"},
    "macd": {"label": "MACD(12,26,9)", "convention": "EMA 以分析窗口第一根收盘价为初值；柱 = (DIF-DEA)×2"},
    "rsi": {"label": "RSI(14)", "convention": "最近 14 日涨跌幅简单平均（非 Wilder 平滑）；前 14 根为 50；无下跌时为 100",
            "reference": "rsi_wilder14"},
    "kdj": {"label": "KDJ(9,3,3)", "convention": "K、D 以 50 为初值，按 2/3、1/3 平滑；前 8 根为 50；最高=最低时 RSV=50"},
    "boll": {"label": "BOLL(20,2)", "convention": "中轨 MA20，上下轨 ±2×总体标准差（除以 20）；前 19 根三轨=收盘价",
             "reference": "boll_up_sample20 / boll_low_sample20"},
    "atr": {"label": "ATR(14)", "convention": "前 14 根取当日 TR，第 15 根起 Wilder 平滑 (ATR×13+TR)/14",
            "reference": "atr_wilder14"},
    "rsi_wilder14": {"label": "RSI(14) 对照：Wilder", "convention":
                     "第 14 根起：首值为前 14 个涨跌的简单平均，之后 (前值×13+当日)/14；无下跌时为 100，无涨跌时为 50；之前为 null"},
    "atr_wilder14": {"label": "ATR(14) 对照：Wilder", "convention":
                     "TR 从第 2 根起算（需前收盘）；第 14 根（索引 14）首值为第 1~14 根 TR 的简单平均，之后 (前值×13+TR)/14；之前为 null"},
    "boll_sample20": {"label": "BOLL(20,2) 对照：样本标准差", "convention":
                      "中轨 MA20，上下轨 ±2×样本标准差（除以 19）；前 19 根为 null"},
}

REFERENCE_SERIES = ("rsi_wilder14", "atr_wilder14", "boll_up_sample20", "boll_low_sample20")


class ReferenceIndicators:
    """FIR-37：行情软件常用口径的对照值。仅供展示对照，不参与形态 / 信号 / 评分。"""

    def calculate(self, klines: List[OHLCV]) -> Dict[str, List[Optional[float]]]:
        n = len(klines)
        closes = [k.close for k in klines]
        highs = [k.high for k in klines]
        lows = [k.low for k in klines]

        rsi: List[Optional[float]] = [None] * n
        if n > 14:
            ch = [closes[i] - closes[i - 1] for i in range(1, n)]
            g = sum(max(c, 0.0) for c in ch[:14]) / 14
            l_ = sum(max(-c, 0.0) for c in ch[:14]) / 14
            for i in range(14, n):
                if i > 14:
                    c = ch[i - 1]
                    g = (g * 13 + max(c, 0.0)) / 14
                    l_ = (l_ * 13 + max(-c, 0.0)) / 14
                rsi[i] = 50.0 if g == 0 and l_ == 0 else (100.0 if l_ == 0 else 100 - 100 / (1 + g / l_))

        atr: List[Optional[float]] = [None] * n
        if n > 14:
            tr = [None] + [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
                           for i in range(1, n)]
            a = sum(tr[1:15]) / 14
            atr[14] = a
            for i in range(15, n):
                a = (a * 13 + tr[i]) / 14
                atr[i] = a

        up: List[Optional[float]] = [None] * n
        low: List[Optional[float]] = [None] * n
        for i in range(19, n):
            w = closes[i - 19:i + 1]
            mid = sum(w) / 20
            sd = math.sqrt(sum((x - mid) ** 2 for x in w) / 19)
            up[i] = mid + 2 * sd
            low[i] = mid - 2 * sd

        return {"rsi_wilder14": rsi, "atr_wilder14": atr, "boll_up_sample20": up, "boll_low_sample20": low}


def volume_status(klines: List[OHLCV], ind: Dict[str, Any]) -> Dict[str, str]:
    """量价状态（V4 analyze_volume 原样迁移）"""
    n = len(klines)
    last = n - 1
    v5 = ind['vma5'][last] or 0
    v20 = ind['vma20'][last] or 0
    vol_ratio = v5 / v20 if v20 > 0 else 1

    today_vol = klines[last].volume
    if today_vol > v5 * 1.5:
        vol_status = '放量'
    elif today_vol < v5 * 0.7:
        vol_status = '缩量'
    else:
        vol_status = '正常'

    recent5 = klines[n-5:] if n >= 5 else klines
    price_up = recent5[-1].close > recent5[0].close
    prev_v5 = ind['vma5'][n-6] if n >= 6 else v5
    vol_up = v5 > prev_v5

    if price_up and vol_up:
        relation = '量价齐升（健康上涨）'
    elif price_up and not vol_up:
        relation = '价涨量缩（上涨乏力，警惕）'
    elif not price_up and vol_up:
        relation = '价跌量增（抛压较重）'
    else:
        relation = '价跌量缩（正常回调）'

    return {'volStatus': vol_status, 'relation': relation, 'volRatio': f'{vol_ratio:.2f}'}


def atr_stop(klines: List[OHLCV], ind: Dict[str, Any]) -> Dict[str, Any]:
    """2 倍 ATR 止损位（V4 calc_atr_stop_loss 原样迁移）"""
    last = len(klines) - 1
    atr = ind['atr'][last]
    cur = klines[last].close
    return {
        'atr': round(atr, 2),
        'atr_pct': round(atr / cur * 100, 2),
        'stop_loss': round(cur - 2 * atr, 2),
        'detail': f'ATR={atr:.2f}({atr/cur*100:.1f}%)，2倍ATR止损位={cur-2*atr:.2f}',
    }
