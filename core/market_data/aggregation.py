"""
FIR-23: 日线 -> 周线/月线 确定性聚合

Blocker 4：WEEKLY/MONTHLY 的调整语义不安全问题。
不要直接用 provider 的周/月行（其复权口径与日线可能不一致），
而是先取带正确复权的 DAILY 数据，再在此处确定性派生周/月线。

聚合规则：
  - open  = 区间第一根（周一/月初）open
  - high  = 区间最高
  - low   = 区间最低
  - close = 区间最后一根（周五/月末）close
  - volume= 区间成交量之和
  - 标签日期 = 区间最后一个交易日
"""

from datetime import datetime
from typing import List
from ..types import MarketDataBar, FrequencyType


def _period_key(bar_date: str, frequency: FrequencyType):
    d = datetime.strptime(bar_date, "%Y-%m-%d").date()
    if frequency == FrequencyType.WEEKLY:
        # ISO 周：(iso_year, iso_week)
        return d.isocalendar()[:2]
    # MONTHLY
    return (d.year, d.month)


def aggregate_daily_bars(daily_bars: List[MarketDataBar], frequency: FrequencyType) -> List[MarketDataBar]:
    """把已按日期升序的日线 bars 聚合为周线/月线。

    Args:
        daily_bars: 日线 bars（调用方保证已按日期升序、去重）。
        frequency: WEEKLY 或 MONTHLY（DAILY 原样返回）。

    Returns:
        List[MarketDataBar]: 聚合后的 bars（按区间升序）。
    """
    if frequency == FrequencyType.DAILY:
        return list(daily_bars)

    groups = {}
    order = []
    for bar in daily_bars:
        key = _period_key(bar.date, frequency)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(bar)

    out: List[MarketDataBar] = []
    for key in order:
        grp = groups[key]
        amount_sum = sum(b.amount for b in grp if b.amount is not None)
        out.append(MarketDataBar(
            date=grp[-1].date,           # 标签 = 区间最后交易日
            open=grp[0].open,            # 周一/月初 open
            high=max(b.high for b in grp),
            low=min(b.low for b in grp),
            close=grp[-1].close,         # 周五/月末 close
            volume=sum(b.volume for b in grp),
            amount=amount_sum if amount_sum else None,
            provenance=grp[-1].provenance,
        ))
    return out
