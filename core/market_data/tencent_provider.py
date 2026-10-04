"""
TencentDataProvider — 腾讯财经数据源

API: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},{freq},,,{datalen},{adjustment}

支持复权: qfq=前复权, hfq=后复权, 空=不复权
返回 JSON，数据在 data[symbol]['qfq'] 或 data[symbol]['day'] 中
每条记录: [date, open, close, high, low, volume]  (注意 close 在 high/low 之前!)
"""

import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional

from ..interfaces import DataProvider, MarketDataError
from ..types import (
    MarketDataBar, MarketDataSeries, DataQualityReport,
    DataProvenance, DataQualityFlag, DataSourceType,
    AdjustmentType, FrequencyType, StockData, OHLCV,
)
from .trading_calendar import get_trading_days


_TENCENT_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    "?param={symbol},{freq},,,{datalen},{adjustment}"
)

_FREQ_MAP = {
    FrequencyType.DAILY: "day",
    FrequencyType.WEEKLY: "week",
    FrequencyType.MONTHLY: "month",
}

_ADJ_MAP = {
    AdjustmentType.NONE: "",
    AdjustmentType.FORWARD: "qfq",
    AdjustmentType.BACKWARD: "hfq",
}


def _http_get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; kline-pattern-analyzer/5.2)",
        "Referer": "https://gu.qq.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise MarketDataError(f"Tencent request failed: {e}") from e


class TencentDataProvider(DataProvider):
    """腾讯财经数据源（支持前复权/后复权）"""

    SOURCE = DataSourceType.TENCENT

    def fetch_klines(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        frequency: FrequencyType = FrequencyType.DAILY,
        adjustment: AdjustmentType = AdjustmentType.NONE,
    ) -> MarketDataSeries:
        freq_str = _FREQ_MAP.get(frequency)
        if freq_str is None:
            raise MarketDataError(f"Unsupported frequency: {frequency}")

        adj_str = _ADJ_MAP.get(adjustment, "")
        datalen = 1023
        if start_date:
            try:
                sd = datetime.strptime(start_date, "%Y-%m-%d")
                today = datetime.now()
                days_needed = (today - sd).days + 10
                datalen = min(1023, max(30, int(days_needed * 1.5)))
            except ValueError:
                pass

        url = _TENCENT_URL.format(symbol=symbol, freq=freq_str, datalen=datalen, adjustment=adj_str)
        raw_text = _http_get(url)
        raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        fetched_at = datetime.now(timezone.utc).isoformat()

        payload = json.loads(raw_text)
        if payload.get("code") != 0:
            raise MarketDataError(f"Tencent API error: {payload}")

        sym_data = payload.get("data", {}).get(symbol, {})
        # 复权数据在 qfqday/hfqday 字段，不复权在 day 字段
        # 腾讯 API 实际 key 命名: qfqday, hfqday, day, week, month
        rows = None
        if adj_str == "qfq" and "qfqday" in sym_data:
            rows = sym_data["qfqday"]
        elif adj_str == "hfq" and "hfqday" in sym_data:
            rows = sym_data["hfqday"]
        elif adj_str and adj_str + "day" in sym_data:
            rows = sym_data[adj_str + "day"]
        elif freq_str in sym_data:
            rows = sym_data[freq_str]
        elif adj_str in sym_data:
            rows = sym_data[adj_str]
        if rows is None:
            raise MarketDataError(f"No kline data for {symbol}, keys={list(sym_data.keys())}")

        bars: List[MarketDataBar] = []
        for row in rows:
            # [date, open, close, high, low, volume, ...]
            day = row[0]
            if start_date and day < start_date:
                continue
            if end_date and day > end_date:
                continue
            prov = DataProvenance(
                source=self.SOURCE,
                url=url,
                fetched_at=fetched_at,
                raw_payload_hash=raw_hash,
                adjustment_applied=adjustment,
                quality_flag=DataQualityFlag.ADJUSTED if adjustment != AdjustmentType.NONE else DataQualityFlag.OK,
            )
            bars.append(MarketDataBar(
                date=day,
                open=float(row[1]),
                close=float(row[2]),
                high=float(row[3]),
                low=float(row[4]),
                volume=float(row[5]),
                amount=None,
                provenance=prov,
            ))

        if not bars:
            raise MarketDataError(f"No bars after date filter for {symbol}")

        series = MarketDataSeries(
            symbol=symbol,
            name=symbol,
            frequency=frequency,
            adjustment=adjustment,
            trading_calendar="SSE",
            source=self.SOURCE,
            fetched_at=fetched_at,
            bars=bars,
        )
        series.quality_report = self.validate_data(series)
        return series

    def validate_data(self, series: MarketDataSeries) -> DataQualityReport:
        if not series.bars:
            return DataQualityReport(
                total_days=0, expected_trading_days=0, actual_bars=0,
                suspended_days=0, missing_days=0, completeness_ratio=0.0,
                adjustment_consistent=True,
                anomalies=[{"type": "empty", "msg": "No bars in series"}],
            )

        first_date = series.bars[0].date
        last_date = series.bars[-1].date
        expected = get_trading_days(first_date, last_date)
        actual_dates = {b.date for b in series.bars}
        missing = [d for d in expected if d not in actual_dates]

        anomalies = []
        for i in range(1, len(series.bars)):
            prev_close = series.bars[i - 1].close
            cur_close = series.bars[i].close
            if prev_close > 0:
                pct = abs(cur_close - prev_close) / prev_close
                if pct > 0.20:
                    anomalies.append({
                        "type": "price_jump",
                        "date": series.bars[i].date,
                        "pct": round(pct, 4),
                        "prev_close": prev_close,
                        "cur_close": cur_close,
                    })

        total_days = (datetime.strptime(last_date, "%Y-%m-%d") -
                      datetime.strptime(first_date, "%Y-%m-%d")).days + 1
        expected_count = len(expected)
        actual_count = len(series.bars)
        completeness = actual_count / expected_count if expected_count > 0 else 0.0

        return DataQualityReport(
            total_days=total_days,
            expected_trading_days=expected_count,
            actual_bars=actual_count,
            suspended_days=0,
            missing_days=len(missing),
            completeness_ratio=round(completeness, 4),
            adjustment_consistent=True,
            anomalies=anomalies,
            notes=f"missing={len(missing)} days",
        )

    def get_trading_calendar(self, exchange: str, start_date: str, end_date: str) -> List[str]:
        return get_trading_days(start_date, end_date)

    def get_stock_data(self, code: str, days: int = 250) -> StockData:
        series = self.fetch_klines(symbol=code, adjustment=AdjustmentType.NONE)
        bars = series.bars[-days:] if len(series.bars) > days else series.bars
        klines = [OHLCV(date=b.date, open=b.open, high=b.high, low=b.low,
                        close=b.close, volume=b.volume, amount=b.amount) for b in bars]
        return StockData(code=code, name=code, klines=klines, source="tencent")

    def get_index_data(self, code: str, days: int = 250) -> StockData:
        return self.get_stock_data(code, days)

    def get_sector_constituents(self, sector_code: str) -> List[str]:
        raise NotImplementedError("Tencent provider does not support sector constituents")
