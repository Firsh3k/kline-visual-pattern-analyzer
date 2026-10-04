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
        # Blocker 6：无停牌数据源时，未知缺口记入 unknown_missing_days，
        # 而不是静默记为 missing_days/OK。
        assert qr.unknown_missing_days >= 1
        assert qr.missing_days == 0
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

class TestDataQualityBlocker6:
    """Blocker 6：suspension/missing/malformed 策略真正执行"""

    def setup_method(self):
        self.provider = SinaDataProvider()

    def test_ohlc_invariant_violation_detected(self):
        # high < low -> malformed
        bars = [
            MarketDataBar(date="2024-01-02", open=10, high=9.0, low=9.5, close=10.2,
                          volume=1000.0, provenance=_prov()),
        ]
        s = _make_series(bars)
        qr = self.provider.validate_data(s)
        inv = [a for a in qr.anomalies if a.get("type") == "ohlc_invariant"]
        assert len(inv) >= 1

    def test_negative_volume_rejected(self):
        bars = [
            MarketDataBar(date="2024-01-02", open=10, high=10.5, low=9.8, close=10.2,
                          volume=-5.0, provenance=_prov()),
        ]
        s = _make_series(bars)
        qr = self.provider.validate_data(s)
        inv = [a for a in qr.anomalies if a.get("type") == "ohlc_invariant"]
        assert any("volume" in a.get("msg", "") for a in inv)

    def test_duplicate_dates_normalized_deterministically(self):
        from core.market_data.validation import normalize_bars
        bars = [
            _bar("2024-01-02", 10, 10.5, 9.8, 10.2, 1000),
            _bar("2024-01-02", 11, 11.5, 10.8, 11.2, 2000),  # duplicate, last wins
            _bar("2024-01-03", 11.2, 11.8, 11.0, 11.6, 1500),
        ]
        norm, anomalies = normalize_bars(bars)
        dup = [a for a in anomalies if a.get("type") == "duplicate_date"]
        assert len(dup) == 1
        assert len(norm) == 2
        # last occurrence kept
        assert norm[0].close == 11.2

    def test_unsorted_bars_get_sorted(self):
        from core.market_data.validation import normalize_bars
        bars = [
            _bar("2024-01-03", 11.2, 11.8, 11.0, 11.6, 1500),
            _bar("2024-01-02", 10, 10.5, 9.8, 10.2, 1000),
        ]
        norm, anomalies = normalize_bars(bars)
        assert [b.date for b in norm] == ["2024-01-02", "2024-01-03"]
        assert any(a.get("type") == "unsorted_bars" for a in anomalies)

    def test_gap_is_unknown_not_ok(self):
        # 跳过中间交易日 -> unknown_missing_days>0 且 adjustment_consistent 不因缺口变 True
        bars = [
            _bar("2024-01-02", 10, 10.5, 9.8, 10.2, 1000),
            _bar("2024-01-04", 10.6, 11.0, 10.3, 10.9, 1200),
        ]
        s = _make_series(bars)
        qr = self.provider.validate_data(s)
        assert qr.unknown_missing_days == 1
        assert "unknown_missing=1" in qr.notes


class TestAdjustmentConsistencyBlocker2:
    """Blocker 2：复权一致性不能是恒真重言式"""

    def test_none_adjustment_not_applicable(self):
        from core.market_data.validation import validate_adjustment_consistency, AdjustmentConsistency
        bars = [_bar("2024-01-02", 10, 10.5, 9.8, 10.2, 1000)]
        s = _make_series(bars)
        state, anomalies = validate_adjustment_consistency(s)
        assert state == AdjustmentConsistency.NOT_APPLICABLE

    def test_adjusted_without_raw_is_unverified_not_true(self):
        from core.market_data.validation import validate_adjustment_consistency, AdjustmentConsistency, ADJUSTMENT_CONSISTENT_BOOL
        bars = [_bar("2024-01-02", 10, 10.5, 9.8, 10.2, 1000)]
        s = _make_series(bars)
        s.adjustment = AdjustmentType.FORWARD
        state, anomalies = validate_adjustment_consistency(s, raw_closes=None)
        assert state == AdjustmentConsistency.UNVERIFIED
        # unknown 绝不编码为 True
        assert ADJUSTMENT_CONSISTENT_BOOL[state] is False
        assert any(a.get("type") == "adjustment_unverified" for a in anomalies)

    def test_monotonic_factors_are_consistent(self):
        from core.market_data.validation import validate_adjustment_consistency, AdjustmentConsistency
        # raw=[10,10,10], adj=[10,15,20] -> factor=[1,1.5,2] monotonic increasing
        bars = [
            MarketDataBar(date="2024-01-02", open=10, high=10, low=10, close=10.0, volume=1, provenance=_prov()),
            MarketDataBar(date="2024-01-03", open=15, high=15, low=15, close=15.0, volume=1, provenance=_prov()),
            MarketDataBar(date="2024-01-04", open=20, high=20, low=20, close=20.0, volume=1, provenance=_prov()),
        ]
        s = _make_series(bars)
        s.adjustment = AdjustmentType.BACKWARD
        state, _ = validate_adjustment_consistency(s, raw_closes=[10.0, 10.0, 10.0])
        assert state == AdjustmentConsistency.CONSISTENT

    def test_non_monotonic_factors_are_inconsistent(self):
        from core.market_data.validation import validate_adjustment_consistency, AdjustmentConsistency
        # factor goes 2.0 -> 0.5 (decreases non-trivially) -> inconsistent
        bars = [
            MarketDataBar(date="2024-01-02", open=20, high=20, low=20, close=20.0, volume=1, provenance=_prov()),
            MarketDataBar(date="2024-01-03", open=5, high=5, low=5, close=5.0, volume=1, provenance=_prov()),
        ]
        s = _make_series(bars)
        s.adjustment = AdjustmentType.BACKWARD
        state, _ = validate_adjustment_consistency(s, raw_closes=[10.0, 10.0])
        assert state == AdjustmentConsistency.INCONSISTENT
