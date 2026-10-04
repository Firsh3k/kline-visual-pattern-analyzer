"""
A股交易日历 — 2020-2026（权威），2027+ 未核实 fail-closed

规则：
- 周六、周日休市（无论是否调休上班）
- 法定节假日休市
- 调休上班的周末仍休市（股市不跟随国务院调休）

权威来源：
- 2020-2025：国务院办公厅年度节假日安排通知（硬编码）。
- 2026：国务院办公厅关于2026年部分节假日安排的通知（国办发明电〔2025〕7号，
  2025-11-04）+ 沪深北交易所《关于2026年部分节假日休市安排的通知》（2025-12-22）。

未核实年份（2027 及以后）不提供权威日历。任何落在未核实年份上的交易日查询
都会抛出 CalendarUnverifiedError（fail-closed），禁止把估算日期伪装成权威安排。
调用方应先用 is_calendar_verified() 软检查，或在拿到交易所正式休市公告后把该年
加入 VERIFIED_YEARS 并补齐 _HOLIDAYS。
"""

from datetime import date, datetime, timedelta
from typing import List


class CalendarUnverifiedError(Exception):
    """交易日历未核实（年份不在 VERIFIED_YEARS）时抛出。

    这是 fail-closed 语义：不允许把未知年份的估算日历当作权威使用。
    """


# 已核实（有官方公告背书）的年份。2027+ 在交易所发布正式休市安排前不在此列。
VERIFIED_YEARS = frozenset({2020, 2021, 2022, 2023, 2024, 2025, 2026})


# 硬编码的休市日（不含周末，周末由 weekday 判断）
# 仅收录周一至周五落在休市安排内的日期；周末由 weekday()>=5 自动休市。
# 来源：国务院办公厅年度节假日安排通知 + 沪深北交易所休市安排。
_HOLIDAYS = {
    # 2020
    "2020-01-01",
    "2020-01-24", "2020-01-27", "2020-01-28", "2020-01-29", "2020-01-30", "2020-01-31",
    "2020-02-03", "2020-02-04",
    "2020-04-06",
    "2020-05-01", "2020-05-04", "2020-05-05",
    "2020-06-25", "2020-06-26",
    "2020-10-01", "2020-10-02", "2020-10-05", "2020-10-06", "2020-10-07", "2020-10-08",
    # 2021
    "2021-01-01",
    "2021-02-11", "2021-02-12", "2021-02-15", "2021-02-16", "2021-02-17",
    "2021-04-05",
    "2021-05-03", "2021-05-04", "2021-05-05",
    "2021-06-14",
    "2021-09-20", "2021-09-21",
    "2021-10-01", "2021-10-04", "2021-10-05", "2021-10-06",
    # 2022
    "2022-01-03",
    "2022-01-31", "2022-02-01", "2022-02-02", "2022-02-03", "2022-02-04",
    "2022-04-04", "2022-04-05",
    "2022-05-02", "2022-05-03", "2022-05-04",
    "2022-06-03",
    "2022-09-12",
    "2022-10-03", "2022-10-04", "2022-10-05", "2022-10-06", "2022-10-07",
    # 2023
    "2023-01-02",
    "2023-01-23", "2023-01-24", "2023-01-25", "2023-01-26", "2023-01-27",
    "2023-04-05",
    "2023-05-01", "2023-05-02", "2023-05-03",
    "2023-06-22", "2023-06-23",
    "2023-09-29",
    "2023-10-02", "2023-10-03", "2023-10-04", "2023-10-05", "2023-10-06",
    # 2024
    "2024-01-01",
    "2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16",
    "2024-04-04", "2024-04-05",
    "2024-05-01", "2024-05-02", "2024-05-03",
    "2024-06-10",
    "2024-09-16", "2024-09-17",
    "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-07",
    # 2025
    "2025-01-01",
    "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-02-03", "2025-02-04",
    "2025-04-04",
    "2025-05-01", "2025-05-02",
    "2025-06-02",
    "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08",
    # 2026（权威：国办发明电〔2025〕7号 + 沪深北交易所 2025-12-22 休市安排）
    # 元旦：1/1(周四)-1/3(周六) 休市；1/4(周日) 调休上班但周末休市。
    "2026-01-01", "2026-01-02",
    # 春节：2/15(周日)-2/23(周一) 休市；2/14(周六)、2/28(周六) 调休上班但周末休市。
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23",
    # 清明：4/4(周六)-4/6(周一) 休市。
    "2026-04-06",
    # 劳动节：5/1(周五)-5/5(周二) 休市；5/9(周六) 调休上班但周末休市。
    "2026-05-01", "2026-05-04", "2026-05-05",
    # 端午：6/19(周五)-6/21(周日) 休市。
    "2026-06-19",
    # 中秋：9/25(周五)-9/27(周日) 休市；9/20(周日) 调休上班但周末休市。
    "2026-09-25",
    # 国庆：10/1(周四)-10/7(周三) 休市；9/20(周日)、10/10(周六) 调休上班但周末休市。
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
}


def _parse_date(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


def is_calendar_verified(date_str: str) -> bool:
    """该日期所在年份是否有权威（官方公告）日历背书。

    未核实年份（2027+）不应被当作权威交易日历使用。
    """
    return _parse_date(date_str).year in VERIFIED_YEARS


def _require_verified(date_str: str) -> None:
    """fail-closed：未核实年份直接抛错，禁止输出权威日历结论。"""
    if not is_calendar_verified(date_str):
        y = _parse_date(date_str).year
        raise CalendarUnverifiedError(
            f"Trading calendar for year {y} is not verified (not in VERIFIED_YEARS="
            f"{sorted(VERIFIED_YEARS)}). Refusing to return an authoritative "
            f"trading-day classification. Use is_calendar_verified() to check, or "
            f"add the year after the exchange publishes its official holiday schedule."
        )


def is_trading_day(date_str: str) -> bool:
    """判断是否为A股交易日

    规则：周一至周五 AND 不在法定节假日休市列表中。
    周末始终休市（即使国务院安排调休上班，股市仍休市）。

    Args:
        date_str: 日期 YYYY-MM-DD

    Returns:
        bool: 是否为交易日

    Raises:
        CalendarUnverifiedError: 日期所在年份未经官方公告核实（fail-closed）。
    """
    _require_verified(date_str)
    d = _parse_date(date_str)
    # 周末休市
    if d.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    # 法定节假日休市
    if date_str in _HOLIDAYS:
        return False
    return True


def get_trading_days(start: str, end: str) -> List[str]:
    """获取 [start, end] 闭区间内的所有交易日

    Raises:
        CalendarUnverifiedError: 区间内任一日期所在年份未核实（fail-closed）。
    """
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    if start_d > end_d:
        return []
    result = []
    cur = start_d
    while cur <= end_d:
        ds = cur.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            result.append(ds)
        cur += timedelta(days=1)
    return result


def next_trading_day(date_str: str) -> str:
    """返回给定日期之后的下一个交易日（不含当日）

    Raises:
        CalendarUnverifiedError: 遍历到的日期所在年份未核实（fail-closed）。
    """
    d = _parse_date(date_str)
    cur = d + timedelta(days=1)
    while True:
        ds = cur.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            return ds
        cur += timedelta(days=1)


def prev_trading_day(date_str: str) -> str:
    """返回给定日期之前的上一个交易日（不含当日）

    Raises:
        CalendarUnverifiedError: 遍历到的日期所在年份未核实（fail-closed）。
    """
    d = _parse_date(date_str)
    cur = d - timedelta(days=1)
    while True:
        ds = cur.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            return ds
        cur -= timedelta(days=1)


def count_trading_days(start: str, end: str) -> int:
    """统计 [start, end] 闭区间内的交易日数量"""
    return len(get_trading_days(start, end))
