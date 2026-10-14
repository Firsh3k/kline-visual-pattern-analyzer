"""
形态超额收益验证 — 随机入场基准 + 交易成本 + 样本外切分

在 FIR-13 固定 horizon 回测之上回答一个问题：
「某组形态信号的收益，是否高于同期随便买入的收益？扣掉交易成本后还剩多少？
  这个结论在样本外时间段是否依然成立？」

三个基准（均与信号同一 horizon、同一入场日收盘价口径）：
- B1 同日随机股票：信号日在股票池内等权随机买入（= 股票池其余股票同窗口等权平均收益）。
  控制「大盘/股票池同期涨跌」。视为被动持有，不计增量交易成本。
- B2 同股随机日期：同一只股票、同一大盘环境、同一时间段内随机日期买入的平均收益。
  控制「这只股票本身在这段行情里的漂移」，衡量形态的择时价值（双方都交易，成本抵消）。

价格来源：
- 优先使用 FIR-13 采集缓存（data/calibration/*_qfq.json，真实日K）。
- 缓存不可用时，从 calibration_dataset.json 里的入场价/各 horizon 收盘价重建
  每只股票的收盘价序列（按 A 股交易日历定位 T+h），短缺口做对数线性插值。

防 look-ahead / 泄漏：
- 样本外切分按信号日期；样本内信号若 T+h 退出日跨过切分日则剔除（purge）。
- B2 只在同一时间段内取随机日期。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from statistics import median
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ===========================================================================
# 交易成本
# ===========================================================================


@dataclass(frozen=True)
class CostModel:
    """A 股往返交易成本（比例，0.001 = 0.1%）

    默认值：
    - 佣金 万2.5 / 边（忽略 5 元最低佣金）
    - 印花税 0.05%，仅卖出（2023-08-28 起减半后的税率）
    - 过户费 0.001% / 边
    - 滑点 0.10% / 边（收盘价成交的冲击 + 买卖价差假设）
    """
    commission_per_side: float = 0.00025
    stamp_duty_sell: float = 0.0005
    transfer_fee_per_side: float = 0.00001
    slippage_per_side: float = 0.0010

    @property
    def round_trip(self) -> float:
        return (2 * self.commission_per_side + self.stamp_duty_sell
                + 2 * self.transfer_fee_per_side + 2 * self.slippage_per_side)

    def net_return(self, gross: float) -> float:
        """扣费后收益：买入付 (1+c_buy)，卖出得 (1-c_sell)"""
        buy = self.commission_per_side + self.transfer_fee_per_side + self.slippage_per_side
        sell = buy + self.stamp_duty_sell
        return (1 + gross) * (1 - sell) / (1 + buy) - 1


COST_SCENARIOS: Dict[str, CostModel] = {
    "低（无滑点）": CostModel(slippage_per_side=0.0),
    "基准": CostModel(),
    "高（滑点0.25%/边）": CostModel(slippage_per_side=0.0025),
}


# ===========================================================================
# 价格面板
# ===========================================================================


class PricePanel:
    """每只股票的「交易日 → 收盘价」面板，T+h 按各自的交易日序列定位。"""

    def __init__(self, closes: Dict[str, Dict[str, float]],
                 bar_dates: Dict[str, List[str]], source: str,
                 interpolated: Optional[Dict[str, set]] = None):
        self.closes = closes
        self.interpolated = interpolated or {}
        self.bar_dates = bar_dates
        self.source = source
        self._pos = {c: {d: i for i, d in enumerate(ds)} for c, ds in bar_dates.items()}

    @property
    def codes(self) -> List[str]:
        return sorted(self.closes)

    def forward_return(self, code: str, entry_date: str, horizon: int) -> Optional[float]:
        pos = self._pos.get(code, {}).get(entry_date)
        if pos is None:
            return None
        dates = self.bar_dates[code]
        if pos + horizon >= len(dates):
            return None
        p0 = self.closes[code].get(entry_date)
        p1 = self.closes[code].get(dates[pos + horizon])
        if p0 is None or p1 is None or p0 <= 0:
            return None
        return p1 / p0 - 1

    def exit_date(self, code: str, entry_date: str, horizon: int) -> Optional[str]:
        pos = self._pos.get(code, {}).get(entry_date)
        if pos is None or pos + horizon >= len(self.bar_dates[code]):
            return None
        return self.bar_dates[code][pos + horizon]

    @classmethod
    def from_klines(cls, klines_by_code: Dict[str, Sequence]) -> "PricePanel":
        closes, bars = {}, {}
        for code, ks in klines_by_code.items():
            ks = sorted(ks, key=lambda k: k.date)
            bars[code] = [k.date for k in ks]
            closes[code] = {k.date: float(k.close) for k in ks}
        return cls(closes, bars, source="kline_cache")

    @classmethod
    def reconstruct(cls, dataset: dict, trading_days: List[str],
                    max_gap: int = 3, conflict_tol: float = 0.005
                    ) -> Tuple["PricePanel", dict]:
        """从 calibration_dataset 的价格点重建收盘价序列。

        已知点：信号日 entry_price；sufficient_data 回测的 final_price（日期 = 信号日后第 h 个交易日）。
        同一天多个点取中位数；离散度超过 conflict_tol 的点视为冲突（多为停牌导致定位偏移）并丢弃。
        缺口 ≤ max_gap 个交易日时对数线性插值，否则留空。
        """
        idx = {d: i for i, d in enumerate(trading_days)}
        sig = {s["signal_id"]: s for s in dataset["signals"]}
        points: Dict[str, Dict[str, List[float]]] = {}

        def add(code, d, p):
            if p is not None and p > 0:
                points.setdefault(code, {}).setdefault(d, []).append(float(p))

        for s in dataset["signals"]:
            add(s["stock_code"], s["signal_date"], s["entry_price"])
        for b in dataset["backtests"]:
            if not b.get("sufficient_data"):
                continue
            s = sig.get(b["signal_id"])
            if s is None or s["signal_date"] not in idx:
                continue
            j = idx[s["signal_date"]] + int(b["horizon"])
            if j < len(trading_days):
                add(s["stock_code"], trading_days[j], b["final_price"])

        stats = {"points": 0, "conflicts": 0, "known": 0, "interpolated": 0}
        closes: Dict[str, Dict[str, float]] = {}
        bars: Dict[str, List[str]] = {}
        interp: Dict[str, set] = {}
        for code, m in points.items():
            known: Dict[int, float] = {}
            for d, vs in m.items():
                stats["points"] += 1
                if max(vs) / min(vs) - 1 > conflict_tol:
                    stats["conflicts"] += 1
                    continue
                known[idx[d]] = median(vs)
            if not known:
                continue
            ks = sorted(known)
            series: Dict[str, float] = {}
            for a, b in zip(ks, ks[1:] + [None]):
                series[trading_days[a]] = known[a]
                stats["known"] += 1
                if b is None or b - a - 1 > max_gap:
                    continue
                la, lb = math.log(known[a]), math.log(known[b])
                for t in range(a + 1, b):
                    w = (t - a) / (b - a)
                    series[trading_days[t]] = math.exp(la + w * (lb - la))
                    interp.setdefault(code, set()).add(trading_days[t])
                    stats["interpolated"] += 1
            closes[code] = series
            bars[code] = trading_days[ks[0]:ks[-1] + 1]
        return cls(closes, bars, source="reconstructed_from_dataset", interpolated=interp), stats


def a_share_trading_days(start: str, end: str) -> List[str]:
    """A 股交易日（工作日 − 节假日），日历见 core.market_data.trading_calendar"""
    from core.market_data import trading_calendar as tc
    out, x, e = [], date.fromisoformat(start), date.fromisoformat(end)
    while x <= e:
        s = x.isoformat()
        if x.weekday() < 5 and s not in tc._HOLIDAYS:
            out.append(s)
        x += timedelta(days=1)
    return out


# ===========================================================================
# 基准与超额
# ===========================================================================


@dataclass
class TradeRecord:
    stock_code: str
    signal_date: str
    pattern_types: List[str]
    horizon: int
    period: str                      # "in_sample" / "out_of_sample"
    gross_return: float
    b1_same_day_pool: Optional[float]
    b2_same_stock_random_day: Optional[float]


def same_day_pool_return(panel: PricePanel, entry_date: str, horizon: int,
                         exclude: Optional[str] = None) -> Optional[float]:
    """B1：信号日在股票池（排除信号股）等权买入的平均 horizon 收益"""
    rs = [r for c in panel.codes if c != exclude
          for r in [panel.forward_return(c, entry_date, horizon)] if r is not None]
    return sum(rs) / len(rs) if len(rs) >= 10 else None


def same_stock_random_day_return(panel: PricePanel, code: str, candidate_dates: Iterable[str],
                                 horizon: int) -> Optional[float]:
    """B2：同股在候选日期集合（同环境、同时间段）随机入场的期望收益"""
    rs = [r for d in candidate_dates
          for r in [panel.forward_return(code, d, horizon)] if r is not None]
    return sum(rs) / len(rs) if len(rs) >= 5 else None


def split_period(signal_date: str, exit_date: Optional[str],
                 split_date: Optional[str]) -> Optional[str]:
    """按信号日切分；样本内信号退出日跨过切分日 → None（purge）。split_date=None → 全样本"""
    if split_date is None:
        return "all"
    if signal_date >= split_date:
        return "out_of_sample"
    if exit_date is None or exit_date >= split_date:
        return None
    return "in_sample"


@dataclass
class ExcessStats:
    n: int
    n_clusters: int
    gross: float
    net: float
    b1: Optional[float]
    b2: Optional[float]
    excess_b1_gross: Optional[float]
    excess_b1_net: Optional[float]
    excess_b2: Optional[float]
    beat_b1_rate: Optional[float]
    ci_excess_b1_net: Optional[Tuple[float, float]]
    ci_excess_b2: Optional[Tuple[float, float]]

    def to_dict(self) -> dict:
        return asdict(self)


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def cluster_bootstrap_ci(values: List[float], clusters: List[str], n_boot: int = 2000,
                         alpha: float = 0.05, seed: int = 20261014
                         ) -> Optional[Tuple[float, float]]:
    """按簇（自然月）重抽样的均值置信区间。同月信号高度相关，不能当独立样本。"""
    if len(values) < 2:
        return None
    by: Dict[str, List[float]] = {}
    for v, c in zip(values, clusters):
        by.setdefault(c, []).append(v)
    keys = sorted(by)
    if len(keys) < 3:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        tot, cnt = 0.0, 0
        for _k in keys:
            vs = by[keys[rng.randrange(len(keys))]]
            tot += sum(vs)
            cnt += len(vs)
        means.append(tot / cnt)
    means.sort()
    lo = means[int(alpha / 2 * n_boot)]
    hi = means[int((1 - alpha / 2) * n_boot) - 1]
    return lo, hi


def summarize(trades: List[TradeRecord], cost: CostModel) -> Optional[ExcessStats]:
    if not trades:
        return None
    gross = [t.gross_return for t in trades]
    net = [cost.net_return(g) for g in gross]
    with_b1 = [(cost.net_return(t.gross_return), t.gross_return, t.b1_same_day_pool, t.signal_date[:7])
               for t in trades if t.b1_same_day_pool is not None]
    with_b2 = [(t.gross_return - t.b2_same_stock_random_day, t.signal_date[:7])
               for t in trades if t.b2_same_stock_random_day is not None]
    ex_b1_net = [n - b for n, _g, b, _m in with_b1]
    ex_b1_gross = [g - b for _n, g, b, _m in with_b1]
    ex_b2 = [e for e, _m in with_b2]
    return ExcessStats(
        n=len(trades),
        n_clusters=len({t.signal_date[:7] for t in trades}),
        gross=_mean(gross),
        net=_mean(net),
        b1=_mean([b for _n, _g, b, _m in with_b1]),
        b2=_mean([t.b2_same_stock_random_day for t in trades if t.b2_same_stock_random_day is not None]),
        excess_b1_gross=_mean(ex_b1_gross),
        excess_b1_net=_mean(ex_b1_net),
        excess_b2=_mean(ex_b2),
        beat_b1_rate=_mean([1.0 if e > 0 else 0.0 for e in ex_b1_net]),
        ci_excess_b1_net=cluster_bootstrap_ci(ex_b1_net, [m for *_x, m in with_b1]),
        ci_excess_b2=cluster_bootstrap_ci(ex_b2, [m for _e, m in with_b2]),
    )


def build_trades(dataset: dict, panel: PricePanel, patterns: Sequence[str], regime: Optional[str],
                 horizon: int, split_date: Optional[str]) -> Tuple[List[TradeRecord], dict]:
    """筛选目标信号（同股同日多形态合并为一笔交易），计算毛收益与两个基准"""
    bt = {(b["signal_id"], b["horizon"]): b for b in dataset["backtests"]}
    regime_by_date: Dict[str, str] = {}
    for s in dataset["signals"]:
        regime_by_date.setdefault(s["signal_date"], s["market_regime"])

    merged: Dict[Tuple[str, str], dict] = {}
    for s in dataset["signals"]:
        if s["pattern_type"] not in patterns:
            continue
        if regime is not None and s["market_regime"] != regime:
            continue
        b = bt.get((s["signal_id"], horizon))
        if not b or not b.get("sufficient_data"):
            continue
        key = (s["stock_code"], s["signal_date"])
        m = merged.setdefault(key, {"patterns": [], "ret": b["forward_return"]})
        m["patterns"].append(s["pattern_type"])

    stats = {"signals_merged": len(merged), "purged": 0}
    trades: List[TradeRecord] = []
    for (code, d), m in sorted(merged.items(), key=lambda kv: kv[0][1]):
        exit_d = panel.exit_date(code, d, horizon)
        period = split_period(d, exit_d, split_date)
        if period is None:
            stats["purged"] += 1
            continue
        candidates = [x for x, rg in regime_by_date.items()
                      if (regime is None or rg == regime)
                      and (period == "all" or (x >= split_date) == (period == "out_of_sample"))]
        if period == "in_sample":
            candidates = [x for x in candidates
                          if (panel.exit_date(code, x, horizon) or "9999") < split_date]
        trades.append(TradeRecord(
            stock_code=code, signal_date=d, pattern_types=sorted(m["patterns"]),
            horizon=horizon, period=period, gross_return=m["ret"],
            b1_same_day_pool=same_day_pool_return(panel, d, horizon, exclude=code),
            b2_same_stock_random_day=same_stock_random_day_return(panel, code, candidates, horizon),
        ))
    return trades, stats
