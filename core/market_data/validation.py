"""
FIR-23: 数据质量与复权一致性校验

设计原则（fail-closed）：
- 永远不要把 unknown/unverified 编码为 True。无法证明时明确返回 UNVERIFIED。
- OHLC 不变量逐根校验。
- 重复/乱序 K 线确定性归一化（去重 + 强制升序）。
- 缺失日区分「已知原因（停牌）」与「未知原因（provider/数据失败）」，
  后者记入 unknown_missing_days，禁止静默记为 OK。
"""

from enum import Enum
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

from ..types import (
    MarketDataBar, MarketDataSeries, AdjustmentType,
    DataSourceType, DataQualityFlag, DataProvenance,
)


class AdjustmentConsistency(Enum):
    """复权一致性校验结果（三态 + 不适用）。

    - CONSISTENT： positively 验证通过。
    - INCONSISTENT： 发现违反复权不变量。
    - UNVERIFIED： 无法证明（缺少配对的 raw 序列）——不得当作 True。
    - NOT_APPLICABLE： 请求本身就是 NONE（无复权可校验）。
    """
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not_applicable"


# UNVERIFIED 绝不能被解释为「一致」。映射到 DataQualityReport.adjustment_consistent(bool)：
ADJUSTMENT_CONSISTENT_BOOL = {
    AdjustmentConsistency.CONSISTENT: True,
    AdjustmentConsistency.NOT_APPLICABLE: True,   # 无复权，vacuous truth（非 unknown）
    AdjustmentConsistency.INCONSISTENT: False,
    AdjustmentConsistency.UNVERIFIED: False,        # unknown -> 不允许 True
}


def validate_ohlc_invariants(bar: MarketDataBar) -> List[Dict[str, Any]]:
    """校验单根 K 线的 OHLC 不变量，返回违例列表（空=通过）。

    规则：
      - high >= low
      - high >= open 且 high >= close
      - low <= open  且 low <= close
      - volume >= 0
    """
    violations = []
    if bar.high < bar.low:
        violations.append({
            "type": "ohlc_invariant", "date": bar.date,
            "msg": f"high({bar.high}) < low({bar.low})",
        })
    if bar.high < bar.open:
        violations.append({
            "type": "ohlc_invariant", "date": bar.date,
            "msg": f"high({bar.high}) < open({bar.open})",
        })
    if bar.high < bar.close:
        violations.append({
            "type": "ohlc_invariant", "date": bar.date,
            "msg": f"high({bar.high}) < close({bar.close})",
        })
    if bar.low > bar.open:
        violations.append({
            "type": "ohlc_invariant", "date": bar.date,
            "msg": f"low({bar.low}) > open({bar.open})",
        })
    if bar.low > bar.close:
        violations.append({
            "type": "ohlc_invariant", "date": bar.date,
            "msg": f"low({bar.low}) > close({bar.close})",
        })
    if bar.volume < 0:
        violations.append({
            "type": "ohlc_invariant", "date": bar.date,
            "msg": f"volume({bar.volume}) < 0",
        })
    return violations


def normalize_bars(bars: List[MarketDataBar]) -> Tuple[List[MarketDataBar], List[Dict[str, Any]]]:
    """确定性归一化：去重 + 强制按日期升序。

    策略：
      - 同一日期出现多次（重复）：确定性保留最后一根（通常是最新一次拉取覆盖），
        其余丢弃并记 anomaly。
      - 乱序：排序为升序。
    返回 (normalized_bars, anomalies)。
    """
    anomalies: List[Dict[str, Any]] = []
    by_date: Dict[str, MarketDataBar] = {}
    duplicates = 0
    for bar in bars:
        if bar.date in by_date:
            duplicates += 1
            anomalies.append({
                "type": "duplicate_date", "date": bar.date,
                "msg": "duplicate bar for date; keeping the last occurrence",
            })
        by_date[bar.date] = bar  # 后者覆盖前者 -> 确定性保留最后一根

    normalized = sorted(by_date.values(), key=lambda b: b.date)

    # 乱序检测（原始顺序非升序）
    raw_dates = [b.date for b in bars]
    if raw_dates != sorted(raw_dates):
        anomalies.append({
            "type": "unsorted_bars",
            "msg": f"input bars were not ascending; sorted deterministically "
                   f"(duplicates_removed={duplicates})",
        })
    return normalized, anomalies


def validate_adjustment_consistency(
    series: MarketDataSeries,
    raw_closes: Optional[List[float]] = None,
) -> Tuple[AdjustmentConsistency, List[Dict[str, Any]]]:
    """校验复权一致性。

    - adjustment == NONE -> NOT_APPLICABLE。
    - 提供了配对 raw_closes 时，执行真正的检查：
        1) 复权后所有价格非负；
        2) 复权因子 f[t]=adj_close[t]/raw_close[t] 单调不减（hfq 属性）；
        3) 因子相邻日变化除权日外应平滑（>1% 记 anomaly，但不直接判失败）。
    - 请求了 qfq/hfq 但没有配对 raw 序列 -> UNVERIFIED（不得当 True）。
    """
    anomalies: List[Dict[str, Any]] = []

    if series.adjustment == AdjustmentType.NONE:
        return AdjustmentConsistency.NOT_APPLICABLE, anomalies

    adj_closes = [b.close for b in series.bars]

    # 1) 复权后价格非负
    for b in series.bars:
        if b.open < 0 or b.high < 0 or b.low < 0 or b.close < 0:
            anomalies.append({
                "type": "adjustment_invariant", "date": b.date,
                "msg": f"negative adjusted price at {b.date}",
            })
            return AdjustmentConsistency.INCONSISTENT, anomalies

    if raw_closes is None:
        # 无法证明 —— fail-closed
        anomalies.append({
            "type": "adjustment_unverified",
            "msg": (
                f"series is {series.adjustment.value} but no paired raw series was "
                f"provided; adjustment consistency cannot be proven -> UNVERIFIED "
                f"(treated as not-consistent, not True)."
            ),
        })
        return AdjustmentConsistency.UNVERIFIED, anomalies

    if len(raw_closes) != len(adj_closes):
        anomalies.append({
            "type": "adjustment_unverified",
            "msg": f"raw_closes length ({len(raw_closes)}) != adjusted ({len(adj_closes)}); cannot compute factors",
        })
        return AdjustmentConsistency.UNVERIFIED, anomalies

    # 2) 复权因子单调性
    factors: List[float] = []
    for r, a in zip(raw_closes, adj_closes):
        factors.append(a / r if r not in (0, 0.0) else 1.0)

    TOL = 0.01  # 1% 容差（吸收取整误差）
    for i in range(1, len(factors)):
        if factors[i] + TOL < factors[i - 1]:
            anomalies.append({
                "type": "adjustment_invariant",
                "date": series.bars[i].date,
                "msg": (
                    f"adjustment factor decreased non-trivially: "
                    f"{factors[i-1]:.6f} -> {factors[i]:.6f}"
                ),
            })
            return AdjustmentConsistency.INCONSISTENT, anomalies
        # 3) 因子跳变（除权日外应平滑）——只记录，不判失败
        if factors[i - 1] > 0 and abs(factors[i] - factors[i - 1]) / factors[i - 1] > 0.01:
            anomalies.append({
                "type": "adjustment_factor_jump",
                "date": series.bars[i].date,
                "msg": (
                    f"factor jump {factors[i-1]:.6f} -> {factors[i]:.6f} "
                    f"(expected on ex-dividend date only)"
                ),
            })

    return AdjustmentConsistency.CONSISTENT, anomalies


def legacy_manual_provenance(adjustment: AdjustmentType = AdjustmentType.NONE) -> DataProvenance:
    """为 legacy OHLCV -> MarketDataBar 转换构造显式的（可识别的）占位 provenance。

    注意：这是一个 UNTRACEABLE 占位，会被校验标记为 legacy，而不是伪装成真实来源。
    """
    return DataProvenance(
        source=DataSourceType.MANUAL,
        url="",
        fetched_at="",
        raw_payload_hash="",
        adjustment_applied=adjustment,
        quality_flag=DataQualityFlag.OK,
    )


def find_bars_missing_provenance(bars: List[MarketDataBar]) -> List[str]:
    """返回缺少 provenance 的 bar 日期列表。"""
    return [b.date for b in bars if b.provenance is None]


def find_legacy_provenance_bars(bars: List[MarketDataBar]) -> List[str]:
    """返回 provenance 为 legacy 占位（MANUAL + 空 fetched_at）的 bar 日期列表。"""
    out = []
    for b in bars:
        p = b.provenance
        if p is not None and p.source == DataSourceType.MANUAL and p.fetched_at == "":
            out.append(b.date)
    return out


def assess_series_quality(series: MarketDataSeries) -> "DataQualityReport":
    """共享的 DataQualityReport 构造（Blocker 2 & 6）。

    流程：
      1. normalize_bars（去重 + 升序，确定性）。
      2. 逐根 OHLC 不变量校验。
      3. 预期交易日 vs 实际 bars 的缺口 —— 无停牌数据源，全部记为
         unknown_missing_days（原因未知），不静默记为 OK。
      4. 复权一致性：NONE -> NOT_APPLICABLE；qfq/hfq 无配对 raw -> UNVERIFIED(bool=False)。
      5. provenance 缺失/legacy 占位标记。
    """
    # 延迟导入避免循环
    from .trading_calendar import get_trading_days
    from ..types import DataQualityReport

    bars = series.bars
    if not bars:
        return DataQualityReport(
            total_days=0, expected_trading_days=0, actual_bars=0,
            suspended_days=0, missing_days=0, completeness_ratio=0.0,
            adjustment_consistent=True, unknown_missing_days=0,
            anomalies=[{"type": "empty", "msg": "No bars in series"}],
        )

    normalized, norm_anomalies = normalize_bars(bars)
    first_date = normalized[0].date
    last_date = normalized[-1].date

    # FIR-23 P0-2: strict calendar propagation.
    # Previously a broad `except Exception` swallowed CalendarUnverifiedError, turning
    # unverified-year ranges (2027+) into expected=[] / completeness==0 reports —
    # silently defeating the fail-closed trading calendar. Now CalendarUnverifiedError
    # propagates so callers cannot treat an unverified-year series as "complete".
    # Only ValueError (malformed date strings inside the bars themselves) is recovered,
    # recorded as an anomaly, and treated as unknown completeness.
    try:
        expected = get_trading_days(first_date, last_date)
    except ValueError:
        expected = []
        norm_anomalies = list(norm_anomalies) + [{
            "type": "calendar_parse_error",
            "msg": (f"could not resolve trading days between {first_date} and "
                    f"{last_date}; completeness treated as unknown"),
        }]
    actual_dates = {b.date for b in normalized}
    missing = [d for d in expected if d not in actual_dates]
    # 无停牌数据源：所有缺口原因未知（provider/数据失败）
    unknown_missing = len(missing)

    anomalies: List[Dict[str, Any]] = list(norm_anomalies)

    # OHLC 不变量
    for b in normalized:
        anomalies.extend(validate_ohlc_invariants(b))

    # 价格跳变启发式（>20%）
    for i in range(1, len(normalized)):
        prev_close = normalized[i - 1].close
        cur_close = normalized[i].close
        if prev_close > 0:
            pct = abs(cur_close - prev_close) / prev_close
            if pct > 0.20:
                anomalies.append({
                    "type": "price_jump",
                    "date": normalized[i].date,
                    "pct": round(pct, 4),
                    "prev_close": prev_close,
                    "cur_close": cur_close,
                })

    # 复权一致性（Blocker 2）
    adj_state, adj_anomalies = validate_adjustment_consistency(series)
    anomalies.extend(adj_anomalies)
    adj_bool = ADJUSTMENT_CONSISTENT_BOOL[adj_state]

    # provenance 检查（Blocker 5/6）
    missing_prov = find_bars_missing_provenance(normalized)
    legacy_prov = find_legacy_provenance_bars(normalized)
    if missing_prov:
        anomalies.append({"type": "missing_provenance", "dates": missing_prov})
    if legacy_prov:
        anomalies.append({"type": "legacy_provenance", "dates": legacy_prov})

    total_days = (datetime.strptime(last_date, "%Y-%m-%d") -
                  datetime.strptime(first_date, "%Y-%m-%d")).days + 1
    expected_count = len(expected)
    actual_count = len(normalized)
    completeness = actual_count / expected_count if expected_count > 0 else 0.0

    notes_parts = [f"unknown_missing={unknown_missing}", f"adjustment={adj_state.value}"]
    if legacy_prov:
        notes_parts.append(f"legacy_provenance_bars={len(legacy_prov)}")

    return DataQualityReport(
        total_days=total_days,
        expected_trading_days=expected_count,
        actual_bars=actual_count,
        suspended_days=0,               # 无停牌数据源，无法归因
        missing_days=0,                 # 已知原因缺失（停牌）——当前无数据
        completeness_ratio=round(completeness, 4),
        adjustment_consistent=adj_bool,
        unknown_missing_days=unknown_missing,
        anomalies=anomalies,
        notes="; ".join(notes_parts),
    )
