"""
SinaDataProvider — 新浪财经数据源

API: https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_{market}{code}=/CN_MarketDataService.getKLineData
    ?symbol={market}{code}&scale=240&ma=no&datalen={datalen}

注意：新浪返回不复权数据，不支持复权参数。
若需复权，需结合 TencentDataProvider 的 hfq 数据反推因子。
"""

import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional

from ..interfaces import DataProvider, MarketDataError
from ..types import (
    MarketDataBar, MarketDataSeries, DataQualityReport,
    DataProvenance, DataQualityFlag, DataSourceType,
    AdjustmentType, FrequencyType, StockData, OHLCV,
)
from .trading_calendar import get_trading_days, is_trading_day


_SINA_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
    "var%20_{market}{code}=/CN_MarketDataService.getKLineData"
    "?symbol={market}{code}&scale=240&ma=no&datalen={datalen}"
)


def _market_prefix(code: str) -> str:
    """根据股票代码首位判断市场前缀"""
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith(("0", "3")):
        return "sz"
    elif code.startswith("8"):
        return "bj"
    return "sz"


def _http_get(url: str, timeout: int = 15) -> str:
    """HTTP GET 请求，返回响应文本"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; kline-pattern-analyzer/5.2)",
        "Referer": "https://finance.sina.com.cn/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise MarketDataError(f"Sina request failed: {e}") from e


def _parse_jsonp(text: str) -> list:
    """解析 JSONP 响应，提取括号内的 JSON 数组"""
    m = re.search(r"\((.*)\)", text, re.DOTALL)
    if not m:
        raise MarketDataError(f"Cannot parse JSONP response: {text[:200]}")
    return json.loads(m.group(1))


class SinaDataProvider(DataProvider):
    """新浪财经数据源（不复权）"""

    SOURCE = DataSourceType.SINA

    def fetch_klines(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        frequency: FrequencyType = FrequencyType.DAILY,
        adjustment: AdjustmentType = AdjustmentType.NONE,
    ) -> MarketDataSeries:
        # 提取纯数字代码
        code = symbol.lstrip("shzabjSHZABJ")
        market = _market_prefix(code)
        full_symbol = f"{market}{code}"

        # 新浪仅支持日线；其他频率暂不支持
        if frequency != FrequencyType.DAILY:
            raise MarketDataError(f"Sina provider only supports DAILY frequency, got {frequency}")

        # 新浪不支持复权
        if adjustment != AdjustmentType.NONE:
            raise NotImplementedError(
                "Sina API does not support adjustment. "
                "Use TencentDataProvider for qfq/hfq, or MultiSourceDataProvider."
            )

        # 计算 datalen
        datalen = 1023  # 最大可获取天数
        if start_date:
            try:
                sd = datetime.strptime(start_date, "%Y-%m-%d")
                today = datetime.now()
                days_needed = (today - sd).days + 10
                datalen = min(1023, max(30, int(days_needed * 1.5)))
            except ValueError:
                pass

        url = _SINA_URL.format(market=market, code=code, datalen=datalen)
        raw_text = _http_get(url)
        raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        fetched_at = datetime.now(timezone.utc).isoformat()

        records = _parse_jsonp(raw_text)
        if not records:
            raise MarketDataError(f"No data returned for {full_symbol}")

        bars: List[MarketDataBar] = []
        for rec in records:
            day = rec["day"]
            # 日期过滤
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
                quality_flag=DataQualityFlag.OK,
            )
            bars.append(MarketDataBar(
                date=day,
                open=float(rec["open"]),
                high=float(rec["high"]),
                low=float(rec["low"]),
                close=float(rec["close"]),
                volume=float(rec["volume"]),
                amount=None,
                provenance=prov,
            ))

        if not bars:
            raise MarketDataError(f"No bars after date filter for {full_symbol}")

        series = MarketDataSeries(
            symbol=full_symbol,
            name=full_symbol,
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
        """校验数据质量"""
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

        # 价格跳变检测 (>20%)
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
            adjustment_consistent=(series.adjustment != AdjustmentType.NONE or True),
            anomalies=anomalies,
            notes=f"missing={len(missing)} days",
        )

    def get_trading_calendar(self, exchange: str, start_date: str, end_date: str) -> List[str]:
        return get_trading_days(start_date, end_date)

    # ---- 兼容旧接口 ----

    def get_stock_data(self, code: str, days: int = 250) -> StockData:
        series = self.fetch_klines(symbol=code, adjustment=AdjustmentType.NONE)
        # 只取最近 days 根
        bars = series.bars[-days:] if len(series.bars) > days else series.bars
        klines = [OHLCV(date=b.date, open=b.open, high=b.high, low=b.low,
                        close=b.close, volume=b.volume, amount=b.amount) for b in bars]
        return StockData(code=code, name=code, klines=klines, source="sina")

    def get_index_data(self, code: str, days: int = 250) -> StockData:
        return self.get_stock_data(code, days)

    def get_sector_constituents(self, sector_code: str) -> List[str]:
        raise NotImplementedError("Sina provider does not support sector constituents")
