"""
MultiSourceDataProvider — 多源容错数据提供者

策略（Blocker 7）：
- NONE（raw）：主源 Sina，失败降级到 Tencent。
- qfq/hfq：仅 Tencent；失败则 raise DataProviderError（fail closed），
  绝不静默回退到 Sina raw。
- 可选交叉验证：两源 raw 收盘价差异 >0.5% 时标记异常。
"""

from typing import List, Optional

from ..interfaces import DataProvider, MarketDataError, DataProviderError
from ..types import (
    MarketDataBar, MarketDataSeries, DataQualityReport,
    DataProvenance, DataQualityFlag, DataSourceType,
    AdjustmentType, FrequencyType, StockData,
)
from .sina_provider import SinaDataProvider
from .tencent_provider import TencentDataProvider
from .trading_calendar import get_trading_days


class MultiSourceDataProvider(DataProvider):
    """多源容错数据提供者"""

    def __init__(self, cross_validate: bool = True):
        self.primary = SinaDataProvider()
        self.secondary = TencentDataProvider()
        self.cross_validate = cross_validate

    def fetch_klines(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        frequency: FrequencyType = FrequencyType.DAILY,
        adjustment: AdjustmentType = AdjustmentType.NONE,
    ) -> MarketDataSeries:
        # Blocker 7：qfq/hfq 请求 —— 优先 Tencent；Tencent 失败则 fail closed，
        # 绝不静默回退到 Sina raw（那样会把不复权数据伪装成已复权）。
        if adjustment != AdjustmentType.NONE:
            try:
                return self.secondary.fetch_klines(symbol, start_date, end_date, frequency, adjustment)
            except Exception as e:
                raise DataProviderError(
                    f"Adjustment '{adjustment.value}' requested for {symbol}, but Tencent failed "
                    f"and Sina is raw-only. Refusing to silently fall back to Sina raw (provider "
                    f"identity + adjustment semantics would be violated). Original error: {e}"
                ) from e

        # 尝试主源 Sina
        primary_series = None
        try:
            primary_series = self.primary.fetch_klines(symbol, start_date, end_date, frequency, adjustment)
        except Exception:
            pass

        # 尝试备源 Tencent
        secondary_series = None
        try:
            secondary_series = self.secondary.fetch_klines(symbol, start_date, end_date, frequency, adjustment)
        except Exception:
            pass

        # 选择返回
        if primary_series is None and secondary_series is None:
            raise MarketDataError(f"Both primary (Sina) and secondary (Tencent) failed for {symbol}")

        result = primary_series or secondary_series

        # 交叉验证
        if self.cross_validate and primary_series and secondary_series:
            result = self._cross_validate(result, secondary_series)

        return result

    def _cross_validate(self, primary: MarketDataSeries, secondary: MarketDataSeries) -> MarketDataSeries:
        """比较两源收盘价，标记 >0.5% 差异"""
        sec_by_date = {b.date: b.close for b in secondary.bars}
        anomalies = []
        diffs = []
        for bar in primary.bars:
            if bar.date in sec_by_date:
                s_close = sec_by_date[bar.date]
                if bar.close > 0:
                    diff_pct = abs(bar.close - s_close) / bar.close
                    diffs.append(diff_pct)
                    if diff_pct > 0.005:  # 0.5%
                        anomalies.append({
                            "type": "source_mismatch",
                            "date": bar.date,
                            "sina_close": bar.close,
                            "tencent_close": s_close,
                            "diff_pct": round(diff_pct, 4),
                        })

        avg_diff = sum(diffs) / len(diffs) if diffs else None

        # 更新 quality_report
        if primary.quality_report is not None:
            qr = primary.quality_report
            qr.source_consistency = round(1.0 - avg_diff, 6) if avg_diff is not None else None
            qr.anomalies = list(qr.anomalies) + anomalies
            qr.notes = (qr.notes + f"; cross_validated: avg_diff={avg_diff:.4%}"
                        if avg_diff is not None else qr.notes)
        return primary

    def validate_data(self, series: MarketDataSeries) -> DataQualityReport:
        return self.primary.validate_data(series)

    def get_trading_calendar(self, exchange: str, start_date: str, end_date: str) -> List[str]:
        return get_trading_days(start_date, end_date)

    def get_stock_data(self, code: str, days: int = 250) -> StockData:
        series = self.fetch_klines(symbol=code, adjustment=AdjustmentType.NONE)
        return series.to_stock_data()

    def get_index_data(self, code: str, days: int = 250) -> StockData:
        return self.get_stock_data(code, days)

    def get_sector_constituents(self, sector_code: str) -> List[str]:
        raise NotImplementedError("MultiSource provider does not support sector constituents")
