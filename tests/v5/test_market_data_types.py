"""
FIR-23: 市场数据类型单元测试
"""

import pytest
from core import (
    AdjustmentType, FrequencyType, DataSourceType, DataQualityFlag,
    DataProvenance, MarketDataBar, MarketDataSeries, DataQualityReport,
    OHLCV, StockData,
)


class TestEnums:
    def test_adjustment_type_values(self):
        assert AdjustmentType.NONE.value == "none"
        assert AdjustmentType.FORWARD.value == "qfq"
        assert AdjustmentType.BACKWARD.value == "hfq"

    def test_frequency_type_values(self):
        assert FrequencyType.DAILY.value == "daily"
        assert FrequencyType.WEEKLY.value == "weekly"
        assert FrequencyType.MONTHLY.value == "monthly"

    def test_data_source_type_values(self):
        assert DataSourceType.SINA.value == "sina"
        assert DataSourceType.TENCENT.value == "tencent"
        assert DataSourceType.IFIND.value == "ifind"
        assert DataSourceType.TONGDAXIN.value == "tongdaxin"
        assert DataSourceType.MANUAL.value == "manual"

    def test_data_quality_flag_values(self):
        assert DataQualityFlag.OK.value == "ok"
        assert DataQualityFlag.SUSPENDED.value == "suspended"
        assert DataQualityFlag.MISSING.value == "missing"
        assert DataQualityFlag.ADJUSTED.value == "adjusted"
        assert DataQualityFlag.PARTIAL.value == "partial"


class TestDataProvenance:
    def _sample(self):
        return DataProvenance(
            source=DataSourceType.SINA,
            url="http://test/api",
            fetched_at="2026-10-04T10:00:00+00:00",
            raw_payload_hash="abc123hash",
            adjustment_applied=AdjustmentType.NONE,
            quality_flag=DataQualityFlag.OK,
        )

    def test_round_trip(self):
        p = self._sample()
        d = p.to_dict()
        p2 = DataProvenance.from_dict(d)
        assert p2.source == p.source
        assert p2.url == p.url
        assert p2.fetched_at == p.fetched_at
        assert p2.raw_payload_hash == p.raw_payload_hash
        assert p2.adjustment_applied == p.adjustment_applied
        assert p2.quality_flag == p.quality_flag

    def test_to_dict_enum_values(self):
        d = self._sample().to_dict()
        assert d["source"] == "sina"
        assert d["adjustment_applied"] == "none"
        assert d["quality_flag"] == "ok"


class TestMarketDataBar:
    def _sample_bar(self):
        prov = DataProvenance(
            source=DataSourceType.SINA, url="http://x", fetched_at="2026-10-04T10:00:00Z",
            raw_payload_hash="h", adjustment_applied=AdjustmentType.NONE, quality_flag=DataQualityFlag.OK,
        )
        return MarketDataBar(
            date="2026-10-04", open=10.0, high=11.0, low=9.5, close=10.5,
            volume=100000.0, amount=1050000.0, provenance=prov,
        )

    def test_round_trip(self):
        b = self._sample_bar()
        d = b.to_dict()
        b2 = MarketDataBar.from_dict(d)
        assert b2.date == b.date
        assert b2.open == b.open
        assert b2.high == b.high
        assert b2.low == b.low
        assert b2.close == b.close
        assert b2.volume == b.volume
        assert b2.amount == b.amount
        assert b2.provenance is not None
        assert b2.provenance.source == DataSourceType.SINA

    def test_bar_requires_provenance(self):
        # blocker5: provenance is now a required constructor arg
        with pytest.raises(TypeError):
            MarketDataBar(date="2026-10-04", open=1.0, high=1.1, low=0.9, close=1.05, volume=100.0)

    def test_from_dict_without_provenance_uses_legacy_placeholder(self):
        # legacy conversion: missing provenance becomes an explicit MANUAL placeholder (not None)
        raw = {"date": "2026-10-04", "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 100.0}
        b = MarketDataBar.from_dict(raw)
        assert b.provenance is not None
        assert b.provenance.source == DataSourceType.MANUAL
        assert b.provenance.fetched_at == ""
        assert b.amount is None

    def test_to_ohlcv(self):
        b = self._sample_bar()
        o = b.to_ohlcv()
        assert isinstance(o, OHLCV)
        assert o.date == b.date
        assert o.close == b.close
        assert o.volume == b.volume

    def test_from_ohlcv(self):
        o = OHLCV(date="2026-10-04", open=10.0, high=11.0, low=9.5, close=10.5, volume=100.0)
        b = MarketDataBar.from_ohlcv(o)
        assert b.date == o.date
        assert b.close == o.close
        # blocker5: legacy conversion constructs an explicit MANUAL provenance, not None
        assert b.provenance is not None
        assert b.provenance.source == DataSourceType.MANUAL
        assert b.provenance.adjustment_applied == AdjustmentType.NONE


class TestDataQualityReport:
    def _sample(self):
        return DataQualityReport(
            total_days=100, expected_trading_days=70, actual_bars=68,
            suspended_days=1, missing_days=1, completeness_ratio=0.9714,
            adjustment_consistent=True, source_consistency=0.999,
            anomalies=[{"type": "price_jump", "date": "2026-03-01"}],
            notes="test",
        )

    def test_round_trip(self):
        qr = self._sample()
        d = qr.to_dict()
        qr2 = DataQualityReport.from_dict(d)
        assert qr2.total_days == qr.total_days
        assert qr2.completeness_ratio == qr.completeness_ratio
        assert qr2.source_consistency == qr.source_consistency
        assert len(qr2.anomalies) == 1
        assert qr2.notes == "test"


class TestMarketDataSeries:
    def _sample_series(self):
        prov = DataProvenance(
            source=DataSourceType.MANUAL, url="", fetched_at="",
            raw_payload_hash="", adjustment_applied=AdjustmentType.NONE,
            quality_flag=DataQualityFlag.OK,
        )
        bars = [
            MarketDataBar(date="2026-10-01", open=10.0, high=10.5, low=9.8, close=10.2, volume=1000.0, provenance=prov),
            MarketDataBar(date="2026-10-02", open=10.2, high=10.8, low=10.0, close=10.6, volume=1200.0, provenance=prov),
        ]
        return MarketDataSeries(
            symbol="sh600519", name="贵州茅台",
            frequency=FrequencyType.DAILY, adjustment=AdjustmentType.NONE,
            trading_calendar="SSE", source=DataSourceType.SINA,
            fetched_at="2026-10-04T10:00:00Z", bars=bars,
        )

    def test_round_trip(self):
        s = self._sample_series()
        d = s.to_dict()
        s2 = MarketDataSeries.from_dict(d)
        assert s2.symbol == s.symbol
        assert s2.name == s.name
        assert s2.frequency == s.frequency
        assert s2.adjustment == s.adjustment
        assert len(s2.bars) == 2
        assert s2.bars[0].close == 10.2

    def test_round_trip_with_quality_report(self):
        s = self._sample_series()
        s.quality_report = DataQualityReport(
            total_days=2, expected_trading_days=2, actual_bars=2,
            suspended_days=0, missing_days=0, completeness_ratio=1.0,
            adjustment_consistent=True,
        )
        s2 = MarketDataSeries.from_dict(s.to_dict())
        assert s2.quality_report is not None
        assert s2.quality_report.actual_bars == 2

    def test_to_ohlcv_list(self):
        s = self._sample_series()
        ol = s.to_ohlcv_list()
        assert len(ol) == 2
        assert all(isinstance(o, OHLCV) for o in ol)
        assert ol[0].close == 10.2

    def test_to_stock_data(self):
        s = self._sample_series()
        sd = s.to_stock_data()
        assert isinstance(sd, StockData)
        assert sd.code == "sh600519"
        assert sd.name == "贵州茅台"
        assert len(sd.klines) == 2
        assert sd.source == "sina"

    def test_schema_version_and_last_bar_date(self):
        s = self._sample_series()
        assert s.schema_version == "1.0"
        assert s.last_bar_date == "2026-10-02"
        d = s.to_dict()
        assert d["schema_version"] == "1.0"
        assert d["last_bar_date"] == "2026-10-02"
        # round-trip ignores derived last_bar_date
        s2 = MarketDataSeries.from_dict(d)
        assert s2.last_bar_date == "2026-10-02"
