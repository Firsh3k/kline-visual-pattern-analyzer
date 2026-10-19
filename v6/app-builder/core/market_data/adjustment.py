"""
复权计算 — 从腾讯 hfq/none 数据反推复权因子

原理：
- 腾讯同时提供不复权(none)和后复权(hfq)数据
- 复权因子 f[t] = hfq_close[t] / raw_close[t]
- 后复权: hfq_price[t] = raw_price[t] * f[t]
- 前复权: qfq_price[t] = raw_price[t] * f[t] / f[latest]
  （以最新交易日为基准，历史价格向下调整）

前复权和后复权的所有 OHLC 价格在同一交易日使用相同因子缩放。
"""

from typing import List, Tuple
from ..types import AdjustmentType


def compute_adjustment_factors(
    raw_closes: List[float],
    hfq_closes: List[float],
) -> List[float]:
    """从 raw 和 hfq 收盘价序列计算每日复权因子

    Args:
        raw_closes: 不复权收盘价序列（升序）
        hfq_closes: 后复权收盘价序列（升序，与 raw 等长对齐）

    Returns:
        List[float]: 每日复权因子 f[t] = hfq_close[t] / raw_close[t]

    Raises:
        ValueError: 两个序列长度不一致或包含零值
    """
    if len(raw_closes) != len(hfq_closes):
        raise ValueError(
            f"raw_closes length ({len(raw_closes)}) != hfq_closes length ({len(hfq_closes)})"
        )
    factors = []
    for r, h in zip(raw_closes, hfq_closes):
        if r == 0:
            factors.append(1.0)
        else:
            factors.append(h / r)
    return factors


def apply_adjustment(
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    factors: List[float],
    target: AdjustmentType,
) -> Tuple[List[float], List[float], List[float], List[float]]:
    """对 OHLC 序列应用复权

    Args:
        opens/highs/lows/closes: 不复权价格序列
        factors: 复权因子序列（来自 compute_adjustment_factors）
        target: 目标复权方式（FORWARD=前复权, BACKWARD=后复权, NONE=原样返回）

    Returns:
        (adj_opens, adj_highs, adj_lows, adj_closes)
    """
    n = len(closes)
    if target == AdjustmentType.NONE:
        return list(opens), list(highs), list(lows), list(closes)

    if target == AdjustmentType.BACKWARD:
        # 后复权: 直接乘以因子
        f = factors
    elif target == AdjustmentType.FORWARD:
        # 前复权: 以最新日为基准归一化
        if not factors:
            return list(opens), list(highs), list(lows), list(closes)
        latest = factors[-1] if factors[-1] != 0 else 1.0
        f = [x / latest for x in factors]
    else:
        return list(opens), list(highs), list(lows), list(closes)

    adj_o = [o * fi for o, fi in zip(opens, f)]
    adj_h = [h * fi for h, fi in zip(highs, f)]
    adj_l = [l * fi for l, fi in zip(lows, f)]
    adj_c = [c * fi for c, fi in zip(closes, f)]
    return adj_o, adj_h, adj_l, adj_c
