"""LiveProvider.index_daily：大盘只走腾讯，且拒绝不合理的指数收盘价（实测新浪会返回个股 000001）。"""
import pytest

from app import providers as P
from app.errors import UpstreamUnavailable
from core.types import AdjustmentType, DataSourceType, FrequencyType, MarketDataBar, MarketDataSeries


def _series(close):
    bars = [MarketDataBar(date="2026-10-14", open=close, high=close, low=close, close=close, volume=1.0, provenance=None)]
    return MarketDataSeries(symbol="sh000001", name="sh000001", frequency=FrequencyType.DAILY,
                            adjustment=AdjustmentType.NONE, trading_calendar="SSE", source=DataSourceType.TENCENT,
                            fetched_at="2026-10-14T08:00:00Z", bars=bars)


def _patch(monkeypatch, close, calls):
    import core.market_data.tencent_provider as T

    class Fake:
        def fetch_klines(self, symbol, **kw):
            calls.append(symbol)
            return _series(close)
    monkeypatch.setattr(T, "TencentDataProvider", Fake)


def test_index_uses_tencent(monkeypatch):
    calls = []
    _patch(monkeypatch, 3767.23, calls)
    f = P.LiveProvider().index_daily()
    assert calls == ["sh000001"] and f.bars[-1]["close"] == 3767.23 and f.provenance["source"] == "tencent"


def test_index_rejects_stock_price(monkeypatch):
    _patch(monkeypatch, 11.5, [])
    with pytest.raises(UpstreamUnavailable, match="不合理"):
        P.LiveProvider().index_daily()
