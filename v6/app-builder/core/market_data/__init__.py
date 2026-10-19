"""
core.market_data — FIR-23 市场数据契约实现

提供统一的市场数据获取、校验、复权和交易日历工具。
"""

from .sina_provider import SinaDataProvider
from .tencent_provider import TencentDataProvider
from .multi_source import MultiSourceDataProvider
from .trading_calendar import (
    is_trading_day,
    get_trading_days,
    next_trading_day,
    prev_trading_day,
    count_trading_days,
    derive_calendar_id,
    CALENDAR_SSE,
    CALENDAR_SZSE,
    CALENDAR_BSE,
    CALENDAR_UNKNOWN,
)
from .adjustment import compute_adjustment_factors, apply_adjustment
from .validation import (
    validate_ohlc_invariants, normalize_bars, validate_adjustment_consistency,
    AdjustmentConsistency, assess_series_quality,
)
from .aggregation import aggregate_daily_bars
from .policy import UseCase, DEFAULT_POLICY, adjustment_for

__all__ = [
    "SinaDataProvider",
    "TencentDataProvider",
    "MultiSourceDataProvider",
    "is_trading_day",
    "get_trading_days",
    "next_trading_day",
    "prev_trading_day",
    "count_trading_days",
    "derive_calendar_id",
    "CALENDAR_SSE",
    "CALENDAR_SZSE",
    "CALENDAR_BSE",
    "CALENDAR_UNKNOWN",
    "compute_adjustment_factors",
    "apply_adjustment",
    "validate_ohlc_invariants",
    "normalize_bars",
    "validate_adjustment_consistency",
    "AdjustmentConsistency",
    "assess_series_quality",
    "aggregate_daily_bars",
    "UseCase",
    "DEFAULT_POLICY",
    "adjustment_for",
]
