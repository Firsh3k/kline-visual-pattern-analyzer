"""
EastMoneyDataProvider — 东方财富美股行情数据源（FIR 美股阶段1）

API:
  https://{host}/api/qt/stock/kline/get?secid={market}.{ticker}
        &fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56
        &klt=101&fqt={fqt}&beg={YYYYMMDD}&end={YYYYMMDD}

口径：
  - klt=101 日线；fqt=0 不复权 / fqt=1 前复权 / fqt=2 后复权；
  - 美股 secid 由 core.market_data.market.eastmoney_secid 路由（105 纳斯达克 / 106 纽交所）；
  - 复权口径由请求参数 fqt 严格保证，不做"返回后再猜键"，失败即 fail-closed；
  - 周 / 月线先取带正确 fqt 的日线，再确定性聚合（与 TencentDataProvider 一致）；
  - 多子域轮询：主域在部分出口 IP 被限流时，数字子域作为传输层兜底。

返回每行 klines: "date,open,close,high,low,volume"（close 在 high/low 之前，与腾讯一致）。
失败（网络 / 空数据 / 复权不一致）一律抛 MarketDataError，绝不静默降级。
"""
from __future__ import annotations

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
from . import market as M
from . import us_calendar as uc
from .validation import normalize_bars, assess_series_quality
from .aggregation import aggregate_daily_bars

# 等价主机：数字子域在前（主域在部分出口 IP 被限流），最后主域兜底。
_HOSTS = [
    "48.push2his.eastmoney.com",
    "13.push2his.eastmoney.com",
    "7.push2his.eastmoney.com",
    "push2his.eastmoney.com",
]

_FQT_MAP = {
    AdjustmentType.NONE: 0,
    AdjustmentType.FORWARD: 1,
    AdjustmentType.BACKWARD: 2,
}

_KLT_MAP = {FrequencyType.DAILY: 101}


def _http_get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; kline-pattern-analyzer/5.2)",
        "Referer": "https://quote.eastmoney.com/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


class EastMoneyDataProvider(DataProvider):
    """东方财富美股数据源（前复权 / 多子域轮询）。"""

    SOURCE = DataSourceType.EASTMONEY

    # -------------------------------------------------- 公共入口
    def fetch_klines(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        frequency: FrequencyType = FrequencyType.DAILY,
        adjustment: AdjustmentType = AdjustmentType.NONE,
    ) -> MarketDataSeries:
        if not M.is_us_symbol(symbol):
            raise MarketDataError(
                f"EastMoneyDataProvider 仅服务美股 us 前缀 symbol，收到 {symbol!r}")
        if frequency in (FrequencyType.WEEKLY, FrequencyType.MONTHLY):
            daily = self._fetch_daily(symbol, start_date, end_date, adjustment)
            bars = aggregate_daily_bars(daily.bars, frequency)
            series = MarketDataSeries(
                symbol=symbol, name=symbol, frequency=frequency,
                adjustment=adjustment, trading_calendar=daily.trading_calendar,
                source=self.SOURCE, fetched_at=daily.fetched_at, bars=bars)
            series.quality_report = self.validate_data(series)
            return series
        return self._fetch_daily(symbol, start_date, end_date, adjustment)

    # -------------------------------------------------- 日线取数
    def _fetch_daily(
        self,
        symbol: str,
        start_date: Optional[str],
        end_date: Optional[str],
        adjustment: AdjustmentType,
    ) -> MarketDataSeries:
        ticker = symbol[2:]  # 去掉 "us" 前缀
        secid = M.eastmoney_secid(ticker)
        fqt = _FQT_MAP.get(adjustment)
        if fqt is None:
            raise MarketDataError(f"不支持的复权方式：{adjustment}")

        beg = (start_date.replace("-", "") if start_date else "19900101")
        end = (end_date.replace("-", "") if end_date
               else datetime.now(uc and timezone.utc).strftime("%Y%m%d"))

        last_err: Optional[Exception] = None
        raw_text: Optional[str] = None
        used_host: Optional[str] = None
        for host in _HOSTS:
            url = (f"https://{host}/api/qt/stock/kline/get?secid={secid}"
                   "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56"
                   f"&klt=101&fqt={fqt}&beg={beg}&end={end}")
            try:
                raw_text = _http_get(url)
                if raw_text.strip():
                    used_host = host
                    break
            except Exception as exc:  # noqa: BLE001 - 记录后尝试下一主机
                last_err = exc
        if raw_text is None or not raw_text.strip():
            raise MarketDataError(
                f"东方财富全部主机不可用（{symbol} {adjustment.value}）：{last_err}")

        raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        fetched_at = datetime.now(timezone.utc).isoformat()

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise MarketDataError(f"东方财富返回非 JSON：{exc}") from exc
        data = payload.get("data")
        if not data or not data.get("klines"):
            raise MarketDataError(
                f"东方财富 {symbol} 返回空 K 线（rc={payload.get('rc')}），fail-closed")

        name = data.get("name") or ticker
        bars: List[MarketDataBar] = []
        for line in data["klines"]:
            p = line.split(",")
            day = p[0]
            if start_date and day < start_date:
                continue
            if end_date and day > end_date:
                continue
            o, c, h, l, v = float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5])
            prov = DataProvenance(
                source=self.SOURCE, url=f"https://{used_host}/",
                fetched_at=fetched_at, raw_payload_hash=raw_hash,
                adjustment_applied=adjustment,
                quality_flag=(DataQualityFlag.ADJUSTED
                              if adjustment != AdjustmentType.NONE else DataQualityFlag.OK))
            bars.append(MarketDataBar(
                date=day, open=o, close=c, high=h, low=l, volume=v,
                amount=None, provenance=prov))

        if not bars:
            raise MarketDataError(f"日期过滤后无 K 线：{symbol}")

        bars, _ = normalize_bars(bars)
        cal_id = uc.derive_us_calendar_id(ticker)
        series = MarketDataSeries(
            symbol=symbol, name=name, frequency=FrequencyType.DAILY,
            adjustment=adjustment, trading_calendar=cal_id,
            source=self.SOURCE, fetched_at=fetched_at, bars=bars)
        series.quality_report = self.validate_data(series)
        return series

    # -------------------------------------------------- 质量 / 兼容接口
    def validate_data(self, series: MarketDataSeries) -> DataQualityReport:
        # 注入美股交易日历，避免误用 A 股日历计算预期交易日
        return assess_series_quality(series, uc.get_trading_days)

    def get_trading_calendar(self, exchange: str, start_date: str, end_date: str) -> List[str]:
        return uc.get_trading_days(start_date, end_date)

    def get_stock_data(self, code: str, days: int = 250) -> StockData:
        symbol = M.to_internal_symbol(code)
        series = self.fetch_klines(symbol, adjustment=AdjustmentType.FORWARD)
        bars = series.bars[-days:]
        return StockData(
            code=code, name=series.name,
            klines=[OHLCV(date=b.date, open=b.open, high=b.high, low=b.low,
                          close=b.close, volume=b.volume, amount=b.amount) for b in bars],
            market="美股", source="eastmoney")

    def get_index_data(self, code: str, days: int = 250) -> StockData:
        return self.get_stock_data(code, days)

    def get_sector_constituents(self, sector_code: str) -> List[str]:
        raise NotImplementedError("EastMoneyDataProvider 暂不支持板块成分")
