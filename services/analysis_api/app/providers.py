"""
行情提供者

- LiveProvider：core.market_data（qfq 只走腾讯，失败即报错，绝不静默回退新浪不复权；TLS 正常校验）
- FixtureProvider：测试 / 本地演示用，从目录读取 CSV + manifest（来源、复权方式如实声明）

两者只负责“取数 + 来源说明”。校验、窗口、形态计算全部在 core.analysis 中完成。
"""
from __future__ import annotations

import csv
import json
import ssl
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import analysis as A

from .errors import InvalidRequest, NotFound, UpstreamUnavailable

FETCH_CALENDAR_DAYS = 420     # 约 290 个交易日，足够 250 根窗口
INDEX_CODE = "000001"         # 上证指数（sh000001）


@dataclass
class Fetched:
    bars: List[Dict[str, Any]]
    provenance: Dict[str, Any]
    name: Optional[str] = None


class Provider:
    name = "base"

    def daily(self, code: str, adjustment: str) -> Fetched:  # pragma: no cover - interface
        raise NotImplementedError

    def index_daily(self) -> Fetched:  # pragma: no cover - interface
        raise NotImplementedError

    def names(self, codes: List[str]) -> Dict[str, str]:
        return {}

    def ready(self) -> Dict[str, Any]:
        return {"provider": self.name, "ok": True}


class LiveProvider(Provider):
    name = "live"

    def _series(self, symbol: str, adjustment: str):
        from core import AdjustmentType, FrequencyType
        from core.market_data import MultiSourceDataProvider
        adj = AdjustmentType.FORWARD if adjustment == "qfq" else AdjustmentType.NONE
        start = (datetime.now(A.CN_TZ) - timedelta(days=FETCH_CALENDAR_DAYS)).strftime("%Y-%m-%d")
        try:
            return MultiSourceDataProvider(cross_validate=False).fetch_klines(
                symbol, start_date=start, frequency=FrequencyType.DAILY, adjustment=adj)
        except Exception as e:  # 网络 / 上游错误 → fail-closed
            raise UpstreamUnavailable(f"行情源不可用：{symbol}（{adjustment}）", details={"upstream_error": str(e)[:300]})

    @staticmethod
    def _to_fetched(series, symbol: str, adjustment: str) -> Fetched:
        if not series.bars:
            raise NotFound(f"行情源没有返回 {symbol} 的 K 线（代码不存在或已退市？）")
        bars = [{"day": b.date, "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                 "volume": b.volume} for b in series.bars]
        return Fetched(bars, {
            "mode": "live", "source": series.source.value, "adjustment": series.adjustment.value,
            "exchange": symbol[:2].upper(), "fetched_at": series.fetched_at,
            "raw_payload_sha256": series.bars[-1].provenance.raw_payload_hash if series.bars[-1].provenance else None,
            "quality_report": series.quality_report.to_dict() if series.quality_report else None,
        })

    def daily(self, code: str, adjustment: str) -> Fetched:
        symbol = A.exchange_symbol(code)
        f = self._to_fetched(self._series(symbol, adjustment), symbol, adjustment)
        if f.provenance["adjustment"] != adjustment:
            raise UpstreamUnavailable(f"行情源返回的复权方式（{f.provenance['adjustment']}）与请求（{adjustment}）不一致，拒绝混用")
        return f

    def index_daily(self) -> Fetched:
        return self._to_fetched(self._series("sh000001", "none"), "sh000001", "none")

    def names(self, codes: List[str]) -> Dict[str, str]:
        """股票名称（仅展示用；失败返回空，不影响分析）。TLS 使用系统默认校验。"""
        out: Dict[str, str] = {}
        if not codes:
            return out
        try:
            q = ",".join(A.exchange_symbol(c) for c in codes)
            req = urllib.request.Request(f"https://qt.gtimg.cn/q={q}", headers={"User-Agent": "Mozilla/5.0"})
            text = urllib.request.urlopen(req, timeout=10, context=ssl.create_default_context()).read() \
                .decode("gbk", errors="replace")
            for line in text.split(";"):
                parts = line.split("~")
                if len(parts) > 2 and parts[2].isdigit():
                    out[parts[2]] = parts[1]
        except Exception:
            pass
        return out


class FixtureProvider(Provider):
    """目录结构：manifest.json + <code>.csv。manifest 声明每个文件的来源与复权方式。"""
    name = "fixture"

    def __init__(self, root: Path):
        self.root = Path(root)
        mf = self.root / "manifest.json"
        if not mf.exists():
            raise FileNotFoundError(f"fixture manifest 不存在：{mf}")
        self.manifest = json.loads(mf.read_text(encoding="utf-8"))

    def _load(self, code: str) -> Fetched:
        entry = self.manifest.get("series", {}).get(code)
        if entry is None:
            raise NotFound(f"fixture 中没有 {code} 的行情")
        path = self.root / entry["file"]
        bars = []
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                def num(v):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return float("nan")
                bars.append({"day": r["date"], "open": num(r["open"]), "high": num(r["high"]),
                             "low": num(r["low"]), "close": num(r["close"]), "volume": num(r["volume"])})
        prov = {"mode": "fixture", "source": entry["source"], "adjustment": entry["adjustment"],
                "exchange": entry.get("exchange"), "fetched_at": entry.get("fetched_at"),
                "file": entry["file"], "note": entry.get("note")}
        return Fetched(bars, prov, entry.get("name"))

    def daily(self, code: str, adjustment: str) -> Fetched:
        A.exchange_symbol(code)
        if self.manifest.get("unavailable", {}).get(code):
            raise UpstreamUnavailable(f"行情源不可用：{code}（fixture 模拟）")
        f = self._load(code)
        if f.provenance["adjustment"] != adjustment:
            raise InvalidRequest(f"{code} 只有 {f.provenance['adjustment']} 数据，不提供 {adjustment}（不混用复权方式）",
                                 code="adjustment_unavailable")
        return f

    def index_daily(self) -> Fetched:
        return self._load("sh000001")

    def names(self, codes: List[str]) -> Dict[str, str]:
        s = self.manifest.get("series", {})
        return {c: s[c]["name"] for c in codes if c in s and s[c].get("name")}


def make_provider(settings) -> Provider:
    if settings.provider == "fixture":
        return FixtureProvider(settings.fixture_dir)
    return LiveProvider()
