"""
FIR-23: A股交易日历单元测试
"""

import pytest
from core.market_data.trading_calendar import (
    is_trading_day, get_trading_days, next_trading_day, prev_trading_day,
    count_trading_days,
)


class TestIsTradingDay:
    def test_known_trading_day(self):
        # 2024-01-02 周二，元旦后第一个交易日
        assert is_trading_day("2024-01-02") is True

    def test_known_holiday_newyear(self):
        # 2024-01-01 元旦
        assert is_trading_day("2024-01-01") is False

    def test_weekend_saturday(self):
        # 2024-01-06 周六
        assert is_trading_day("2024-01-06") is False

    def test_weekend_sunday(self):
        # 2024-01-07 周日
        assert is_trading_day("2024-01-07") is False

    def test_spring_festival_2024(self):
        # 2024-02-09 除夕（周五）休市
        assert is_trading_day("2024-02-09") is False

    def test_spring_festival_resume(self):
        # 2024-02-19 周一，春节后开市
        assert is_trading_day("2024-02-19") is True

    def test_makeup_workday_still_closed(self):
        # 2024-02-04 周日（国务院调休上班日），但股市仍休市
        assert is_trading_day("2024-02-04") is False

    def test_national_day_2024(self):
        # 2024-10-01 国庆
        assert is_trading_day("2024-10-01") is False

    def test_national_day_resume(self):
        # 2024-10-08 周二，国庆后开市
        assert is_trading_day("2024-10-08") is True

    def test_mid_autumn_2024(self):
        # 2024-09-16 周一（中秋调休）
        assert is_trading_day("2024-09-16") is False


class TestGetTradingDays:
    def test_short_range(self):
        days = get_trading_days("2024-01-02", "2024-01-05")
        # Tue 1/2, Wed 1/3, Thu 1/4, Fri 1/5
        assert days == ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]

    def test_range_includes_holiday(self):
        # 2024-01-01 to 2024-01-05: skip 1/1 holiday + weekend
        days = get_trading_days("2024-01-01", "2024-01-05")
        assert "2024-01-01" not in days
        assert "2024-01-02" in days

    def test_reversed_range(self):
        assert get_trading_days("2024-01-05", "2024-01-02") == []

    def test_count(self):
        n = count_trading_days("2024-01-02", "2024-01-31")
        # 2024年1月交易日约22天
        assert 15 < n < 25


class TestNextPrevTradingDay:
    def test_next_after_holiday(self):
        assert next_trading_day("2024-01-01") == "2024-01-02"

    def test_next_after_weekend(self):
        # 2024-01-05 Fri -> next is Mon 2024-01-08
        assert next_trading_day("2024-01-05") == "2024-01-08"

    def test_prev_before_holiday(self):
        assert prev_trading_day("2024-01-02") == "2023-12-29"

    def test_prev_after_weekend(self):
        # 2024-01-08 Mon -> prev is Fri 2024-01-05
        assert prev_trading_day("2024-01-08") == "2024-01-05"

    def test_next_spring_festival(self):
        # 2024-02-08 周四（春节前最后交易日）
        assert next_trading_day("2024-02-08") == "2024-02-19"
