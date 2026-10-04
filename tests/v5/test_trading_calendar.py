"""
FIR-23: A股交易日历单元测试
"""

import pytest
from core.market_data.trading_calendar import (
    is_trading_day, get_trading_days, next_trading_day, prev_trading_day,
    count_trading_days, is_calendar_verified, CalendarUnverifiedError,
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

class TestTradingCalendar2026:
    """2026 年权威日历回归测试（国办发明电〔2025〕7号 + 沪深北交易所 2025-12-22）"""

    # ---- 元旦：1/1(周四)-1/3(周六) 休市；1/4(周日) 调休上班但周末休市 ----
    def test_new_year_2026_jan1_closed(self):
        assert is_trading_day("2026-01-01") is False

    def test_new_year_2026_jan2_closed(self):
        # 之前遗漏的工作日休市日
        assert is_trading_day("2026-01-02") is False

    def test_new_year_2026_jan5_open(self):
        # 1/5(周一) 起照常开市
        assert is_trading_day("2026-01-05") is True

    # ---- 春节：2/15(周日)-2/23(周一) 休市；2/24(周二) 开市 ----
    def test_spring_festival_2026_weekday_closed(self):
        for d in ["2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20"]:
            assert is_trading_day(d) is False, d

    def test_spring_festival_2026_feb23_closed(self):
        # 之前遗漏：2/23(周一) 仍在春节休市内
        assert is_trading_day("2026-02-23") is False

    def test_spring_festival_2026_feb24_open(self):
        assert is_trading_day("2026-02-24") is True

    def test_spring_makeup_saturday_closed(self):
        # 2/28(周六) 国务院调休上班，但股市周末休市
        assert is_trading_day("2026-02-28") is False

    # ---- 清明节：4/4(周六)-4/6(周一) ----
    def test_qingming_2026_apr6_closed(self):
        assert is_trading_day("2026-04-06") is False

    def test_qingming_2026_apr7_open(self):
        assert is_trading_day("2026-04-07") is True

    # ---- 劳动节：5/1(周五)-5/5(周二)；5/6(周三) 开市 ----
    def test_labor_day_2026_may1_closed(self):
        assert is_trading_day("2026-05-01") is False

    def test_labor_day_2026_may4_may5_closed(self):
        # 之前遗漏的两个工作日休市日
        assert is_trading_day("2026-05-04") is False
        assert is_trading_day("2026-05-05") is False

    def test_labor_day_2026_may6_open(self):
        assert is_trading_day("2026-05-06") is True

    # ---- 端午节：6/19(周五)-6/21(周日) ----
    def test_dragon_boat_2026_jun19_closed(self):
        assert is_trading_day("2026-06-19") is False

    def test_dragon_boat_2026_jun22_open(self):
        assert is_trading_day("2026-06-22") is True

    # ---- 中秋节：9/25(周五)-9/27(周日) ----
    def test_mid_autumn_2026_sep25_closed(self):
        assert is_trading_day("2026-09-25") is False

    def test_mid_autumn_2026_sep28_open(self):
        assert is_trading_day("2026-09-28") is True

    # ---- 国庆节：10/1(周四)-10/7(周三)；10/8(周四) 开市 ----
    def test_national_day_2026_weekdays_closed(self):
        for d in ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"]:
            assert is_trading_day(d) is False, d

    def test_national_day_2026_oct8_open(self):
        assert is_trading_day("2026-10-08") is True

    def test_oct10_makeup_saturday_closed(self):
        # 10/10(周六) 调休上班，周末休市
        assert is_trading_day("2026-10-10") is False


class TestUnverifiedYears:
    """未核实年份（2027+）必须 fail-closed，不能伪装成权威日历"""

    def test_verified_years_set(self):
        assert is_calendar_verified("2026-06-01") is True
        assert is_calendar_verified("2024-06-01") is True

    def test_2027_is_unverified(self):
        assert is_calendar_verified("2027-01-04") is False

    def test_is_trading_day_raises_on_unverified_year(self):
        with pytest.raises(CalendarUnverifiedError):
            is_trading_day("2027-01-04")

    def test_get_trading_days_raises_when_touching_unverified_year(self):
        with pytest.raises(CalendarUnverifiedError):
            get_trading_days("2026-12-28", "2027-01-08")

    def test_next_trading_day_raises_on_unverified(self):
        with pytest.raises(CalendarUnverifiedError):
            next_trading_day("2026-12-31")
