#!/usr/bin/env python3
"""
python_analyzer.py - K线形态分析本地执行器（App Builder 部署版）

架构：NestJS BFF 通过 child_process 调用本脚本，形态识别 100% 走 Python Canonical Core
（SwingPivotEngine + W/M/H&S 检测器 + 新浪数据源）。

⚠️ fail-closed 原则（v6/app-builder 硬约束）：
    真实 core 不可用或分析失败时，本脚本必须【非零退出 + 输出错误】，
    绝不允许静默返回随机/骨架算法的假形态。
    骨架算法仅可在显式 `--allow-skeleton`（本地开发/测试）时使用，生产路径禁止。

用法:
    python3 python_analyzer.py <stock_code> [--days N]
    python3 python_analyzer.py --mode health
    python3 python_analyzer.py <code> --allow-skeleton   # 仅本地开发，生产禁用

输出 (stdout, JSON)，失败时错误写入 stderr 并退出码 1。
"""
import argparse
import json
import sys
import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
_server_core = PROJECT_ROOT / "server" / "core"
if _server_core.is_dir():
    sys.path.insert(0, str(PROJECT_ROOT / "server"))


def _try_import_real_core():
    try:
        from core.swing_pivot import SwingPivotEngine
        from core.market_data.sina_provider import SinaDataProvider
        from core.registry import registry
        return SwingPivotEngine, SinaDataProvider, registry
    except Exception:
        return None, None, None


def _pattern_result_to_dict(pr) -> dict:
    return {
        "pattern_id": pr.pattern_id,
        "pattern_name": pr.pattern_name,
        "direction": pr.direction.value,
        "stage": pr.stage.value,
        "score": pr.score,
        "confidence": pr.confidence.value if pr.confidence else None,
        "neckline_price": pr.neckline_price,
        "breakout_price": pr.breakout_price,
        "target_price": pr.target_price,
        "stop_loss": pr.stop_loss,
        "key_points": [
            {"name": kp.name, "date": kp.date, "price": kp.price,
             "index": kp.index, "description": kp.description}
            for kp in pr.key_points
        ],
        "notes": pr.notes,
    }


def _analyze_with_real_core(code: str, days: int) -> dict:
    pivot_cls, provider_cls, registry_obj = _try_import_real_core()
    if not (pivot_cls and provider_cls and registry_obj):
        raise ImportError("真实 core 模块接口不完整（SwingPivotEngine/SinaDataProvider/registry）")

    provider = provider_cls()
    stock_data = provider.get_stock_data(code, days=days)
    klines = stock_data.klines
    if not klines:
        raise ValueError("未能获取K线数据（数据源返回空）")

    engine = pivot_cls(left_bars=5, right_bars=5)
    all_pivots = engine.detect(klines)
    confirmed_pivots = [p for p in all_pivots if p.confirmed]

    all_results = {}
    for pid in registry_obj.get_pattern_ids():
        detector = registry_obj.get_pattern(pid)
        matches = detector.detect(klines, confirmed_pivots)
        if matches:
            best = max(matches, key=lambda m: m.score)
            all_results[pid] = _pattern_result_to_dict(best)
        else:
            all_results[pid] = None

    patterns = {
        "w_bottom": _normalize_pattern_output(all_results.get("w_bottom")),
        "m_top": _normalize_pattern_output(all_results.get("m_top")),
        "hs_bottom": _normalize_pattern_output(all_results.get("hs_bottom")),
        "hs_top": _normalize_pattern_output(all_results.get("hs_top")),
    }

    attention = _calculate_attention(patterns, klines[-1].close)

    return {
        "code": stock_data.code,
        "name": stock_data.name or stock_data.code,
        "data_length": len(klines),
        "latest_date": klines[-1].date,
        "latest_close": float(klines[-1].close),
        "klines": [_kline_to_dict(k) for k in klines],
        "patterns": patterns,
        "attention": attention,
        "engine": {
            "analyzer": "real_core",
            "data_source": "sina",
            "pivot_count": len(confirmed_pivots),
            "pattern_count": sum(1 for p in patterns.values() if p.get("detected")),
        },
    }


def _kline_to_dict(k) -> dict:
    return {
        "date": k.date,
        "open": float(k.open), "high": float(k.high),
        "low": float(k.low), "close": float(k.close),
        "volume": float(k.volume),
        "amount": float(k.amount) if k.amount is not None else None,
    }


def _normalize_pattern_output(raw: dict | None) -> dict:
    if not raw or raw.get("score", 0) <= 0:
        return {"detected": False, "score": 0, "stage": "none", "direction": "none", "details": {}}
    details = {k: v for k, v in raw.items()
               if k not in ("detected", "score", "stage", "direction", "pattern_id")}
    return {
        "detected": True,
        "score": raw["score"],
        "stage": raw.get("stage", "unknown"),
        "direction": raw.get("direction", "unknown"),
        "details": details,
    }


# ===================== 骨架算法（仅本地开发，禁止进入生产结果） =====================

def _generate_mock_klines(code: str, days: int) -> list:
    import random
    random.seed(hash(code) & 0xFFFFFFFF)
    base_price = 50 + random.random() * 150
    klines = []
    price = base_price
    start_date = datetime.date.today() - datetime.timedelta(days=days)
    for i in range(days):
        date = start_date + datetime.timedelta(days=i)
        if date.weekday() >= 5:
            continue
        change = random.gauss(0, 0.02)
        open_price = price
        close_price = price * (1 + change)
        high_price = max(open_price, close_price) * (1 + abs(random.gauss(0, 0.01)))
        low_price = min(open_price, close_price) * (1 - abs(random.gauss(0, 0.01)))
        volume = random.randint(1000000, 5000000)
        klines.append({
            "date": date.strftime("%Y-%m-%d"),
            "open": round(open_price, 2), "high": round(high_price, 2),
            "low": round(low_price, 2), "close": round(close_price, 2),
            "volume": volume,
        })
        price = close_price
    return klines


def _skeleton_find_pivots(klines: list, left: int = 5, right: int = 5) -> list:
    n = len(klines)
    if n < left + right + 1: return []
    pivots = []
    for i in range(left, n - right):
        ch = float(klines[i]["high"])
        cl = float(klines[i]["low"])
        window = klines[i - left:i] + klines[i + 1:i + right + 1]
        highs = [float(k["high"]) for k in window]
        lows = [float(k["low"]) for k in window]
        if ch >= max(highs):
            pivots.append({"index": i, "price": ch, "type": "high", "date": klines[i]["date"]})
        if cl <= min(lows):
            pivots.append({"index": i, "price": cl, "type": "low", "date": klines[i]["date"]})
    return pivots


def _skeleton_detect_patterns(pivots: list) -> dict:
    base = {"detected": False, "score": 0, "stage": "none", "direction": "none", "details": {}}
    patterns = {"w_bottom": dict(base), "m_top": dict(base),
                "hs_bottom": dict(base), "hs_top": dict(base)}
    if len(pivots) < 4: return patterns
    highs = [p for p in pivots if p["type"] == "high"]
    lows = [p for p in pivots if p["type"] == "low"]
    if len(lows) >= 2:
        first, second = lows[0], lows[-1]
        diff_ratio = abs(first["price"] - second["price"]) / max(first["price"], 0.01)
        if diff_ratio < 0.03 and len(highs) >= 1:
            patterns["w_bottom"] = {"detected": True, "score": 75, "stage": "forming",
                "direction": "bull", "details": {"neckline_price": round(highs[0]["price"], 2)}}
    if len(highs) >= 2:
        first, second = highs[0], highs[-1]
        diff_ratio = abs(first["price"] - second["price"]) / max(first["price"], 0.01)
        if diff_ratio < 0.03 and len(lows) >= 1:
            patterns["m_top"] = {"detected": True, "score": 75, "stage": "forming",
                "direction": "bear", "details": {"neckline_price": round(lows[0]["price"], 2)}}
    return patterns


def _analyze_with_skeleton(code: str, days: int) -> dict:
    """⚠️ 仅本地开发。返回结果显式标记 analyzer=fallback_skeleton，生产路径不得使用。"""
    klines = _generate_mock_klines(code, days)
    pivots = _skeleton_find_pivots(klines)
    patterns = _skeleton_detect_patterns(pivots)
    last_close = float(klines[-1]["close"])
    attention = _calculate_attention(patterns, last_close)
    return {
        "code": code, "name": f"[骨架测试]{code}",
        "data_length": len(klines), "latest_date": klines[-1]["date"],
        "latest_close": last_close, "klines": klines,
        "patterns": patterns, "attention": attention,
        "engine": {"analyzer": "fallback_skeleton", "data_source": "mock",
                   "pivot_count": len(pivots),
                   "pattern_count": sum(1 for p in patterns.values() if p.get("detected"))},
    }


def _calculate_attention(patterns: dict, last_close: float) -> dict:
    bull_ids = ["w_bottom", "hs_bottom"]
    bear_ids = ["m_top", "hs_top"]
    bull_total = sum(patterns.get(pid, {}).get("score", 0) for pid in bull_ids
                     if patterns.get(pid, {}).get("detected"))
    bear_total = sum(patterns.get(pid, {}).get("score", 0) for pid in bear_ids
                     if patterns.get(pid, {}).get("detected"))
    net = 50 + (bull_total * 0.1) - (bear_total * 0.1)
    score = int(max(0, min(100, round(net))))
    if score >= 80:
        level, advice = "strong_bull", "强烈看涨，可考虑积极建仓或加仓"
    elif score >= 65:
        level, advice = "bull", "偏多，可关注回调买入机会"
    elif score >= 45:
        level, advice = "neutral", "多空平衡，建议观望或轻仓试探"
    elif score >= 25:
        level, advice = "bear", "偏空，谨慎操作，注意控制仓位"
    else:
        level, advice = "strong_bear", "强烈看空，建议减仓或规避风险"
    return {"score": score, "level": level, "advice": advice,
            "bull_score": bull_total, "bear_score": bear_total}


def analyze_stock(code: str, days: int = 250, allow_skeleton: bool = False) -> dict:
    """
    生产入口：优先真实 core。
    - allow_skeleton=False（默认/生产）：core 失败直接抛错（fail-closed），由 NestJS 转成明确错误，
      前端走红色错误面板，绝不静默降级。
    - allow_skeleton=True（仅本地开发）：允许回退骨架，结果带 fallback_skeleton 标记。
    """
    try:
        return _analyze_with_real_core(code, days)
    except Exception as e:
        if allow_skeleton:
            print(f"[python_analyzer] 真实core不可用({e})，--allow-skeleton 下使用骨架（禁止用于生产）",
                  file=sys.stderr)
            return _analyze_with_skeleton(code, days)
        # fail-closed：包装错误向上抛，绝不返回假数据
        raise RuntimeError(f"Python Canonical Core 分析失败（fail-closed，未降级）：{e}") from e


def health_check() -> dict:
    pivot_cls, provider_cls, registry_obj = _try_import_real_core()
    has_real = all([pivot_cls, provider_cls, registry_obj])
    return {
        "status": "ok" if has_real else "degraded",
        "python_version": sys.version.split()[0],
        "core_installed": has_real,
        "core_pivot_engine": pivot_cls is not None,
        "core_data_provider": provider_cls is not None,
        "core_pattern_detector": registry_obj is not None,
        "analyzer": "real_core" if has_real else "fallback_skeleton",
        "fail_closed": True,
    }


def main():
    parser = argparse.ArgumentParser(description="K线形态分析 - 本地 Python 执行器")
    parser.add_argument("code", nargs="?", help="股票代码")
    parser.add_argument("--days", type=int, default=250, help="分析K线天数")
    parser.add_argument("--mode", default="analyze", choices=["analyze", "health"])
    parser.add_argument("--allow-skeleton", action="store_true",
                        help="仅本地开发：允许core失败时回退骨架（生产禁止）")
    args = parser.parse_args()

    if args.mode == "health":
        print(json.dumps(health_check(), ensure_ascii=False))
        return 0

    if not args.code:
        print(json.dumps({"error": "缺少股票代码参数"}, ensure_ascii=False), file=sys.stderr)
        return 1

    try:
        result = analyze_stock(args.code, args.days, allow_skeleton=args.allow_skeleton)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({"code": args.code, "error": str(e), "fail_closed": True},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
