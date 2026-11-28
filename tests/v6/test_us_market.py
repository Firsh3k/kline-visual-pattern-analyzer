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

    def test_juneteenth_2021_markets_open(self):
        # 权威口径 NASDAQ ETA2021-45：2021-06-17 才立法，交易所当年不休市，
        # 2021-06-18（周五）与 2021-06-21（周一）全部正常开市。
        # 回归：ee33c4f 曾误把 2021-06-18 标为休市。
        assert uc.is_trading_day("2021-06-18") is True
        assert uc.is_trading_day("2021-06-21") is True

    def test_juneteenth_2022_first_market_closure(self):
        # NASDAQ ETA2022-55：首个 Juneteenth 休市观察日为 2022-06-20（6/19 周日顺延周一）
        assert uc.is_trading_day("2022-06-20") is False
        assert uc.is_trading_day("2022-06-17") is True

    def test_good_friday_2026_closed(self):
        # 2026 Good Friday = 04-03（复活节 04-05 前两个交易日）
        assert uc.is_trading_day("2026-04-03") is False

    def test_black_friday_2026_early_close(self):
        # 2026-11-27 感恩节后周五：交易日，13:00 美东提前收盘
        assert uc.is_trading_day("2026-11-27") is True
        assert uc.is_early_close("2026-11-27") is True
        assert uc.close_time("2026-11-27") == uc.EARLY_CLOSE_ET
        assert uc.close_time("2026-11-27").strftime("%H:%M") == "13:00"

    def test_regular_day_close_16(self):
        assert uc.is_early_close("2026-11-23") is False
        assert uc.close_time("2026-11-23").strftime("%H:%M") == "16:00"
        assert uc.close_time("2026-11-26") is None  # 感恩节休市无收盘时刻

    def test_early_close_never_overlaps_holidays(self):
        assert not (uc._EARLY_CLOSES & uc._HOLIDAYS)
        for d in sorted(uc._EARLY_CLOSES):
            assert uc.is_trading_day(d) is True

    def test_trading_day_counts_2021_2022(self):
        # NYSE 实际：2021=252（无 Juneteenth 休市，且 12-31 正常开市）、2022=251（首个 Juneteenth 休市）
        assert len(uc.get_trading_days("2021-01-01", "2021-12-31")) == 252
        assert len(uc.get_trading_days("2022-01-01", "2022-12-31")) == 251

    def test_new_year_2022_saturday_friday_open(self):
        # 回归：2022 元旦 1/1 周六，NYSE 规则前一周五 2021-12-31 正常开市（不设观察休市）。
        # 此前 _HOLIDAYS 误含 2021-12-31，会拒绝当天有效 K 线。
        assert uc.is_trading_day("2021-12-31") is True
        assert uc.close_time("2021-12-31") == uc.REGULAR_CLOSE_ET
        assert uc.is_trading_day("2022-01-01") is False  # 周六

    def test_carter_mourning_2025_01_09_closed(self):
        # 回归：2025-01-09 卡特总统全国哀悼日，NYSE/NASDAQ 临时休市；前后交易日常开市。
        assert uc.is_trading_day("2025-01-09") is False
        assert uc.close_time("2025-01-09") is None
        assert uc.is_trading_day("2025-01-08") is True
        assert uc.is_trading_day("2025-01-10") is True

    def test_trading_day_count_2025(self):
        # 2025=250（含 01-09 卡特哀悼日休市）
        assert len(uc.get_trading_days("2025-01-01", "2025-12-31")) == 250

    def test_weekend(self):
        assert uc.is_trading_day("2024-01-06") is False
        assert uc.is_trading_day("2024-01-07") is False

    def test_derive_calendar(self):
        assert uc.derive_us_calendar_id("AAPL") == uc.CALENDAR_NASDAQ
        assert uc.derive_us_calendar_id("KO") == uc.CALENDAR_NYSE

    def test_unverified_year_fail_closed(self):
        with pytest.raises(uc.CalendarUnverifiedError):
            uc.is_trading_day("2027-01-04")


# ---------------------------------------------------------------- 提前收盘盘中/收盘状态
class TestEarlyCloseSession:
    """2026-11-27（感恩节后周五）13:00 美东提前收盘：收盘前后状态必须按当日实际收盘时刻判定，
    不得统一按 16:00。对照普通交易日 2026-11-23 的 16:00 边界。"""

    def _ny(self, s):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(s).replace(tzinfo=ZoneInfo("America/New_York"))

    def _info(self, last_date, now_str):
        import core.analysis as A
        return A._calendar_info_us("usAAPL", last_date, self._ny(now_str))

    def test_early_close_day_before_13_is_intraday(self):
        info = self._info("2026-11-27", "2026-11-27T12:30:00")
        assert info["status"] == "verified"
        assert info["early_close"] is True
        assert info["close_time_et"] == "13:00"
        assert info["session_state"] == "intraday"
        assert info["stale"] is False
        # 13:00 前当日未收盘，期望最近收盘日仍是上一交易日（11-26 感恩节休市 → 11-25）
        assert info["expected_trade_date"] == "2026-11-25"

    def test_early_close_day_after_13_is_close_confirmed(self):
        info = self._info("2026-11-27", "2026-11-27T13:30:00")
        assert info["session_state"] == "close_confirmed"
        assert info["expected_trade_date"] == "2026-11-27"
        assert info["stale"] is False

    def test_early_close_day_13_to_16_not_treated_as_intraday(self):
        # 关键回归：13:30 已收盘，若统一按 16:00 会误判 intraday 且把当日数据当未确认
        info = self._info("2026-11-25", "2026-11-27T14:00:00")
        assert info["session_state"] == "close_confirmed"
        assert info["stale"] is True  # 已收盘但数据还停在 11-25

    def test_regular_day_16_boundary(self):
        before = self._info("2026-11-23", "2026-11-23T15:30:00")
        assert before["early_close"] is False
        assert before["close_time_et"] == "16:00"
        assert before["session_state"] == "intraday"
        after = self._info("2026-11-23", "2026-11-23T16:30:00")
        assert after["session_state"] == "close_confirmed"
        assert after["expected_trade_date"] == "2026-11-23"
        assert after["stale"] is False


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


# ---------------------------------------------------------------- 美股美东日期校验
class TestUSDateValidation:
    """美股 asOf / 最后一根 K 线按美东日期校验；A 股仍按北京时间。

    关键场景：北京时间已跨日、美东尚未跨日时，美东的未来日期必须拒绝。
    北京 2026-11-25 00:30 = 美东 2026-11-24 11:30（EST，UTC-5）。
    """
    from datetime import datetime, timezone, timedelta
    CN = timezone(timedelta(hours=8))
    NOW = datetime(2026, 11, 25, 0, 30, tzinfo=CN)

    def _bars_ending(self, end):
        days = uc.get_trading_days("2025-01-01", end)
        bars = []
        p = 100.0
        for d in days:
            p += 0.15
            bars.append({"day": d, "open": p - 0.2, "high": p + 0.5,
                         "low": p - 0.6, "close": p, "volume": 1e7})
        return bars

    def test_us_asof_et_future_rejected_cn_today_accepted(self):
        import core.analysis as A
        assert A.today_cn(self.NOW) == "2026-11-25"
        assert A.today_us(self.NOW) == "2026-11-24"
        # 11-25 是北京今天、美东未来 → 美股拒绝，A 股放行
        with pytest.raises(A.InvalidInputError, match="美东时间"):
            A.parse_as_of("2026-11-25", self.NOW, market="us")
        assert A.parse_as_of("2026-11-25", self.NOW, market="cn") == "2026-11-25"
        assert A.parse_as_of("2026-11-24", self.NOW, market="us") == "2026-11-24"

    def test_us_last_bar_et_future_rejected(self):
        import core.analysis as A
        bars = self._bars_ending("2026-11-25")  # 美东未来（美东今天 11-24）
        with pytest.raises(A.InvalidInputError, match="晚于当前日期"):
            A.analyze("AAPL", bars, adjustment="qfq",
                      provenance={"source": "eastmoney"}, now=self.NOW)

    def test_us_last_bar_et_today_accepted(self):
        import core.analysis as A
        bars = self._bars_ending("2026-11-24")  # 美东今天
        doc = A.analyze("AAPL", bars, adjustment="qfq",
                        provenance={"source": "eastmoney"}, now=self.NOW)
        assert doc["bars"][-1][0] == "2026-11-24"

    def test_cn_last_bar_uses_beijing_not_et(self):
        import core.analysis as A
        from core.market_data import trading_calendar as tc
        days = tc.get_trading_days("2025-01-01", "2026-11-25")
        bars = []
        p = 100.0
        for d in days:
            p += 0.15
            bars.append({"day": d, "open": p - 0.2, "high": p + 0.5,
                         "low": p - 0.6, "close": p, "volume": 1e7})
        # A 股最后一根 11-25（北京今天）放行，不受美东未跨日影响
        doc = A.analyze("600519", bars, adjustment="qfq",
                        provenance={"source": "tencent"}, now=self.NOW)
        assert doc["bars"][-1][0] == "2026-11-25"


# ---------------------------------------------------------------- 执行器首次 asOf 校验（取数前）
class TestExecutorAsOfGate:
    """FIR-47 复核补丁：python_analyzer.analyze_stock 的首次 asOf 校验必须按市场时区。

    场景：北京 2026-11-25 00:30 = 美东 2026-11-24 11:30。直接调用执行器分析 AAPL
    asOf=2026-11-25（北京今天、美东未来）→ 必须在调用行情提供方【之前】拒绝，取数调用为零。
    """

    def _freeze_cross_day(self, monkeypatch):
        import core.analysis as A
        monkeypatch.setattr(A, "today_cn", lambda now=None: "2026-11-25")
        monkeypatch.setattr(A, "today_us", lambda now=None: "2026-11-24")

    def test_us_asof_et_future_rejected_before_any_fetch(self, monkeypatch):
        import python_analyzer as P
        from core.analysis import InvalidInputError
        self._freeze_cross_day(monkeypatch)
        calls = []

        def spy_fetch(code, symbol, adjustment, as_of=None):
            calls.append((code, symbol, as_of))
            raise AssertionError("不应触达行情提供方")

        monkeypatch.setattr(P, "_fetch_series_for", spy_fetch)
        monkeypatch.delenv("KLINE_FIXTURE_DIR", raising=False)
        with pytest.raises(InvalidInputError, match="美东时间"):
            P.analyze_stock("AAPL", as_of="2026-11-25")
        assert calls == [], f"未来回测在取数前被拒，取数调用必须为零，实际 {calls}"

    def test_us_asof_et_today_passes_gate_and_fetches_once(self, monkeypatch):
        import python_analyzer as P
        self._freeze_cross_day(monkeypatch)
        calls = []

        def spy_fetch(code, symbol, adjustment, as_of=None):
            calls.append((code, symbol, as_of))
            raise RuntimeError("sentinel：已通过首次校验并进入取数")

        monkeypatch.setattr(P, "_fetch_series_for", spy_fetch)
        monkeypatch.delenv("KLINE_FIXTURE_DIR", raising=False)
        with pytest.raises(RuntimeError, match="行情获取失败（fail-closed，未降级）"):
            P.analyze_stock("AAPL", as_of="2026-11-24")
        assert calls == [("AAPL", "usAAPL", "2026-11-24")]

    def test_cn_asof_cn_today_not_blocked_by_et(self, monkeypatch):
        import python_analyzer as P
        self._freeze_cross_day(monkeypatch)
        calls = []

        def spy_fetch(code, symbol, adjustment, as_of=None):
            calls.append((code, symbol, as_of))
            raise RuntimeError("sentinel：已通过首次校验并进入取数")

        monkeypatch.setattr(P, "_fetch_series_for", spy_fetch)
        monkeypatch.delenv("KLINE_FIXTURE_DIR", raising=False)
        # A 股 11-25（北京今天）不受美东未跨日影响，仍按北京时间放行
        with pytest.raises(RuntimeError, match="行情获取失败（fail-closed，未降级）"):
            P.analyze_stock("600519", as_of="2026-11-25")
        assert len(calls) == 1 and calls[0][2] == "2026-11-25"


# ---------------------------------------------------------------- fail-closed 边界
class TestFailClosed:
    def test_invalid_code_rejected_with_clear_message(self):
        import python_analyzer as P
        from core.analysis import InvalidInputError
        with pytest.raises(InvalidInputError, match="无法识别市场"):
            P.analyze_stock("ABC123")

    def test_unknown_ticker_outside_secid_table_fail_closed(self):
        # 格式合法但不在东财 secid 映射表内 → fail-closed，不降级
        import python_analyzer as P
        with pytest.raises(RuntimeError, match="行情获取失败（fail-closed，未降级）"):
            P.analyze_stock("ZZZZZ")

    def test_upstream_failure_fail_closed_no_fallback(self, monkeypatch):
        import python_analyzer as P
        from core.interfaces import MarketDataError

        def boom(code, symbol, adjustment, as_of=None):
            raise MarketDataError("东方财富全部主机不可用（usAAPL qfq）")

        monkeypatch.setattr(P, "_fetch_series_for", boom)
        with pytest.raises(RuntimeError, match="行情获取失败（fail-closed，未降级）"):
            P.analyze_stock("AAPL")


# ---------------------------------------------------------------- 东财 provider（mock HTTP，确定性）
class TestEastMoneyProvider:
    """开发环境东财不可达（生产出口可达性属部署后冒烟项），
    用 mock _http_get 覆盖解析 / 复权映射 / 子域轮询 / fail-closed。"""

    PAYLOAD = ('{"rc":0,"data":{"code":"AAPL","name":"苹果","klines":['
               '"2026-11-24,340.00,345.00,348.00,338.00,50000000",'
               '"2026-11-25,346.00,349.00,352.00,344.00,60000000"]}}')

    def _provider(self, monkeypatch, handler):
        from core.market_data import eastmoney_provider as E
        monkeypatch.setattr(E, "_http_get", handler)
        return E.EastMoneyDataProvider()

    def test_parse_and_fqt_and_calendar(self, monkeypatch):
        from core import AdjustmentType
        seen = []

        def handler(url, timeout=20):
            seen.append(url)
            return self.PAYLOAD

        p = self._provider(monkeypatch, handler)
        series = p.fetch_klines("usAAPL", adjustment=AdjustmentType.FORWARD)
        assert "fqt=1" in seen[0] and "secid=105.AAPL" in seen[0]
        assert series.name == "苹果"
        assert series.trading_calendar == "NASDAQ"
        assert series.adjustment == AdjustmentType.FORWARD
        b = series.bars[-1]
        # 东财行格式 [date, open, close, high, low, volume]
        assert (b.open, b.close, b.high, b.low, b.volume) == (346.0, 349.0, 352.0, 344.0, 60000000.0)
        import hashlib
        assert b.provenance.raw_payload_hash == hashlib.sha256(self.PAYLOAD.encode()).hexdigest()

    def test_subdomain_rotation(self, monkeypatch):
        from core import AdjustmentType
        calls = []

        def handler(url, timeout=20):
            calls.append(url)
            if len(calls) == 1:
                raise ConnectionError("Remote end closed connection without response")
            return self.PAYLOAD

        p = self._provider(monkeypatch, handler)
        series = p.fetch_klines("usAAPL", adjustment=AdjustmentType.FORWARD)
        assert len(calls) == 2, "首主机失败应轮询下一子域"
        assert series.bars[-1].provenance.url.startswith("https://13.push2his.eastmoney.com")

    def test_all_hosts_fail_closed(self, monkeypatch):
        from core import AdjustmentType
        from core.interfaces import MarketDataError

        def handler(url, timeout=20):
            raise ConnectionError("Remote end closed connection without response")

        p = self._provider(monkeypatch, handler)
        with pytest.raises(MarketDataError, match="全部主机不可用"):
            p.fetch_klines("usAAPL", adjustment=AdjustmentType.FORWARD)

    def test_empty_klines_fail_closed(self, monkeypatch):
        from core import AdjustmentType
        from core.interfaces import MarketDataError

        p = self._provider(monkeypatch, lambda url, timeout=20: '{"rc":0,"data":{"klines":[]}}')
        with pytest.raises(MarketDataError, match="fail-closed"):
            p.fetch_klines("usAAPL", adjustment=AdjustmentType.FORWARD)

    def test_non_us_symbol_rejected(self):
        from core.interfaces import MarketDataError
        from core.market_data.eastmoney_provider import EastMoneyDataProvider
        with pytest.raises(MarketDataError, match="仅服务美股"):
            EastMoneyDataProvider().fetch_klines("sh600519")
