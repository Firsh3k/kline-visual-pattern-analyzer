"""
FIR-23 Blocker 7：source/adjustment policy 一致性测试
"""

import pytest
from core.market_data.policy import UseCase, DEFAULT_POLICY, adjustment_for
from core.market_data.multi_source import MultiSourceDataProvider
from core import AdjustmentType, FrequencyType, MarketDataSeries, MarketDataBar
from core.types import DataProvenance, DataSourceType, DataQualityFlag


class TestPolicyMapping:
    def test_technical_analysis_is_forward(self):
        assert adjustment_for(UseCase.TECHNICAL_ANALYSIS) == AdjustmentType.FORWARD

    def test_transaction_price_is_none(self):
        assert adjustment_for(UseCase.TRANSACTION_PRICE) == AdjustmentType.NONE

    def test_default_policy_mapping(self):
        assert DEFAULT_POLICY[UseCase.TECHNICAL_ANALYSIS] == AdjustmentType.FORWARD
        assert DEFAULT_POLICY[UseCase.TRANSACTION_PRICE] == AdjustmentType.NONE


class _StubSeries(MarketDataSeries):
    pass


def _make_daily_series():
    prov = DataProvenance(source=DataSourceType.TENCENT, url="u", fetched_at="t",
                          raw_payload_hash="h", adjustment_applied=AdjustmentType.FORWARD,
                          quality_flag=DataQualityFlag.ADJUSTED)
    return MarketDataSeries(
        symbol="sh600519", name="m", frequency=FrequencyType.DAILY,
        adjustment=AdjustmentType.FORWARD, trading_calendar="SSE",
        source=DataSourceType.TENCENT, fetched_at="t",
        bars=[MarketDataBar(date="2026-01-05", open=10, high=10.5, low=9.8, close=10.2,
                            volume=1, provenance=prov)],
    )


class TestMultiSourceFailClosed:
    def test_qfq_failure_raises_not_raw_fallback(self, monkeypatch):
        provider = MultiSourceDataProvider(cross_validate=False)

        def _boom(*args, **kwargs):
            raise RuntimeError("tencent down")
        monkeypatch.setattr(provider.secondary, "fetch_klines", _boom)

        # Sina cannot serve qfq either; Tencent fails -> must raise, not silently return Sina raw
        with pytest.raises(Exception) as ei:
            provider.fetch_klines(symbol="sh600519", adjustment=AdjustmentType.FORWARD)
        # 必须是 fail-closed 错误，且明确提到拒绝回退 raw
        assert "Refusing to silently fall back" in str(ei.value) or "Tencent" in str(ei.value)

    def test_qfq_happy_path_uses_tencent(self, monkeypatch):
        provider = MultiSourceDataProvider(cross_validate=False)
        seen = {}

        def _fake(symbol, start=None, end=None, frequency=FrequencyType.DAILY, adjustment=AdjustmentType.NONE):
            seen["adjustment"] = adjustment
            return _make_daily_series()
        monkeypatch.setattr(provider.secondary, "fetch_klines", _fake)

        s = provider.fetch_klines(symbol="sh600519", adjustment=AdjustmentType.FORWARD)
        assert s.adjustment == AdjustmentType.FORWARD
        assert seen["adjustment"] == AdjustmentType.FORWARD
