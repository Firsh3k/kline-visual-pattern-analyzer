"""FIR-26 — V5 release integration qualification.

This suite intentionally crosses the V5 module boundaries instead of retesting
individual units in isolation:

FIR-15 sector authority -> FIR-23/25 market-data provenance -> FIR-12/24
canonical signal generation -> FIR-13 backtest/calibration semantics.
"""

from datetime import date, timedelta
import os

import pytest

from core import (
    AdjustmentType,
    DataProvenance,
    DataQualityFlag,
    DataQualityReport,
    DataSourceType,
    FrequencyType,
    MarketDataBar,
    MarketDataSeries,
    OHLCV,
)
from core.calibration.backtest_engine import FixedHorizonBacktester
from core.calibration.calibrator import EmpiricalCalibrator
from core.calibration.data_collector import CalibrationDataCollector, to_market_symbol
from core.calibration.signal_generator import CalibrationSignalGenerator
from core.calibration.types import (
    ConfirmationState,
    MarketRegime,
    PatternType,
    SignalOccurrence,
)
from core.sector_validation import (
    MembershipStatus,
    load_authoritative_snapshot,
    reconcile_snapshot_to_reports,
)
from scripts.refresh_sector_membership import load_sectors_from_js


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SECTOR_FILE = os.path.join(ROOT, "sector_data", "new_sectors.js")
SECTOR_SNAPSHOT = os.path.join(
    ROOT, "tests", "v5", "fixtures", "fir15_innovation_drug_snapshot.json"
)


def _series(symbol: str, calendar: str, rising: bool = True, n: int = 110) -> MarketDataSeries:
    source = DataSourceType.SINA
    adjustment = AdjustmentType.FORWARD
    prov = DataProvenance(
        source=source,
        url="fixture://fir26",
        fetched_at="2026-10-05T00:00:00Z",
        raw_payload_hash=f"fir26-{symbol}",
        adjustment_applied=adjustment,
        quality_flag=DataQualityFlag.OK,
    )
    d0 = date(2025, 1, 2)
    bars = []
    for i in range(n):
        close = 100.0 + i * 0.3 if rising else 200.0 - i * 0.3
        bars.append(
            MarketDataBar(
                date=(d0 + timedelta(days=i)).isoformat(),
                open=close - 0.1,
                high=close + 0.2,
                low=close - 0.2,
                close=close,
                volume=2000.0,
                amount=None,
                provenance=prov,
            )
        )
    quality = DataQualityReport(
        total_days=n,
        expected_trading_days=n,
        actual_bars=n,
        suspended_days=0,
        missing_days=0,
        completeness_ratio=1.0,
        adjustment_consistent=True,
        source_consistency=0.999,
        anomalies=[],
        notes="FIR-26 deterministic integration fixture",
    )
    return MarketDataSeries(
        symbol=symbol,
        name="FIR26 fixture",
        frequency=FrequencyType.DAILY,
        adjustment=adjustment,
        trading_calendar=calendar,
        source=source,
        fetched_at="2026-10-05T00:00:00Z",
        bars=bars,
        quality_report=quality,
    )


def _overall_metric(summary, horizon: int):
    group = next(
        g for g in summary.groups
        if g.pattern_type == "all"
        and g.market_regime == "all"
        and g.confirmation_state == "all"
    )
    return group.metrics_by_horizon[horizon]


def test_exchange_routing_is_canonical_and_fail_closed():
    """FIR-13 must consume FIR-25 exchange semantics without reimplementing drift."""
    assert to_market_symbol("600519") == "sh600519"
    assert to_market_symbol("000858") == "sz000858"
    assert to_market_symbol("920211") == "bj920211"
    assert to_market_symbol("430047") == "bj430047"
    assert to_market_symbol("830799") == "bj830799"
    with pytest.raises(ValueError):
        to_market_symbol("700000")


@pytest.mark.parametrize(
    "symbol,calendar",
    [
        ("sh600519", "SSE"),
        ("sz000858", "SZSE"),
        ("bj920211", "BSE"),
    ],
)
def test_sh_sz_bse_provenance_survives_calibration_input(symbol, calendar):
    series = _series(symbol, calendar)
    provenance = CalibrationDataCollector._build_provenance(series)
    assert provenance["trading_calendar"] == calendar
    assert provenance["data_source"] == "sina"
    assert provenance["adjustment"] == "qfq"
    assert provenance["data_quality"]["status"] == "OK"


def test_authoritative_sector_member_flows_into_signal_and_calibration(tmp_path):
    """Real production sector authority must survive into the final calibration path."""
    sectors = load_sectors_from_js(SECTOR_FILE)
    snapshot = load_authoritative_snapshot(SECTOR_SNAPSHOT)
    reports, _ = reconcile_snapshot_to_reports(
        snapshot,
        sectors,
        sector_filter="创新药",
        sector_codes={"创新药": "886015"},
        provenance_dir=str(tmp_path / "provenance"),
        evidence_dir=str(tmp_path / "evidence"),
    )
    sector_report = reports["创新药"]
    member = next(c for c in sector_report.constituents if c.code == "600276")
    assert member.status == MembershipStatus.VERIFIED

    series = _series("sh600276", "SSE", rising=True)
    provenance = CalibrationDataCollector._build_provenance(series)
    generator = CalibrationSignalGenerator()
    signals = generator.generate_signals(
        series.to_ohlcv_list(),
        "600276",
        {},
        provenance=provenance,
    )
    assert signals
    signal = signals[0]
    assert signal.data_source == "sina"
    assert signal.adjustment == "qfq"
    assert signal.data_quality == "OK"

    backtests = FixedHorizonBacktester().backtest_all_horizons(
        signal, series.to_ohlcv_list(), horizons=[3, 5]
    )
    assert all(b.sufficient_data for b in backtests)
    assert all(b.direction == "bullish" for b in backtests)
    assert all(b.directional_profit is not None and b.directional_profit > 0 for b in backtests)

    summary = EmpiricalCalibrator(
        min_sample_size=1, empirical_threshold=1
    ).calibrate([signal], backtests, horizons=[3, 5])
    metric = _overall_metric(summary, 3)
    assert metric.sample_size == 1
    assert metric.win_rate == 1.0
    assert metric.avg_directional_profit is not None
    assert metric.avg_directional_profit > 0


def test_bearish_outcomes_remain_direction_normalized_end_to_end():
    """Bearish raw returns are negative while directional trade outcomes stay positive."""
    series = _series("sh600519", "SSE", rising=False)
    klines = series.to_ohlcv_list()
    signal_index = 60
    entry = klines[signal_index].close

    # Preserve the falling close path but inject a real 1% adverse intraday spike
    # on the first future bar. This makes bearish MAE exercise its intended
    # negative, direction-normalized semantics instead of a no-adverse-excursion case.
    first_future = klines[signal_index + 1]
    klines[signal_index + 1] = OHLCV(
        date=first_future.date,
        open=first_future.open,
        high=entry * 1.01,
        low=first_future.low,
        close=first_future.close,
        volume=first_future.volume,
    )

    signal = SignalOccurrence(
        signal_id="fir26-bearish",
        stock_code="600519",
        signal_date=klines[signal_index].date,
        pattern_type=PatternType.MA_BEAR,
        entry_price=entry,
        confirmation_state=ConfirmationState.BREAKOUT_DAY,
        market_regime=MarketRegime.BEAR,
        pattern_score=70,
        data_source="sina",
        adjustment="qfq",
        data_quality="OK",
        detector_version="fir26-directional-fixture",
        pattern_confidence="medium",
    )

    result = FixedHorizonBacktester().backtest_signal(
        signal, klines, horizon=3
    )
    assert result.sufficient_data
    assert result.direction == "bearish"
    assert result.forward_return is not None and result.forward_return < 0
    assert result.directional_profit is not None and result.directional_profit > 0
    assert result.win is True
    assert result.mfe is not None and result.mfe > 0
    assert result.mae == pytest.approx(-0.01)

    summary = EmpiricalCalibrator(
        min_sample_size=1, empirical_threshold=1
    ).calibrate([signal], [result], horizons=[3])
    metric = _overall_metric(summary, 3)
    assert metric.win_rate == 1.0
    assert metric.avg_directional_profit is not None and metric.avg_directional_profit > 0
    assert metric.avg_mfe is not None and metric.avg_mfe > 0
    assert metric.avg_mae == pytest.approx(-0.01)


def test_missing_market_data_provenance_stays_unknown():
    """No upstream evidence must never be promoted into apparently-valid provenance."""
    provenance = CalibrationDataCollector._build_provenance(None)
    assert provenance["data_source"] == "unknown"
    assert provenance["adjustment"] == "unknown"
    assert provenance["frequency"] == "unknown"
    assert provenance["trading_calendar"] == "unknown"
    assert provenance["data_quality"]["status"] == "unknown"
