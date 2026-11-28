"""
美股交易日历（NYSE / NASDAQ）— 2020-2026（权威硬编码），2027+ 未核实 fail-closed

规则：
  - 周六、周日休市；
  - 周一至周五落在下列节假日休市安排内的日期休市。
  - NYSE 与 NASDAQ 休市安排一致（is_trading_day 共用），仅日历 provenance 标识不同。

节假日（固定日期节日遇周末按交易所观察日调整：周六→前一交易日，周日→后一交易日）：
  元旦 / 马丁·路德·金纪念日(1月第3个周一) / 总统日(2月第3个周一) /
  耶稣受难日 / 阵亡将士纪念日(5月最后一个周一) / 六月节(6/19，**2022起休市**) /
  独立日(7/4) / 劳工节(9月第1个周一) / 感恩节(11月第4个周四) / 圣诞节(12/25)。

Juneteenth 权威口径（勿再凭联邦假日推断休市）：
  - 2021-06-17 才立法成为联邦假日，交易所当年**不**休市：NASDAQ ETA2021-45 明确
    2021-06-18（周五）与 2021-06-21（周一）全部正常开市；
  - 首个休市观察日为 2022-06-20（6/19 周日顺延周一），见 NASDAQ ETA2022-55。

特别休市 / 观察日规则：
  - 2025-01-09：卡特总统全国哀悼日，NYSE/NASDAQ 临时休市（非周期节日，单独硬编码）。
  - 元旦逢周六：前一周五正常开市（2021-12-31 开市），不设观察休市；
    圣诞节逢周六：前一周五休市（2021-12-24 休市）。两者规则不同，勿混淆。

提前收盘（13:00 美东，NYSE/NASDAQ 一致）：感恩节后周五、7/3（适逢交易日）、
  平安夜 12/24（适逢交易日）。提前收盘日仍是交易日，仅收盘时刻不同，
  见 _EARLY_CLOSES 与 close_time()；盘中/收盘判定必须用 close_time，不得统一按 16:00。

权威来源：NYSE 年度市场休市日历（market holidays & trading hours）、
  NASDAQ ETA2021-45 / ETA2022-55 / Holiday Trading Calendar。
日期由确定性规则脚本生成后硬编码，模块本身无外部依赖，可逐字段审计。

未核实年份（2027 及以后）查询抛 CalendarUnverifiedError，禁止把估算当权威。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List, Optional

# 复用 A 股日历的未核实异常语义（assess_series_quality 等下游已识别该类型）
from .trading_calendar import CalendarUnverifiedError  # noqa: F401

VERIFIED_YEARS = frozenset({2020, 2021, 2022, 2023, 2024, 2025, 2026})

# 周一至周五的休市日（周末由 weekday 自动判断）。
# 固定节日观察日规则（NYSE）：圣诞节逢周六→前一周五休市（如 2021-12-24）；
# 但元旦逢周六→前一周五**正常开市**（如 2021-12-31），不设观察休市。
_HOLIDAYS = {
    # 2020
    "2020-01-01", "2020-01-20", "2020-02-17", "2020-04-10", "2020-05-25",
    "2020-07-03", "2020-09-07", "2020-11-26", "2020-12-25",
    # 2021
    # 2021 Juneteenth：6/17 立法，交易所当年不休市（ETA2021-45：6/18、6/21 正常开市）。
    # 首个休市观察日是 2022-06-20，勿把 2021-06-18 标为休市。
    "2021-01-01", "2021-01-18", "2021-02-15", "2021-04-02", "2021-05-31",
    "2021-07-05", "2021-09-06", "2021-11-25", "2021-12-24",
    # 2022（2022 元旦 1/1 周六：NYSE 规则周五 2021-12-31 正常开市，不设观察休市）
    "2022-01-17", "2022-02-21", "2022-04-15", "2022-05-30",
    "2022-06-20", "2022-07-04", "2022-09-05", "2022-11-24", "2022-12-26",
    # 2023（元旦观察日 2023-01-02）
    "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29",
    "2023-06-19", "2023-07-04", "2023-09-04", "2023-11-23", "2023-12-25",
    # 2024
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
    "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
    # 2025（01-09 卡特总统全国哀悼日，NYSE/NASDAQ 临时休市）
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026（独立日观察日 2026-07-03）
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}

# 日历 canonical 标识（NYSE/NASDAQ 休市一致，provenance 需准确区分）
CALENDAR_NYSE = "NYSE"
CALENDAR_NASDAQ = "NASDAQ"

# 提前收盘日（13:00 美东，NYSE/NASDAQ 一致）：感恩节后周五 / 7-3（适逢交易日）/
# 平安夜 12-24（适逢交易日）。这些日子仍是交易日，不在 _HOLIDAYS 内。
# 2020-07-03、2026-07-03 为独立日观察日全天休市；2021-12-24 为圣诞节观察日全天休市。
_EARLY_CLOSES = {
    "2020-11-27", "2020-12-24",
    "2021-11-26",
    "2022-11-25",
    "2023-07-03", "2023-11-24",
    "2024-07-03", "2024-11-29", "2024-12-24",
    "2025-07-03", "2025-11-28", "2025-12-24",
    "2026-11-27", "2026-12-24",
}

REGULAR_CLOSE_ET = time(16, 0)
EARLY_CLOSE_ET = time(13, 0)


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
    """判断美股交易日：周一至周五且不在节假日休市列表。提前收盘日仍是交易日。"""
    _require_verified(date_str)
    d = _parse_date(date_str)
    if d.weekday() >= 5:
        return False
    if date_str in _HOLIDAYS:
        return False
    return True


def is_early_close(date_str: str) -> bool:
    """该交易日是否 13:00 美东提前收盘（非交易日返回 False）。"""
    _require_verified(date_str)
    return date_str in _EARLY_CLOSES and is_trading_day(date_str)


def close_time(date_str: str) -> Optional[time]:
    """该日美东收盘时刻：正常交易日 16:00，提前收盘日 13:00，非交易日 None。
    盘中/收盘判定必须用本函数，不得统一按 16:00。未核实年份 fail-closed。"""
    if not is_trading_day(date_str):
        return None
    return EARLY_CLOSE_ET if date_str in _EARLY_CLOSES else REGULAR_CLOSE_ET


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
