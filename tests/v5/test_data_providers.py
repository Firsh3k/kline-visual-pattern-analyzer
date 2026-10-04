"""
FIR-23: 数据源集成测试（需要网络）

通过 SKIP_NETWORK_TESTS=1 环境变量跳过。
"""

import os
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_NETWORK_TESTS") == "1",
    reason="Skipping network tests (SKIP_NETWORK_TESTS=1)",
)

from core.market_data.sina_provider import SinaDataProvider
from core.market_data.tencent_provider import TencentDataProvider
from core.market_data.multi_source import MultiSourceDataProvider
from core import AdjustmentType, FrequencyType


class TestSinaProviderIntegration:
    def setup_method(self):
        self.provider = SinaDataProvider()

    def test_fetch_moutai_daily(self):
        series = self.provider.fetch_klines(
            symbol="sh600519",
            frequency=FrequencyType.DAILY,
            adjustment=AdjustmentType.NONE,
        )
        assert series.symbol == "sh600519"
        assert len(series.bars) > 100
        assert series.bars[0].date <= series.bars[-1].date
        # 每根 bar 有 provenance
        assert all(b.provenance is not None for b in series.bars)
        assert series.quality_report is not None
        assert series.quality_report.actual_bars == len(series.bars)

    def test_invalid_code_raises(self):
        with pytest.raises(Exception):
            self.provider.fetch_klines(symbol="sh99999999")


class TestTencentProviderIntegration:
    def setup_method(self):
        self.provider = TencentDataProvider()

    def test_fetch_moutai_qfq(self):
        series = self.provider.fetch_klines(
            symbol="sh600519",
            frequency=FrequencyType.DAILY,
            adjustment=AdjustmentType.FORWARD,
        )
        assert len(series.bars) > 100
        assert series.adjustment == AdjustmentType.FORWARD
        assert all(b.provenance is not None for b in series.bars)

    def test_fetch_moutai_none(self):
        series = self.provider.fetch_klines(
            symbol="sh600519",
            adjustment=AdjustmentType.NONE,
        )
        assert len(series.bars) > 100


class TestMultiSourceIntegration:
    def test_cross_validation(self):
        provider = MultiSourceDataProvider(cross_validate=True)
        series = provider.fetch_klines(symbol="sh600519")
        assert len(series.bars) > 100
        # 交叉验证后 source_consistency 应接近 1.0
        if series.quality_report and series.quality_report.source_consistency is not None:
            assert series.quality_report.source_consistency > 0.99
