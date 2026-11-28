#!/usr/bin/env python3
"""一次性生成美股固定行情 fixture（网络依赖，不进 CI）。

开发环境（云主机）东财全部主机不可达（2026-11-25 实测，生产出口可达性属部署后冒烟项），
故 fixture 用可达的腾讯美股前复权接口抓取，provenance 如实记录 source=tencent。
验收时把冻结行情注入美股路由路径（stub _fetch_series_for），
Core→API→页面 全链路确定性可重复，不依赖当天行情、不依赖东财可达性。

运行：python3 tests/v6/make_us_fixtures.py
"""
import csv
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

FIX = ROOT / "tests" / "v6" / "fixtures"
FIX.mkdir(parents=True, exist_ok=True)

# 交易所后缀（与线上单文件版 US_FULL_SYMBOLS 同口径）
SUFFIX = {"AAPL": "OQ", "NVDA": "OQ"}


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


for ticker in ("AAPL", "NVDA"):
    full = f"us{ticker}.{SUFFIX[ticker]}"
    raw = _get(f"https://ifzq.gtimg.cn/appstock/app/usfqkline/get?param={full},day,,,250,qfq")
    payload = json.loads(raw.decode("utf-8"))
    sd = payload["data"][full]
    rows = sd.get("qfqday") or sd.get("day")
    assert rows and len(rows) == 250, f"{ticker} 根数异常: {len(rows) if rows else 0}"
    # 腾讯行格式 [date, open, close, high, low, volume]
    bars = [{"date": r[0], "open": float(r[1]), "high": float(r[3]),
             "low": float(r[4]), "close": float(r[2]), "volume": float(r[5])} for r in rows]
    bars.sort(key=lambda b: b["date"])
    name_raw = _get(f"https://qt.gtimg.cn/q=us{ticker}")
    name = name_raw.decode("gbk", errors="replace").split("~")[1] or ticker
    lastday = bars[-1]["date"].replace("-", "")
    csv_path = FIX / f"us_{ticker.lower()}_qfq_{lastday}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"]])
    import hashlib
    prov = {
        "ticker": ticker,
        "symbol": f"us{ticker}",
        "name": name,
        "source": "tencent",
        "adjustment": "qfq",
        "trading_calendar": "NASDAQ",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "bar_count": len(bars),
        "first_day": bars[0]["date"],
        "last_day": bars[-1]["date"],
        "raw_payload_sha256": hashlib.sha256(raw).hexdigest(),
        "note": ("冻结行情 fixture：开发环境东财不可达，用腾讯美股前复权抓取；"
                 "验收注入美股路由路径，东财生产可达性属部署后冒烟项"),
    }
    prov_path = FIX / f"us_{ticker.lower()}_qfq_{lastday}_provenance.json"
    prov_path.write_text(json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{ticker}: {len(bars)}根 {bars[0]['date']}→{bars[-1]['date']} name={name} → {csv_path.name}")