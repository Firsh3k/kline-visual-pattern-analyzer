"""
FIR-23: 数据质量单元测试
"""

import pytest
from core import (
    MarketDataBar, MarketDataSeries, DataQualityReport,
    DataSourceType, AdjustmentType, FrequencyType,
    DataProvenance, DataQualityFlag,
)


def _prov(adjustment=AdjustmentType.NONE):
    return DataProvenance(
        source=DataSourceType.MANUAL, url="", fetched_at="",
        raw_payload_hash="", adjustment_applied=adjustment,
        quality_flag=DataQualityFlag.OK,
    )


def _bar(date, o, h, l, c, v):
    return MarketDataBar(date=date, open=o, high=h, low=l, close=c, volume=v, provenance=_prov())
from core.market_data.sina_provider import SinaDataProvider


def _make_series(bars):
    return MarketDataSeries(
        symbol="sh600519", name="贵州茅台",
        frequency=FrequencyType.DAILY, adjustment=AdjustmentType.NONE,
        trading_calendar="SSE", source=DataSourceType.SINA,
        fetched_at="2026-10-04T10:00:00Z", bars=bars,
    )


class TestDataQuality:
    def setup_method(self):
        self.provider = SinaDataProvider()

    def test_perfect_data_completeness(self):
        # 连续5个交易日，无缺失
        bars = [
            _bar("2024-01-02", 10, 10.5, 9.8, 10.2, 1000),
            _bar("2024-01-03", 10.2, 10.8, 10.0, 10.6, 1100),
            _bar("2024-01-04", 10.6, 11.0, 10.3, 10.9, 1200),
            _bar("2024-01-05", 10.9, 11.2, 10.5, 10.8, 900),
        ]
        s = _make_series(bars)
        qr = self.provider.validate_data(s)
        assert qr.actual_bars == 4
        assert qr.expected_trading_days == 4
        assert qr.missing_days == 0
        assert qr.completeness_ratio == 1.0

    def test_missing_data_detected(self):
        # 跳过中间一个交易日（2024-01-03 周三）
        bars = [
            _bar("2024-01-02", 10, 10.5, 9.8, 10.2, 1000),
            # missing 2024-01-03
            _bar("2024-01-04", 10.6, 11.0, 10.3, 10.9, 1200),
            _bar("2024-01-05", 10.9, 11.2, 10.5, 10.8, 900),
        ]
        s = _make_series(bars)
        qr = self.provider.validate_data(s)
        assert qr.missing_days >= 1
        assert qr.completeness_ratio < 1.0

    def test_price_jump_anomaly(self):
        # 制造一个 >20% 的跳变
        bars = [
            _bar("2024-01-02", 10, 10.5, 9.8, 10.0, 1000),
            _bar("2024-01-03", 12, 13, 11.5, 12.5, 2000),  # +25%
        ]
        s = _make_series(bars)
        qr = self.provider.validate_data(s)
        jump_anomalies = [a for a in qr.anomalies if a.get("type") == "price_jump"]
        assert len(jump_anomalies) >= 1
        assert jump_anomalies[0]["date"] == "2024-01-03"

    def test_no_jump_for_normal_movement(self):
        bars = [
            _bar("2024-01-02", 10, 10.5, 9.8, 10.2, 1000),
            _bar("2024-01-03", 10.2, 10.8, 10.0, 10.6, 1100),
        ]
        s = _make_series(bars)
        qr = self.provider.validate_data(s)
        jump_anomalies = [a for a in qr.anomalies if a.get("type") == "price_jump"]
        assert len(jump_anomalies) == 0

    def test_empty_series(self):
        s = _make_series([])
        qr = self.provider.validate_data(s)
        assert qr.actual_bars == 0
        assert qr.completeness_ratio == 0.0
