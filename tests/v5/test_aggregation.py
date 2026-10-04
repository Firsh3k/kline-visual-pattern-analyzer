"""
FIR-23 Blocker 4：日线 -> 周线/月线 确定性聚合测试
"""

from core.market_data.aggregation import aggregate_daily_bars
from core.types import (
    MarketDataBar, FrequencyType, DataProvenance,
    DataSourceType, AdjustmentType, DataQualityFlag,
)


def _prov():
    return DataProvenance(
        source=DataSourceType.TENCENT, url="u", fetched_at="t",
        raw_payload_hash="h", adjustment_applied=AdjustmentType.FORWARD,
        quality_flag=DataQualityFlag.ADJUSTED,
    )


def _bar(date, o, h, l, c, v):
    return MarketDataBar(date=date, open=o, high=h, low=l, close=c, volume=v, provenance=_prov())


class TestAggregation:
    def test_daily_returns_original(self):
        bars = [_bar("2026-01-05", 10, 10.5, 9.8, 10.2, 1000)]
        out = aggregate_daily_bars(bars, FrequencyType.DAILY)
        assert out is not bars  # list() copy
        assert len(out) == 1

    def test_weekly_aggregation_rules(self):
        # 同一周（2026-01-05 周一 ~ 2026-01-09 周五）5 根日线
        week = [
            _bar("2026-01-05", 10.0, 10.8, 9.8, 10.5, 1000),   # Mon open=10.0
            _bar("2026-01-06", 10.5, 11.0, 10.2, 10.8, 1100),
            _bar("2026-01-07", 10.8, 11.5, 10.6, 11.2, 900),
            _bar("2026-01-08", 11.2, 11.3, 10.9, 11.0, 1200),
            _bar("2026-01-09", 11.0, 11.6, 10.8, 11.4, 1300),   # Fri close=11.4
        ]
        out = aggregate_daily_bars(week, FrequencyType.WEEKLY)
        assert len(out) == 1
        w = out[0]
        assert w.open == 10.0          # 周一 open
        assert w.close == 11.4         # 周五 close
        assert w.high == 11.6          # 周内最高
        assert w.low == 9.8            # 周内最低
        assert w.volume == 1000+1100+900+1200+1300  # 成交量求和
        assert w.date == "2026-01-09"  # 标签 = 周五

    def test_weekly_multiple_weeks(self):
        bars = [
            _bar("2026-01-05", 10, 10.5, 9.8, 10.2, 1000),
            _bar("2026-01-06", 10.2, 10.8, 10.0, 10.6, 1100),
            # next week
            _bar("2026-01-12", 10.6, 11.0, 10.4, 10.9, 1200),
            _bar("2026-01-13", 10.9, 11.2, 10.7, 11.1, 1300),
        ]
        out = aggregate_daily_bars(bars, FrequencyType.WEEKLY)
        assert len(out) == 2
        assert out[0].date == "2026-01-06"
        assert out[1].date == "2026-01-13"
        # ascending
        assert out[0].date < out[1].date

    def test_monthly_aggregation(self):
        bars = [
            _bar("2026-01-05", 10, 10.5, 9.8, 10.2, 1000),
            _bar("2026-01-20", 10.2, 11.0, 10.0, 10.8, 1100),
            # next month
            _bar("2026-02-03", 10.8, 11.5, 10.5, 11.2, 1200),
        ]
        out = aggregate_daily_bars(bars, FrequencyType.MONTHLY)
        assert len(out) == 2
        assert out[0].open == 10.0
        assert out[0].close == 10.8
        assert out[0].high == 11.0
        assert out[0].volume == 2100
        assert out[0].date == "2026-01-20"
        assert out[1].date == "2026-02-03"

    def test_bar_frequency_semantics_preserved(self):
        # 聚合不改变复权口径：provenance 携带的 adjustment_applied 应被保留
        bars = [_bar("2026-01-05", 10, 10.5, 9.8, 10.2, 1000)]
        out = aggregate_daily_bars(bars, FrequencyType.WEEKLY)
        assert out[0].provenance is not None
        assert out[0].provenance.adjustment_applied == AdjustmentType.FORWARD
