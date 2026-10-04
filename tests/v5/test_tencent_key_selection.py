"""
FIR-23 P0-1: deterministic Tencent kline key-selection tests (no network).

We patch ``core.market_data.tencent_provider._http_get`` to return canned Tencent
``fqkline/get`` payloads, then assert the strict adjustment -> key mapping is
enforced:

  adjustment == NONE     -> only accepts raw "day"
  adjustment == FORWARD  -> only accepts "qfqday"
  adjustment == BACKWARD -> only accepts "hfqday"

Crucially, when qfq/hfq is requested but the response omits the adjusted key and
only provides raw ``day``, the provider MUST raise MarketDataError — it must NOT
silently return raw prices mislabeled as adjusted.

Tencent kline row layout (note close comes before high/low):
    [date, open, close, high, low, volume]
"""

import json
from unittest.mock import patch

import pytest

from core.interfaces import MarketDataError
from core.market_data.tencent_provider import TencentDataProvider
from core import AdjustmentType


SYMBOL = "sh600519"


def _payload(symbol, **series):
    """Build a Tencent fqkline/get response. ``series`` maps key -> list of rows."""
    return json.dumps({"code": 0, "data": {symbol: dict(series)}})


# qfqday row: open=10, close=10.5, high=10.8, low=9.8, vol=1000
_QFQ_ROW = ["2026-01-05", "10", "10.5", "10.8", "9.8", "1000"]
# raw day row: open=20, close=20.5, high=20.8, low=19.8, vol=2000
_DAY_ROW = ["2026-01-05", "20", "20.5", "20.8", "19.8", "2000"]
# hfqday row: open=30, close=30.5, high=30.8, low=29.8, vol=3000
_HFQ_ROW = ["2026-01-05", "30", "30.5", "30.8", "29.8", "3000"]


class TestTencentKeySelection:
    """Strict key selection: qfq/hfq/none each bind to exactly one key."""

    @patch("core.market_data.tencent_provider._http_get")
    def test_qfq_requires_qfqday_key(self, mock_get):
        # Response only has raw `day`, no qfqday. Requesting FORWARD must raise —
        # no silent fallback to raw prices.
        mock_get.return_value = _payload(SYMBOL, day=[_DAY_ROW])
        provider = TencentDataProvider()
        with pytest.raises(MarketDataError) as exc_info:
            provider.fetch_klines(symbol=SYMBOL, adjustment=AdjustmentType.FORWARD)
        msg = str(exc_info.value)
        assert "qfqday" in msg
        assert "qfq" in msg

    @patch("core.market_data.tencent_provider._http_get")
    def test_hfq_requires_hfqday_key(self, mock_get):
        # Response only has raw `day`, no hfqday. Requesting BACKWARD must raise.
        mock_get.return_value = _payload(SYMBOL, day=[_DAY_ROW])
        provider = TencentDataProvider()
        with pytest.raises(MarketDataError) as exc_info:
            provider.fetch_klines(symbol=SYMBOL, adjustment=AdjustmentType.BACKWARD)
        assert "hfqday" in str(exc_info.value)

    @patch("core.market_data.tencent_provider._http_get")
    def test_qfq_happy_path_selects_qfqday(self, mock_get):
        # Both qfqday and day present; FORWARD must pick qfqday (close=10.5),
        # NOT raw day (close=20.5).
        mock_get.return_value = _payload(SYMBOL, qfqday=[_QFQ_ROW], day=[_DAY_ROW])
        provider = TencentDataProvider()
        series = provider.fetch_klines(symbol=SYMBOL, adjustment=AdjustmentType.FORWARD)
        assert len(series.bars) == 1
        assert series.bars[0].close == 10.5
        assert series.bars[0].open == 10.0

    @patch("core.market_data.tencent_provider._http_get")
    def test_hfq_happy_path_selects_hfqday(self, mock_get):
        # Both hfqday and day present; BACKWARD must pick hfqday (close=30.5),
        # NOT raw day (close=20.5).
        mock_get.return_value = _payload(SYMBOL, hfqday=[_HFQ_ROW], day=[_DAY_ROW])
        provider = TencentDataProvider()
        series = provider.fetch_klines(symbol=SYMBOL, adjustment=AdjustmentType.BACKWARD)
        assert len(series.bars) == 1
        assert series.bars[0].close == 30.5
        assert series.bars[0].open == 30.0

    @patch("core.market_data.tencent_provider._http_get")
    def test_none_adjustment_selects_day(self, mock_get):
        # NONE must use raw `day` (close=20.5), even when qfqday is also present
        # (otherwise an adjusted series would be silently consumed as raw).
        mock_get.return_value = _payload(SYMBOL, qfqday=[_QFQ_ROW], day=[_DAY_ROW])
        provider = TencentDataProvider()
        series = provider.fetch_klines(symbol=SYMBOL, adjustment=AdjustmentType.NONE)
        assert len(series.bars) == 1
        assert series.bars[0].close == 20.5
        assert series.bars[0].open == 20.0

    @patch("core.market_data.tencent_provider._http_get")
    def test_no_keys_at_all_raises(self, mock_get):
        # sym_data is empty {} -> no kline key for any adjustment -> raise.
        mock_get.return_value = _payload(SYMBOL)  # no series keys at all
        provider = TencentDataProvider()
        with pytest.raises(MarketDataError):
            provider.fetch_klines(symbol=SYMBOL, adjustment=AdjustmentType.NONE)
