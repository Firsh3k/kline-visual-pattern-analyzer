"""
FIR 美股阶段1 — 确定性单元测试（不访问网络，CI 可重复）。

覆盖：
  - core.market_data.market：市场识别 / 归一化 / 东财 secid / 内部 symbol，非法 fail-closed；
  - core.market_data.us_calendar：交易日数、Good Friday（含 off-by-one 回归）、感恩节、
    黑色周五交易、固定节日观察日、2027+ 未核实 fail-closed；
  - core.analysis：合成美股数据走同一 Core，A 股 exchange_symbol 输出保持不变。

真实东财网络取数属外部依赖，不在此硬闸门内（见 test 末尾的 skip 守卫）。
"""
import os
import sys

APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "v6", "app-builder"))
if APP not in sys.path:
    sys.path.insert(0, APP)

import pytest  # noqa: E402

from core.market_data import market as M  # noqa: E402
from core.market_data import us_calendar as uc  # noqa: E402


# ---------------------------------------------------------------- market
class TestMarket:
    def test_detect_cn(self):
        assert M.detect_market("600519") == "cn"
        assert M.detect_market("002436") == "cn"

    def test_detect_us(self):
        assert M.detect_market("AAPL") == "us"
        assert M.detect_market("nvda") == "us"

    def test_normalize_ticker(self):
        assert M.normalize_us_ticker("aapl") == "AAPL"
        assert M.normalize_us_ticker("  Nvda ") == "NVDA"
        assert M.normalize_code("aapl") == ("us", "AAPL")

    def test_eastmoney_secid(self):
        assert M.eastmoney_secid("AAPL") == "105.AAPL"
        assert M.eastmoney_secid("NVDA") == "105.NVDA"
        assert M.eastmoney_secid("KO") == "106.KO"

    def test_to_internal_symbol(self):
        assert M.to_internal_symbol("600519") == "sh600519"
        assert M.to_internal_symbol("002436") == "sz002436"
        assert M.to_internal_symbol("AAPL") == "usAAPL"

    def test_is_us_symbol(self):
        assert M.is_us_symbol("usAAPL") is True
        assert M.is_us_symbol("sh600519") is False

    @pytest.mark.parametrize("bad", ["", "12345", "ABC123", "TOOLONGSYM", "中文"])
    def test_reject_invalid(self, bad):
        with pytest.raises(M.UnknownCodeError):
            M.detect_market(bad)


# ---------------------------------------------------------------- us_calendar
class TestUSCalendar:
    def test_2024_trading_day_count(self):
        days = uc.get_trading_days("2024-01-01", "2024-12-31")
        assert len(days) == 252

    def test_good_friday_2026_regression(self):
        # off-by-one 回归：2026 Good Friday 是 04-03（周五），04-02 周四应交易
        assert uc.is_trading_day("2026-04-03") is False
        assert uc.is_trading_day("2026-04-02") is True

    def test_all_good_fridays(self):
        from datetime import date, timedelta
        def easter(y):
            a = y % 19; b = y // 100; c = y % 100; d = b // 4; e = b % 4
            f = (b + 8) // 25; g = (b - f + 1) // 3
            h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4
            l = (32 + 2 * e + 2 * i - h - k) % 7
            mm = (a + 11 * h + 22 * l) // 451
            mon = (h + l - 7 * mm + 114) // 31
            dd = ((h + l - 7 * mm + 114) % 31) + 1
            return date(y, mon, dd)
        for y in range(2020, 2027):
            gf = (easter(y) - timedelta(days=2)).isoformat()
            assert uc.is_trading_day(gf) is False

    def test_thanksgiving_closed_black_friday_open(self):
        # 2024 感恩节 11-28（第4周四）休市；黑色周五 11-29 交易
        assert uc.is_trading_day("2024-11-28") is False
        assert uc.is_trading_day("2024-11-29") is True

    def test_juneteenth_2021_first_observation(self):
        # 2021 Juneteenth：6/19 周六，观察日 6/18 周五休市
        assert uc.is_trading_day("2021-06-18") is False

    def test_weekend(self):
        assert uc.is_trading_day("2024-01-06") is False
        assert uc.is_trading_day("2024-01-07") is False

    def test_derive_calendar(self):
        assert uc.derive_us_calendar_id("AAPL") == uc.CALENDAR_NASDAQ
        assert uc.derive_us_calendar_id("KO") == uc.CALENDAR_NYSE

    def test_unverified_year_fail_closed(self):
        with pytest.raises(uc.CalendarUnverifiedError):
            uc.is_trading_day("2027-01-04")


# ---------------------------------------------------------------- analysis 路由
class TestAnalysisRouting:
    def _us_bars(self):
        days = uc.get_trading_days("2025-01-01", "2026-11-24")
        bars = []
        price = 100.0
        for d in days:
            price += 0.15
            bars.append({"day": d, "open": price - 0.2, "high": price + 0.5,
                         "low": price - 0.6, "close": price, "volume": 1e7})
        return bars

    def test_us_analyze_uses_same_core(self):
        import core.analysis as A
        doc = A.analyze("AAPL", self._us_bars(), adjustment="qfq",
                        provenance={"source": "eastmoney", "adjustment": "qfq"})
        assert doc["symbol"] == "usAAPL"
        assert doc["calendar"]["calendar_id"] == "NASDAQ"
        assert doc["calendar"]["status"] == "verified"
        assert doc["session_state"] == "close_confirmed"
        assert doc["input"]["bar_count"] == 250
        assert len(doc["core"]["patterns"]) == 8

    def test_cn_exchange_symbol_unchanged(self):
        import core.analysis as A
        assert A.exchange_symbol("600519") == "sh600519"
        assert A.exchange_symbol("002436") == "sz002436"
