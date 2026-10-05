"""
FIR-25: Exchange calendar provenance — stop labeling SZ/BJ instruments as SSE

验证 derive_calendar_id() 能根据 symbol 正确派生交易所日历标识符，
并确保 MarketDataSeries round-trip 保留正确值。

验收标准：
- SH symbol → SSE
- SZ symbol → SZSE (not SSE)
- BJ symbol → BSE (not SSE)
- 带前缀 (sh/sz/bj) 和纯数字两种格式都支持
- 无法识别 → UNKNOWN (fail-closed, 不静默回退 SSE)
- MarketDataSeries round-trip 保留正确日历标识符
- 交易日历行为无回归
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.market_data.trading_calendar import (  # noqa: E402
    derive_calendar_id,
    CALENDAR_SSE,
    CALENDAR_SZSE,
    CALENDAR_BSE,
    CALENDAR_UNKNOWN,
    is_trading_day,
    get_trading_days,
)
from core.types import (  # noqa: E402
    MarketDataSeries,
    MarketDataBar,
    DataProvenance,
    DataSourceType,
    DataQualityFlag,
    AdjustmentType,
    FrequencyType,
)


# ---------------------------------------------------------------------------
# derive_calendar_id 单元测试
# ---------------------------------------------------------------------------

class TestDeriveCalendarId:
    """根据 symbol 派生交易所日历标识符"""

    # --- 上海 (SSE) ---
    @pytest.mark.parametrize("symbol", [
        "sh600519", "sh601318", "sh603259", "sh605117",
        "sh688981", "sh510300", "sh900901",
        "600519", "601318", "603259", "605117",
        "688981", "510300", "900901",
    ])
    def test_sh_symbols_return_sse(self, symbol):
        """上海股票（带前缀/纯数字）→ SSE"""
        assert derive_calendar_id(symbol) == CALENDAR_SSE

    # --- 深圳 (SZSE) ---
    @pytest.mark.parametrize("symbol", [
        "sz000858", "sz001979", "sz002415", "sz003816",
        "sz300750", "sz301269",
        "000858", "001979", "002415", "003816",
        "300750", "301269",
    ])
    def test_sz_symbols_return_szse_not_sse(self, symbol):
        """深圳股票（带前缀/纯数字）→ SZSE，不是 SSE"""
        result = derive_calendar_id(symbol)
        assert result == CALENDAR_SZSE
        assert result != CALENDAR_SSE  # 核心验收：不能标记为 SSE

    # --- 北京 (BSE) ---
    @pytest.mark.parametrize("symbol", [
        "bj430047", "bj830799", "bj836471", "bj872925",
        "430047", "830799", "836471", "872925",
        # P0: 北交所 920xxx 新代码段（2024-04-22 启用，2025-10-09 全面切换）
        "920211", "920002", "920010",
        "bj920211", "bj920002",
        # 即使前缀错误写成 sh/sz，数字部分 920 也必须识别为 BSE
        "sh920211", "sz920211",
    ])
    def test_bj_symbols_return_bse_not_sse(self, symbol):
        """北京股票（带前缀/纯数字）→ BSE，不是 SSE"""
        result = derive_calendar_id(symbol)
        assert result == CALENDAR_BSE
        assert result != CALENDAR_SSE  # 核心验收：不能标记为 SSE

    # --- 无法识别 → UNKNOWN (fail-closed) ---
    @pytest.mark.parametrize("symbol", [
        "", None, "   ", "abc", "12345",  # 太短
        "700000", "200000",  # 不在 A 股前缀范围
    ])
    def test_unrecognized_symbols_return_unknown(self, symbol):
        """无法识别的 symbol → UNKNOWN，不静默回退到 SSE"""
        result = derive_calendar_id(symbol)
        assert result == CALENDAR_UNKNOWN
        assert result != CALENDAR_SSE  # fail-closed: 不能默认 SSE

    def test_case_insensitive_prefix(self):
        """前缀大小写不敏感"""
        assert derive_calendar_id("SH600519") == CALENDAR_SSE
        assert derive_calendar_id("SZ000858") == CALENDAR_SZSE
        assert derive_calendar_id("BJ430047") == CALENDAR_BSE

    def test_whitespace_stripped(self):
        """前后空白被去除"""
        assert derive_calendar_id("  sh600519  ") == CALENDAR_SSE
        assert derive_calendar_id("  sz000858  ") == CALENDAR_SZSE

    # --- P0 回归: 920xxx vs 900xxx vs 930+ 边界 ---
    @pytest.mark.parametrize("symbol,expected", [
        # 920xxx → BSE（北交所新代码段）
        ("920001", CALENDAR_BSE),
        ("920211", CALENDAR_BSE),
        ("920999", CALENDAR_BSE),
        ("bj920211", CALENDAR_BSE),
        ("sh920211", CALENDAR_BSE),  # 前缀错误但数字部分正确
        ("sz920211", CALENDAR_BSE),  # 前缀错误但数字部分正确
        # 900xxx-919xxx → SSE（上海 B 股，必须保持不变）
        ("900901", CALENDAR_SSE),
        ("900957", CALENDAR_SSE),
        ("sh900901", CALENDAR_SSE),
        ("910000", CALENDAR_SSE),
        ("919999", CALENDAR_SSE),
        # 930xxx+ → UNKNOWN（fail-closed，不默认 SSE）
        ("930000", CALENDAR_UNKNOWN),
        ("950000", CALENDAR_UNKNOWN),
        ("999999", CALENDAR_UNKNOWN),
    ])
    def test_920_bse_vs_900_sse_boundary(self, symbol, expected):
        """P0 回归：920→BSE, 900-919→SSE, 930+→UNKNOWN (fail-closed)"""
        result = derive_calendar_id(symbol)
        assert result == expected, f"{symbol}: expected {expected}, got {result}"
        # 920xxx 绝对不能是 SSE
        digits = "".join(c for c in symbol if c.isdigit())
        if digits.startswith("920"):
            assert result != CALENDAR_SSE
        # 930xxx+ 绝对不能是 SSE（fail-closed）
        if digits >= "930" and not digits.startswith("920"):
            assert result == CALENDAR_UNKNOWN
            assert result != CALENDAR_SSE


# ---------------------------------------------------------------------------
# Sina provider _market_prefix 测试
# ---------------------------------------------------------------------------

class TestSinaMarketPrefix:
    """Sina provider 的 _market_prefix() 必须正确路由 920xxx 到 bj"""

    def test_920_routes_to_bj(self):
        """P0: 920xxx 必须路由到 bj（北交所），不是 sh"""
        from core.market_data.sina_provider import _market_prefix
        assert _market_prefix("920211") == "bj"
        assert _market_prefix("920002") == "bj"
        assert _market_prefix("920999") == "bj"

    def test_900_still_routes_to_sh(self):
        """回归：900xxx 上海 B 股仍然路由到 sh"""
        from core.market_data.sina_provider import _market_prefix
        assert _market_prefix("900901") == "sh"
        assert _market_prefix("900957") == "sh"

    def test_6xx_routes_to_sh(self):
        """6xx 上海主板路由到 sh"""
        from core.market_data.sina_provider import _market_prefix
        assert _market_prefix("600519") == "sh"
        assert _market_prefix("688981") == "sh"

    def test_0xx_3xx_routes_to_sz(self):
        """0xx/3xx 深圳路由到 sz"""
        from core.market_data.sina_provider import _market_prefix
        assert _market_prefix("000858") == "sz"
        assert _market_prefix("300750") == "sz"

    def test_4xx_8xx_routes_to_bj(self):
        """4xx/8xx 北交所历史代码路由到 bj"""
        from core.market_data.sina_provider import _market_prefix
        assert _market_prefix("430047") == "bj"
        assert _market_prefix("830799") == "bj"

    def test_930plus_returns_none_fail_closed(self):
        """930xxx+ 无法识别 → None（fail-closed，不静默回退 sz）"""
        from core.market_data.sina_provider import _market_prefix
        assert _market_prefix("930000") is None
        assert _market_prefix("950000") is None
        assert _market_prefix("999999") is None

    def test_unrecognized_returns_none_fail_closed(self):
        """完全无法识别的代码 → None（fail-closed）"""
        from core.market_data.sina_provider import _market_prefix
        assert _market_prefix("700000") is None
        assert _market_prefix("200000") is None
        assert _market_prefix("abc") is None


# ---------------------------------------------------------------------------
# MarketDataSeries round-trip 测试
# ---------------------------------------------------------------------------

class TestMarketDataSeriesCalendarRoundTrip:
    """MarketDataSeries 序列化/反序列化保留正确的日历标识符"""

    def _make_bar(self, date: str, close: float) -> MarketDataBar:
        return MarketDataBar(
            date=date, open=close - 0.1, high=close + 0.2, low=close - 0.2,
            close=close, volume=1000000.0,
            provenance=DataProvenance(
                source=DataSourceType.TENCENT, url="",
                fetched_at="2026-10-01T15:00:00+08:00",
                raw_payload_hash="sha256:test",
                adjustment_applied=AdjustmentType.FORWARD,
                quality_flag=DataQualityFlag.OK,
            ),
        )

    def _make_series(self, symbol: str, calendar: str) -> MarketDataSeries:
        return MarketDataSeries(
            symbol=symbol, name=symbol, frequency=FrequencyType.DAILY,
            adjustment=AdjustmentType.FORWARD, trading_calendar=calendar,
            source=DataSourceType.TENCENT,
            fetched_at="2026-10-01T15:30:00+08:00",
            bars=[self._make_bar("2026-09-30", 100.0)],
        )

    @pytest.mark.parametrize("symbol,expected_calendar", [
        ("sh600519", CALENDAR_SSE),
        ("sz000858", CALENDAR_SZSE),
        ("bj430047", CALENDAR_BSE),
    ])
    def test_series_roundtrip_preserves_calendar(self, symbol, expected_calendar):
        """MarketDataSeries to_dict/from_dict 保留正确的日历标识符"""
        series = self._make_series(symbol, expected_calendar)
        d = series.to_dict()
        restored = MarketDataSeries.from_dict(d)
        assert restored.trading_calendar == expected_calendar
        assert restored.trading_calendar != CALENDAR_SSE or expected_calendar == CALENDAR_SSE

    def test_sz_series_not_labeled_sse(self):
        """深圳股票的 series 不能标记为 SSE（核心验收）"""
        series = self._make_series("sz000858", derive_calendar_id("sz000858"))
        assert series.trading_calendar == CALENDAR_SZSE
        assert series.trading_calendar != CALENDAR_SSE

    def test_bj_series_not_labeled_sse(self):
        """北京股票的 series 不能标记为 SSE（核心验收）"""
        series = self._make_series("bj430047", derive_calendar_id("bj430047"))
        assert series.trading_calendar == CALENDAR_BSE
        assert series.trading_calendar != CALENDAR_SSE


# ---------------------------------------------------------------------------
# 交易日历行为无回归测试
# ---------------------------------------------------------------------------

class TestTradingCalendarNoRegression:
    """确保 derive_calendar_id 的添加不影响已验证的交易日历行为"""

    def test_2024_2026_trading_days_still_verified(self):
        """2024-2026 年的交易日查询仍然正常（无 CalendarUnverifiedError）"""
        # 2024 年的一个工作日应该是交易日
        assert is_trading_day("2024-06-03") is True  # 周一
        # 2024 年国庆休市
        assert is_trading_day("2024-10-01") is False
        # 2025 年（6月3日周二，避开端午调休）
        assert is_trading_day("2025-06-03") is True
        # 2026 年
        assert is_trading_day("2026-06-01") is True  # 周一

    def test_get_trading_days_2024(self):
        """2024 年 1 月的交易日数量合理"""
        days = get_trading_days("2024-01-01", "2024-01-31")
        # 2024年1月：元旦休1天，春节在2月，所以约22个交易日
        assert 18 <= len(days) <= 23

    def test_unverified_year_still_fail_closed(self):
        """未核实年份（2027+）仍然 fail-closed"""
        from core.market_data.trading_calendar import CalendarUnverifiedError
        with pytest.raises(CalendarUnverifiedError):
            is_trading_day("2027-01-04")

    def test_weekend_always_closed(self):
        """周末始终休市（无论是否调休上班）"""
        assert is_trading_day("2024-09-14") is False  # 周六
        assert is_trading_day("2024-09-15") is False  # 周日
        assert is_trading_day("2025-10-18") is False  # 周六
        assert is_trading_day("2026-05-16") is False  # 周六
