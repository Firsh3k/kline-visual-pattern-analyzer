"""
美股交易日历（NYSE / NASDAQ）— 2020-2026（权威硬编码），2027+ 未核实 fail-closed

规则：
  - 周六、周日休市；
  - 周一至周五落在下列节假日休市安排内的日期休市。
  - NYSE 与 NASDAQ 休市安排一致（is_trading_day 共用），仅日历 provenance 标识不同。

节假日（固定日期节日遇周末按交易所观察日调整：周六→前一交易日，周日→后一交易日）：
  元旦 / 马丁·路德·金纪念日(1月第3个周一) / 总统日(2月第3个周一) /
  耶稣受难日 / 阵亡将士纪念日(5月最后一个周一) / 六月节(6/19，2022起) /
  独立日(7/4) / 劳工节(9月第1个周一) / 感恩节(11月第4个周四) / 圣诞节(12/25)。

权威来源：NYSE 年度市场休市日历（market holidays & trading hours）。
日期由确定性规则脚本生成后硬编码，模块本身无外部依赖，可逐字段审计。

未核实年份（2027 及以后）查询抛 CalendarUnverifiedError，禁止把估算当权威。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List

# 复用 A 股日历的未核实异常语义（assess_series_quality 等下游已识别该类型）
from .trading_calendar import CalendarUnverifiedError  # noqa: F401

VERIFIED_YEARS = frozenset({2020, 2021, 2022, 2023, 2024, 2025, 2026})

# 周一至周五的休市日（周末由 weekday 自动判断）。
# 注意固定节日观察日可能落在上一年（如 2022 元旦观察日为 2021-12-31）。
_HOLIDAYS = {
    # 2020
    "2020-01-01", "2020-01-20", "2020-02-17", "2020-04-10", "2020-05-25",
    "2020-07-03", "2020-09-07", "2020-11-26", "2020-12-25",
    # 2021
    # 2021（Juneteenth 首次成为联邦假日：6/19 周六，观察日 6/18 周五休市）
    "2021-01-01", "2021-01-18", "2021-02-15", "2021-04-02", "2021-05-31",
    "2021-06-18", "2021-07-05", "2021-09-06", "2021-11-25", "2021-12-24",
    # 2022（元旦观察日 2021-12-31）
    "2021-12-31", "2022-01-17", "2022-02-21", "2022-04-15", "2022-05-30",
    "2022-06-20", "2022-07-04", "2022-09-05", "2022-11-24", "2022-12-26",
    # 2023（元旦观察日 2023-01-02）
    "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29",
    "2023-06-19", "2023-07-04", "2023-09-04", "2023-11-23", "2023-12-25",
    # 2024
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
    "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026（独立日观察日 2026-07-03）
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}

# 日历 canonical 标识（NYSE/NASDAQ 休市一致，provenance 需准确区分）
CALENDAR_NYSE = "NYSE"
CALENDAR_NASDAQ = "NASDAQ"


def _parse_date(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


def is_calendar_verified(date_str: str) -> bool:
    """该日期所在年份是否有权威美股日历背书。"""
    return _parse_date(date_str).year in VERIFIED_YEARS


def _require_verified(date_str: str) -> None:
    if not is_calendar_verified(date_str):
        y = _parse_date(date_str).year
        raise CalendarUnverifiedError(
            f"US trading calendar for year {y} is not verified "
            f"(VERIFIED_YEARS={sorted(VERIFIED_YEARS)}). Refusing to return an "
            f"authoritative trading-day classification.")


def is_trading_day(date_str: str) -> bool:
    """判断美股交易日：周一至周五且不在节假日休市列表。"""
    _require_verified(date_str)
    d = _parse_date(date_str)
    if d.weekday() >= 5:
        return False
    if date_str in _HOLIDAYS:
        return False
    return True


def get_trading_days(start: str, end: str) -> List[str]:
    """闭区间内全部美股交易日。"""
    start_d, end_d = _parse_date(start), _parse_date(end)
    if start_d > end_d:
        return []
    out: List[str] = []
    cur = start_d
    while cur <= end_d:
        ds = cur.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            out.append(ds)
        cur += timedelta(days=1)
    return out


def next_trading_day(date_str: str) -> str:
    cur = _parse_date(date_str) + timedelta(days=1)
    while True:
        ds = cur.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            return ds
        cur += timedelta(days=1)


def prev_trading_day(date_str: str) -> str:
    cur = _parse_date(date_str) - timedelta(days=1)
    while True:
        ds = cur.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            return ds
        cur -= timedelta(days=1)


def derive_us_calendar_id(ticker: str) -> str:
    """根据 ticker 交易所归属返回日历 provenance（NASDAQ/NYSE）。"""
    from . import market as M
    secid = M.eastmoney_secid(ticker)          # "105.AAPL" / "106.KO"
    return CALENDAR_NASDAQ if secid.startswith("105.") else CALENDAR_NYSE
