"""
市场识别与代码映射 — A股 / 美股统一入口（FIR 美股阶段1，新增模块）

职责（只做"代码 → 市场 / 内部 symbol / 数据源 secid"的确定性路由，不含任何形态算法）：
  - detect_market(code)：区分 A 股（6 位数字）与美股（字母 ticker），无法识别 fail-closed；
  - normalize_code / to_internal_symbol：统一代码口径；
  - eastmoney_secid：美股 ticker → 东方财富 secid（105 纳斯达克 / 106 纽交所）。

设计约束：
  - 本模块是纯新增，不修改 A 股既有 exchange_symbol / _market_prefix 行为；
  - 美股交易所归属采用显式映射表，表外 ticker 直接报错（fail-closed），
    不静默猜测交易所，避免把纽交所股票误标为纳斯达克；新增标的时在 US_EXCHANGE_MAP 补充。

口径：
  - A 股内部 symbol 沿用 sh/sz/bj + 6 位（如 sh600519）；
  - 美股内部 symbol 统一 "us" + 大写 ticker（如 usAAPL），实际取数走东财 secid。
"""
from __future__ import annotations

import re
from typing import Optional

# 市场 canonical 标识
MARKET_CN = "cn"
MARKET_US = "us"

# A 股：6 位纯数字
_CN_CODE_RE = re.compile(r"^\d{6}$")
# 美股：1-6 位大写字母，可选 ".X" 后缀（如 BRK.B / BF.B）
_US_TICKER_RE = re.compile(r"^[A-Z]{1,6}(?:\.[A-Z])?$")

# 东方财富 secid 市场前缀
_EM_NASDAQ = "105"   # NASDAQ 纳斯达克
_EM_NYSE = "106"     # NYSE 纽约证券交易所

# 美股交易所显式映射（ticker -> 东财市场前缀）。
# 首轮验收标的 AAPL / NVDA 均在纳斯达克；同时登记常见纽交所蓝筹，表外一律 fail-closed。
US_EXCHANGE_MAP: dict[str, str] = {
    # 纳斯达克 NASDAQ (105)
    "AAPL": _EM_NASDAQ,
    "NVDA": _EM_NASDAQ,
    "MSFT": _EM_NASDAQ,
    "GOOGL": _EM_NASDAQ,
    "GOOG": _EM_NASDAQ,
    "AMZN": _EM_NASDAQ,
    "META": _EM_NASDAQ,
    "TSLA": _EM_NASDAQ,
    "AVGO": _EM_NASDAQ,
    "COST": _EM_NASDAQ,
    "NFLX": _EM_NASDAQ,
    "AMD": _EM_NASDAQ,
    "INTC": _EM_NASDAQ,
    "QCOM": _EM_NASDAQ,
    "ADBE": _EM_NASDAQ,
    "PEP": _EM_NASDAQ,
    "CSCO": _EM_NASDAQ,
    # 纽交所 NYSE (106)
    "KO": _EM_NYSE,
    "JPM": _EM_NYSE,
    "BRK.B": _EM_NYSE,
    "V": _EM_NYSE,
    "WMT": _EM_NYSE,
    "JNJ": _EM_NYSE,
    "MA": _EM_NYSE,
    "PG": _EM_NYSE,
    "XOM": _EM_NYSE,
    "UNH": _EM_NYSE,
    "HD": _EM_NYSE,
    "DIS": _EM_NYSE,
    "BAC": _EM_NYSE,
    "ORCL": _EM_NYSE,
}


class UnknownCodeError(ValueError):
    """代码无法识别市场 / 美股标的未登记交易所时抛出（fail-closed）。"""


def normalize_us_ticker(code: str) -> str:
    """美股代码归一化为大写；非法字符 fail-closed。"""
    if not isinstance(code, str):
        raise UnknownCodeError(f"美股代码必须是字符串：{code!r}")
    ticker = code.strip().upper()
    if not _US_TICKER_RE.match(ticker):
        raise UnknownCodeError(
            f"美股 ticker 应为 1-6 位字母（可带 .X 后缀）：{code!r}")
    return ticker


def detect_market(code: str) -> str:
    """识别代码所属市场。

    Returns:
        MARKET_CN / MARKET_US

    Raises:
        UnknownCodeError: 既不是 6 位 A 股代码，也不是合法美股 ticker。
    """
    if not isinstance(code, str) or not code.strip():
        raise UnknownCodeError(f"代码为空或类型非法：{code!r}")
    s = code.strip()
    if _CN_CODE_RE.match(s):
        return MARKET_CN
    ticker = s.upper()
    if _US_TICKER_RE.match(ticker):
        return MARKET_US
    raise UnknownCodeError(
        f"无法识别市场（A 股需 6 位数字，美股需字母 ticker）：{code!r}")


def normalize_code(code: str) -> tuple[str, str]:
    """返回 (market, normalized_code)。A 股原样返回，美股转大写。"""
    market = detect_market(code)
    if market == MARKET_CN:
        return market, code.strip()
    return market, normalize_us_ticker(code)


def eastmoney_secid(ticker: str) -> str:
    """美股 ticker → 东财 secid（"105.AAPL" / "106.KO"）。未登记交易所 fail-closed。"""
    t = normalize_us_ticker(ticker)
    prefix = US_EXCHANGE_MAP.get(t)
    if prefix is None:
        raise UnknownCodeError(
            f"美股标的 {t} 未登记交易所归属，拒绝猜测 secid；"
            f"请在 core.market_data.market.US_EXCHANGE_MAP 补充 105(纳斯达克)/106(纽交所)")
    return f"{prefix}.{t}"


def to_internal_symbol(code: str) -> str:
    """统一内部 symbol：A 股 sh/sz/bj+6 位（委托现有 A 股映射）；美股 us+TICKER。"""
    market, normalized = normalize_code(code)
    if market == MARKET_CN:
        # 延迟导入，避免与 A 股模块产生循环依赖
        from .sina_provider import _market_prefix
        prefix = _market_prefix(normalized)
        if prefix is None:
            raise UnknownCodeError(f"无法识别 A 股交易所：{normalized}")
        return f"{prefix}{normalized}"
    return f"us{normalized}"


def is_us_symbol(symbol: Optional[str]) -> bool:
    """判断内部 symbol 是否为美股（us 前缀）。"""
    return bool(symbol) and isinstance(symbol, str) and symbol.startswith("us")
