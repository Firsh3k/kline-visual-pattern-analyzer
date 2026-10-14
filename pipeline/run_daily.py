#!/usr/bin/env python3
"""
形态计算管线（Single Source of Truth）—— 只使用 V5 core

后台是唯一的计算方，前端只读取本脚本输出的 JSON 并渲染，不做任何形态判断。

一次运行做三件事：
  1. 读取每只股票的日K线（本地 CSV，或按 V5 数据策略在线拉取前复权数据）
  2. 用 V5 core 计算：
       - 8 种形态：W底 / M头 / 头肩底 / 头肩顶（摆动点结构识别）
                  均线多头 / 均线空头 / 平台突破 / 平台破位（由 V4 迁移，数值一致）
       - 摆动高低点、经典技术指标、量价状态、ATR 止损位
       - 大盘环境（上证指数）
  3. 输出：
       <out>/index.json              运行信息 + 每只股票摘要
       <out>/stocks/<code>.json      单只股票的完整结果 + 渲染用K线

V4 的综合“推荐度”评分不再计算（已确认其总分与等级可能互相矛盾，不具可靠含义）。

用法：
  # 本地 CSV（聊天中核验后的数据即走这条路径）
  python3 pipeline/run_daily.py --csv-dir <目录> --index-csv <上证CSV> --provenance-json <说明> --out site/data
  # 在线拉取（需要能访问腾讯行情接口）
  python3 pipeline/run_daily.py --watchlist config/watchlist.json --out site/data
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from core import (  # noqa: E402
    OHLCV, SwingPivotEngine, WBottomDetector, ClassicIndicators, ClassicMarketRegimeAnalyzer,
    volume_status, atr_stop, registry, AdjustmentType, FrequencyType,
    ReferenceIndicators, INDICATOR_CONVENTIONS,
)

SCHEMA_VERSION = "2.0"
WINDOW = 250               # 分析窗口：最近 250 根日K（所有计算使用同一窗口）
FETCH_CALENDAR_DAYS = 420  # 约 250+ 个交易日
CN_TZ = timezone(timedelta(hours=8))
PATTERN_ORDER = ["w_bottom", "m_top", "hs_bottom", "hs_top",
                 "ma_bull", "ma_bear", "platform_break", "platform_break_down"]
INDICATOR_SERIES = ("ma5", "ma10", "ma20", "ma60")  # 输出完整序列，供图表叠加


# ------------------------------------------------------------------
# 数据加载
# ------------------------------------------------------------------

def _norm_date(s: str) -> str:
    """统一为 YYYY-MM-DD；无法识别则抛错（不静默猜测）。"""
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"无法识别的日期格式: {s!r}")


def load_csv(path: Path) -> List[Dict[str, Any]]:
    bars = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            bars.append({
                "day": _norm_date(row["date"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            })
    bars.sort(key=lambda b: b["day"])
    return bars


def _symbol(code: str) -> str:
    from core.market_data.sina_provider import _market_prefix
    prefix = _market_prefix(code)
    if prefix is None:
        raise ValueError(f"无法识别交易所: {code}")
    return f"{prefix}{code}"


def _fetch_series(symbol: str, adjustment: AdjustmentType):
    from core.market_data import MultiSourceDataProvider
    provider = MultiSourceDataProvider(cross_validate=False)
    start = (datetime.now(CN_TZ) - timedelta(days=FETCH_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    return provider.fetch_klines(symbol, start_date=start, frequency=FrequencyType.DAILY, adjustment=adjustment)


def fetch_live(code: str, adjustment: AdjustmentType) -> Dict[str, Any]:
    """前复权只走腾讯，失败即报错（不静默回退到新浪不复权）。"""
    series = _fetch_series(_symbol(code), adjustment)
    bars = [{"day": b.date, "open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "volume": b.volume} for b in series.bars]
    return {"bars": bars, "provenance": {
        "mode": "live", "source": series.source.value, "adjustment": series.adjustment.value,
        "fetched_at": series.fetched_at,
        "raw_payload_sha256": series.bars[-1].provenance.raw_payload_hash if series.bars else "",
        "quality_report": series.quality_report.to_dict() if series.quality_report else None,
    }}


def fetch_index_live() -> List[Dict[str, Any]]:
    series = _fetch_series("sh000001", AdjustmentType.NONE)
    return [{"day": b.date, "open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "volume": b.volume} for b in series.bars]


def fetch_names_live(codes: List[str]) -> Dict[str, str]:
    import urllib.request
    names: Dict[str, str] = {}
    try:
        q = ",".join(_symbol(c) for c in codes)
        req = urllib.request.Request(f"https://qt.gtimg.cn/q={q}", headers={"User-Agent": "Mozilla/5.0"})
        text = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="replace")
        for line in text.split(";"):
            parts = line.split("~")
            if len(parts) > 2:
                names[parts[2]] = parts[1]
    except Exception:
        pass
    return names


def to_klines(bars: List[Dict[str, Any]]) -> List[OHLCV]:
    return [OHLCV(date=b["day"], open=b["open"], high=b["high"], low=b["low"],
                  close=b["close"], volume=b["volume"]) for b in bars]


# ------------------------------------------------------------------
# V5 core 计算
# ------------------------------------------------------------------

def run_core(klines: List[OHLCV]) -> Dict[str, Any]:
    left, right = WBottomDetector.left_bars, WBottomDetector.right_bars
    pivots = SwingPivotEngine(left_bars=left, right_bars=right).detect(klines)
    patterns = {pid: [r.to_dict() for r in registry.get_pattern(pid).detect(klines)]
                for pid in PATTERN_ORDER}
    ind = ClassicIndicators().calculate(klines)
    ref = ReferenceIndicators().calculate(klines)
    last = len(klines) - 1
    latest = {k: ind[k][last] for k in ind}
    return {
        "core_version": core.__version__,
        "pivot_params": {"left_bars": left, "right_bars": right},
        "pivots": [p.to_dict() for p in pivots],
        "patterns": patterns,
        "indicators": {
            "latest": latest,
            "series": {k: ind[k] for k in INDICATOR_SERIES},
            "volume": volume_status(klines, ind),
            "atr_stop": atr_stop(klines, ind),
            # FIR-37：口径说明 + 行情软件常用口径对照值（仅展示，不参与任何判定）
            "conventions": INDICATOR_CONVENTIONS,
            "reference": {"latest": {k: v[last] for k, v in ref.items()}, "series": ref},
        },
    }


# ------------------------------------------------------------------
# 输出
# ------------------------------------------------------------------

def _json_roundtrip(obj: Any) -> Any:
    return json.loads(json.dumps(obj, ensure_ascii=False))


def _engine_commit() -> str:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def _sha256(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def analyze_one(code: str, name: str, loaded: Dict[str, Any]) -> Dict[str, Any]:
    bars = loaded["bars"][-WINDOW:]
    if len(bars) < 60:
        raise ValueError(f"K线不足 60 根（实际 {len(bars)} 根），均线形态与指标无法计算")
    result = _json_roundtrip(run_core(to_klines(bars)))
    return {
        "schema_version": SCHEMA_VERSION,
        "code": code,
        "name": name,
        "as_of": bars[-1]["day"],
        "data": {**loaded["provenance"], "bar_count": len(bars),
                 "first_date": bars[0]["day"], "last_date": bars[-1]["day"]},
        "bars": [[b["day"], b["open"], b["high"], b["low"], b["close"], b["volume"]] for b in bars],
        "core": {**result, "result_sha256": _sha256(result)},
    }


def summarize(doc: Dict[str, Any]) -> Dict[str, Any]:
    bars = doc["bars"]
    last_idx = len(bars) - 1
    close, prev = bars[-1][4], bars[-2][4]
    latest = {}
    for pid, results in doc["core"]["patterns"].items():
        if results:
            r = max(results, key=lambda x: max(kp["index"] for kp in x["key_points"]))
            k = max(kp["index"] for kp in r["key_points"])
            latest[pid] = {"pattern_name": r["pattern_name"], "direction": r["direction"],
                           "category": r["category"],
                           "stage": r["stage"], "score": r["score"], "confidence": r["confidence"],
                           "last_key_date": bars[k][0], "bars_since": last_idx - k}
    return {
        "code": doc["code"], "name": doc["name"], "as_of": doc["as_of"],
        "close": close, "change": round(close - prev, 2), "change_pct": round((close - prev) / prev * 100, 2),
        "patterns_latest": latest,
        "data_source": doc["data"]["source"], "adjustment": doc["data"]["adjustment"],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watchlist", type=Path, help="自选股 JSON（含 watchlist 数组），在线模式使用")
    ap.add_argument("--codes", nargs="*", help="直接指定股票代码，覆盖 watchlist")
    ap.add_argument("--csv-dir", type=Path, help="离线模式：从此目录读取 <code>.csv")
    ap.add_argument("--index-csv", type=Path, help="离线模式：上证指数 CSV（用于大盘环境）")
    ap.add_argument("--out", type=Path, required=True, help="输出目录")
    ap.add_argument("--adjustment", choices=["qfq", "none"], default="qfq")
    ap.add_argument("--expect-date", help="期望的最新交易日 YYYY-MM-DD；最后一根K线早于此日期的股票标记为 stale")
    ap.add_argument("--calendar", default="verified", help="交易日历状态（verified/unverified）")
    ap.add_argument("--provenance-json", type=Path, help="离线模式：数据来源说明 JSON（{代码: {...}}）")
    args = ap.parse_args(argv)

    adjustment = AdjustmentType.FORWARD if args.adjustment == "qfq" else AdjustmentType.NONE
    offline = args.csv_dir is not None
    if args.codes:
        codes = args.codes
    elif offline:
        codes = sorted(p.stem for p in args.csv_dir.glob("*.csv"))
    elif args.watchlist:
        codes = json.loads(args.watchlist.read_text(encoding="utf-8"))["watchlist"]
    else:
        ap.error("需要 --watchlist、--codes 或 --csv-dir 之一")

    run_errors: List[Dict[str, str]] = []
    index_bars = None
    try:
        index_bars = (load_csv(args.index_csv) if args.index_csv else None) if offline else fetch_index_live()
    except Exception as e:
        run_errors.append({"code": "sh000001", "error": f"大盘数据获取失败: {e}"})
    market = None
    if index_bars:
        market = ClassicMarketRegimeAnalyzer().analyze_dict(to_klines(index_bars[-WINDOW:]))
    if market is None:
        run_errors.append({"code": "sh000001", "error": "无大盘数据，大盘环境不显示"})

    names = {} if offline else fetch_names_live(codes)
    extra_prov = json.loads(args.provenance_json.read_text(encoding="utf-8")) if args.provenance_json else {}
    names.update({c: p["name"] for c, p in extra_prov.items() if p.get("name")})

    stocks_dir = args.out / "stocks"
    stocks_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for code in codes:
        name = names.get(code, code)
        try:
            if offline:
                csv_path = (args.csv_dir / f"{code}.csv").resolve()
                loaded = {"bars": load_csv(csv_path),
                          "provenance": {"mode": "offline_csv", "source": "csv", "adjustment": "unknown",
                                         "file": str(csv_path.relative_to(ROOT))
                                         if csv_path.is_relative_to(ROOT) else csv_path.name}}
                if extra_prov.get(code):
                    loaded["provenance"].update(extra_prov[code])
            else:
                loaded = fetch_live(code, adjustment)
            doc = analyze_one(code, name, loaded)
            (stocks_dir / f"{code}.json").write_text(
                json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            summary = summarize(doc)
            summary["stale"] = bool(args.expect_date and doc["as_of"] < args.expect_date)
            summaries.append(summary)
            print(f"[ok]   {code} {name}  {doc['as_of']}  bars={doc['data']['bar_count']}", file=sys.stderr)
        except Exception as e:
            run_errors.append({"code": code, "error": f"{type(e).__name__}: {e}"})
            print(f"[fail] {code}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    index = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "engine_commit": _engine_commit(),
        "core_version": core.__version__,
        "mode": "offline_csv" if offline else "live",
        "expected_trade_date": args.expect_date,
        "calendar": args.calendar,
        "market_env": market,
        "stocks": summaries,
        "errors": run_errors,
    }
    (args.out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成：{len(summaries)} 成功，{len([e for e in run_errors if e['code'] != 'sh000001'])} 失败 → {args.out}",
          file=sys.stderr)
    return 1 if codes and not summaries else 0


if __name__ == "__main__":
    sys.exit(main())
