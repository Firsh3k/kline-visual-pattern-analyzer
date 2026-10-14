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
import json
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from core import AdjustmentType, FrequencyType  # noqa: E402
from core import analysis as A  # noqa: E402  所有入口共用的单一分析实现（FIR-38）

SCHEMA_VERSION = A.ANALYSIS_SCHEMA_VERSION
WINDOW = A.WINDOW           # 分析窗口：最近 250 根日K（所有计算使用同一窗口）
FETCH_CALENDAR_DAYS = 420   # 约 250+ 个交易日
CN_TZ = A.CN_TZ
PATTERN_ORDER = A.PATTERN_ORDER
INDICATOR_SERIES = A.INDICATOR_SERIES
run_core = A.run_core
to_klines = A.to_klines


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


def _num(v: str) -> float:
    """CSV 数值；空串 / 非数字 → NaN（交给 core.analysis 校验拒绝，不在这里静默修补）"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def load_csv(path: Path) -> List[Dict[str, Any]]:
    """原样读取（不排序、不去重）：乱序 / 重复 / 非法值由 core.analysis.validate_bars 拒绝"""
    bars = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            bars.append({
                "day": _norm_date(row["date"]),
                "open": _num(row["open"]),
                "high": _num(row["high"]),
                "low": _num(row["low"]),
                "close": _num(row["close"]),
                "volume": _num(row["volume"]),
            })
    return bars


_symbol = A.exchange_symbol


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


# ------------------------------------------------------------------
# 计算与输出（全部委托给 core.analysis，与 API / 扫描同源）
# ------------------------------------------------------------------

def _sha256(obj: Any) -> str:
    return A.sha256_json(obj)


def analyze_one(code: str, name: str, loaded: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    prov = loaded["provenance"]
    return A.analyze(code, loaded["bars"], adjustment=prov.get("adjustment", "unknown"),
                     provenance=prov, name=name, now=now)


def summarize(doc: Dict[str, Any]) -> Dict[str, Any]:
    return A.pattern_summary(doc)


def _previous_doc(path: Path) -> Optional[Dict[str, Any]]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if doc.get("schema_version") == SCHEMA_VERSION else None
    except Exception:
        return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watchlist", type=Path, help="自选股 JSON（含 watchlist 数组），在线模式使用")
    ap.add_argument("--codes", nargs="*", help="直接指定股票代码，覆盖 watchlist")
    ap.add_argument("--csv-dir", type=Path, help="离线模式：从此目录读取 <code>.csv")
    ap.add_argument("--index-csv", type=Path, help="离线模式：上证指数 CSV（用于大盘环境）")
    ap.add_argument("--out", type=Path, required=True, help="输出目录")
    ap.add_argument("--adjustment", choices=["qfq", "none"], default="qfq")
    ap.add_argument("--expect-date", help="期望的最新交易日 YYYY-MM-DD；最后一根K线早于此日期的股票标记为 stale"
                                         "（不指定时由权威交易日历推导；日历未覆盖则为 null）")
    ap.add_argument("--now", help="计算收盘状态 / 期望交易日所用的当前时间（ISO8601，默认系统时间，北京时间）")
    ap.add_argument("--provenance-json", type=Path, help="离线模式：数据来源说明 JSON（{代码: {...}}）")
    args = ap.parse_args(argv)

    adjustment = AdjustmentType.FORWARD if args.adjustment == "qfq" else AdjustmentType.NONE
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(CN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
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
        try:
            market = A.analyze_market(index_bars)
        except A.InvalidInputError as e:
            run_errors.append({"code": "sh000001", "error": f"大盘数据不合法: {e}", "issues": e.issues[:20]})
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
            doc = analyze_one(code, name, loaded, now)
            (stocks_dir / f"{code}.json").write_text(
                json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
            summary = summarize(doc)
            if args.expect_date:
                summary["stale"] = doc["as_of"] < args.expect_date
            summary["status"] = "ok"
            summaries.append(summary)
            print(f"[ok]   {code} {name}  {doc['as_of']}  bars={doc['input']['bar_count']}", file=sys.stderr)
        except Exception as e:
            err = {"code": code, "error": f"{type(e).__name__}: {e}"}
            if isinstance(e, A.InvalidInputError):
                err["issues"] = e.issues[:20]
            # 失败后旧结果：保留上次成功的文件，但在摘要中明确标记为“上次结果（本次失败）”，绝不冒充本次结果
            prev = _previous_doc(stocks_dir / f"{code}.json")
            if prev is not None:
                summary = summarize(prev)
                summary.update({"status": "stale_previous", "stale": True,
                                "previous_as_of": prev["as_of"], "error": err["error"]})
                summaries.append(summary)
                err["kept_previous_as_of"] = prev["as_of"]
            else:
                stale_file = stocks_dir / f"{code}.json"
                if stale_file.exists():
                    stale_file.unlink()   # 旧格式或损坏的文件不能留在输出里
            run_errors.append(err)
            print(f"[fail] {code}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    run_cal = A.calendar_info("sh000001", now.astimezone(CN_TZ).date().isoformat(), now)
    index = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "engine_commit": A.engine_commit(),
        "core_version": core.__version__,
        "mode": "offline_csv" if offline else "live",
        "computed_at": now.isoformat(timespec="seconds"),
        "expected_trade_date": args.expect_date or run_cal["expected_trade_date"],
        "expected_trade_date_source": "argument" if args.expect_date else "trading_calendar",
        "calendar": {"status": run_cal["status"], "source": run_cal["source"], "reason": run_cal.get("reason")},
        "market_env": market,
        "stocks": summaries,
        "errors": run_errors,
    }
    (args.out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成：{len(summaries)} 成功，{len([e for e in run_errors if e['code'] != 'sh000001'])} 失败 → {args.out}",
          file=sys.stderr)
    return 1 if codes and not any(x["status"] == "ok" for x in summaries) else 0


if __name__ == "__main__":
    sys.exit(main())
