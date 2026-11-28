#!/usr/bin/env python3
"""美股个股验收证据生成器（确定性，不访问网络）。

用冻结 fixture（tests/v6/fixtures/us_*_qfq_*.csv）+ 固定"当前时间"生成两层结果：
  core：直接 core.analysis.analyze（算法唯一来源）
  api ：python_analyzer.analyze_stock（BFF 实际 spawn 的执行器），
        仅 stub _fetch_series_for 注入冻结行情（开发环境东财不可达，见 fixture provenance），
        并冻结 core.analysis.datetime 使 session_state 不随运行日期变化。
输出 JSON 供 tests/v6/test_us_acceptance.js 做 Core↔API↔页面 逐字段对照。

运行：python3 tests/v6/us_acceptance_make_docs.py
"""
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "v6" / "app-builder"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(APP))

from core import analysis as A  # noqa: E402
from core.types import (AdjustmentType, DataProvenance, DataQualityFlag,  # noqa: E402
                        DataSourceType, FrequencyType, MarketDataBar, MarketDataSeries)

FIX = ROOT / "tests" / "v6" / "fixtures"
# 固定"当前时间"：CN 2026-11-26 12:00 = ET 2026-11-25 23:00（美股已收盘），
# fixture 最后一根 2026-11-25 → close_confirmed / stale=False，确定性不随运行日期变化。
FIXED_NOW = datetime(2026, 11, 26, 12, 0, tzinfo=A.CN_TZ)


def load_fixture(ticker):
    csvs = sorted(FIX.glob(f"us_{ticker.lower()}_qfq_*.csv"))
    csvs = [p for p in csvs if not p.name.endswith("_provenance.json")]
    assert csvs, f"缺少 {ticker} fixture，先运行 make_us_fixtures.py"
    path = csvs[-1]
    prov = json.loads(path.with_name(path.stem + "_provenance.json").read_text(encoding="utf-8"))
    bars = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            bars.append({"day": row["date"], "open": float(row["open"]), "high": float(row["high"]),
                         "low": float(row["low"]), "close": float(row["close"]), "volume": float(row["volume"])})
    return bars, prov


def fixture_series(ticker, bars, prov):
    """把冻结行情包装成 MarketDataSeries（provenance 如实记 tencent）。"""
    mdbars = [MarketDataBar(
        date=b["day"], open=b["open"], high=b["high"], low=b["low"],
        close=b["close"], volume=b["volume"],
        provenance=DataProvenance(
            source=DataSourceType.TENCENT, url="fixture", fetched_at=prov["fetched_at"],
            raw_payload_hash=prov["raw_payload_sha256"],
            adjustment_applied=AdjustmentType.FORWARD, quality_flag=DataQualityFlag.ADJUSTED),
    ) for b in bars]
    return MarketDataSeries(
        symbol=f"us{ticker}", name=prov["name"], frequency=FrequencyType.DAILY,
        adjustment=AdjustmentType.FORWARD, trading_calendar=prov["trading_calendar"],
        source=DataSourceType.TENCENT, fetched_at=prov["fetched_at"], bars=mdbars)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW if tz is None else FIXED_NOW.astimezone(tz)


def main():
    import python_analyzer as P

    out = {}
    real_fetch = P._fetch_series_for
    real_dt = A.datetime
    try:
        for ticker in ("AAPL", "NVDA"):
            bars, prov = load_fixture(ticker)
            core_doc = A.analyze(ticker, bars, adjustment="qfq",
                                 provenance={"source": prov["source"], "mode": "fixture",
                                             "adjustment": "qfq"},
                                 name=prov["name"], now=FIXED_NOW)
            series = fixture_series(ticker, bars, prov)
            P._fetch_series_for = lambda code, symbol, adjustment, as_of=None: series
            A.datetime = FrozenDatetime
            api_doc = P.analyze_stock(ticker)
            out[ticker] = {"core": core_doc, "api": api_doc, "fixture": prov}
        # A股对照标的（切换竞态测试用）：既有冻结 fixture，同一 Core
        cn_path = ROOT / "tests" / "v5" / "fixtures" / "002436_qfq_20261013.csv"
        cn_bars = []
        with open(cn_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cn_bars.append({"day": row["date"], "open": float(row["open"]), "high": float(row["high"]),
                                "low": float(row["low"]), "close": float(row["close"]), "volume": float(row["volume"])})
        out["002436"] = {"core": A.analyze("002436", cn_bars, adjustment="qfq",
                                           provenance={"source": "tencent", "mode": "fixture",
                                                       "adjustment": "qfq"}, now=FIXED_NOW)}
    finally:
        P._fetch_series_for = real_fetch
        A.datetime = real_dt
    json.dump(out, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()