"""
FIR-13: Empirical Calibration — 经验校准器

按维度分组统计信号的历史胜率，将 heuristic priors 转换为 evidence-backed
probability metrics。每个概率指标必须附带 sample_size，样本不足时返回 N/A。

分组维度：
1. 总体（all, all, all）
2. 按 pattern_type（pattern, all, all）— 8 种形态
3. 按 market_regime（all, regime, all）— 3 种环境
4. 按 confirmation_state（all, all, state）
5. 交叉：pattern_type × market_regime
6. 交叉：pattern_type × confirmation_state

硬约束：
- sample_size < min_sample_size (默认 30) 时，所有概率指标返回 None
- 每个 CalibrationMetric 必须携带 sample_size 和 confidence_label
- 收益率内部一律用小数（0.05 = 5%）
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import List, Optional, Dict, Tuple

from core.calibration.types import (
    PatternType,
    ConfirmationState,
    MarketRegime,
    ConfidenceLabel,
    SignalOccurrence,
    HorizonBacktest,
    CalibrationMetric,
    CalibrationGroup,
    CalibrationSummary,
    DEFAULT_HORIZONS,
    DEFAULT_WIN_THRESHOLD,
    confidence_for_sample_size,
)


class EmpiricalCalibrator:
    """经验校准器 — 按维度分组统计信号的历史胜率

    将 heuristic priors 转换为 evidence-backed probability metrics。
    每个概率指标必须附带 sample_size，样本不足时返回 N/A。
    """

    def __init__(
        self,
        win_threshold: float = DEFAULT_WIN_THRESHOLD,
        min_sample_size: int = 30,
        empirical_threshold: int = 100,
    ):
        self.win_threshold = float(win_threshold)
        self.min_sample_size = int(min_sample_size)
        self.empirical_threshold = int(empirical_threshold)

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    def calibrate(
        self,
        signals: List[SignalOccurrence],
        backtests: List[HorizonBacktest],
        horizons: Optional[List[int]] = None,
    ) -> CalibrationSummary:
        """执行完整校准，生成所有维度的分组统计"""
        if horizons is None:
            horizons = list(DEFAULT_HORIZONS)
        else:
            horizons = list(horizons)

        groups: List[CalibrationGroup] = []

        # 1. 总体
        groups.append(self._build_group(signals, backtests, "all", "all", "all", horizons))

        # 2. 按 pattern_type（8 种形态）
        for pt in PatternType:
            groups.append(
                self._build_group(signals, backtests, pt.value, "all", "all", horizons)
            )

        # 3. 按 market_regime（3 种环境）
        for mr in MarketRegime:
            groups.append(
                self._build_group(signals, backtests, "all", mr.value, "all", horizons)
            )

        # 4. 按 confirmation_state
        for cs in ConfirmationState:
            groups.append(
                self._build_group(signals, backtests, "all", "all", cs.value, horizons)
            )

        # 5. 交叉：pattern_type × market_regime
        for pt in PatternType:
            for mr in MarketRegime:
                groups.append(
                    self._build_group(signals, backtests, pt.value, mr.value, "all", horizons)
                )

        # 6. 交叉：pattern_type × confirmation_state
        for pt in PatternType:
            for cs in ConfirmationState:
                groups.append(
                    self._build_group(signals, backtests, pt.value, "all", cs.value, horizons)
                )

        stock_codes = sorted({s.stock_code for s in signals})
        dates = sorted(s.signal_date for s in signals)
        date_range = (
            f"{dates[0]} ~ {dates[-1]}" if dates else ""
        )

        return CalibrationSummary(
            version="1.0",
            created_at=datetime.now(timezone.utc).isoformat(),
            total_signals=len(signals),
            stock_count=len(stock_codes),
            date_range=date_range,
            groups=groups,
            metadata={
                "win_threshold": self.win_threshold,
                "min_sample_size": self.min_sample_size,
                "empirical_threshold": self.empirical_threshold,
                "horizons": horizons,
            },
        )

    # ------------------------------------------------------------------
    # 内部：分组构建
    # ------------------------------------------------------------------

    def _build_group(
        self,
        signals: List[SignalOccurrence],
        backtests: List[HorizonBacktest],
        pattern_type: str,
        market_regime: str,
        confirmation_state: str,
        horizons: List[int],
    ) -> CalibrationGroup:
        """筛选分组信号/回测，并为每个 horizon 计算指标"""
        g_signals, g_backtests = self._group_signals(
            signals, backtests, pattern_type, market_regime, confirmation_state
        )
        metrics_by_horizon: Dict[int, CalibrationMetric] = {}
        for h in horizons:
            h_backtests = [b for b in g_backtests if b.horizon == h]
            metrics_by_horizon[h] = self._compute_metric(
                h_backtests, h, total_signals=len(g_signals)
            )
        return CalibrationGroup(
            pattern_type=pattern_type,
            market_regime=market_regime,
            confirmation_state=confirmation_state,
            metrics_by_horizon=metrics_by_horizon,
        )

    def _compute_metric(
        self,
        horizon_backtests: List[HorizonBacktest],
        horizon: int,
        total_signals: int,
    ) -> CalibrationMetric:
        """对一组 horizon 回测结果计算统计指标"""
        # 只统计 sufficient_data=True 的样本
        valid = [b for b in horizon_backtests if b.sufficient_data]
        sample_size = len(valid)

        confidence = self._confidence_for(sample_size)

        metric = CalibrationMetric(
            horizon=horizon,
            sample_size=sample_size,
            total_signals=total_signals,
            confidence_label=confidence,
        )

        # 样本不足：所有概率指标保持 None，不编造数字
        if sample_size < self.min_sample_size:
            return metric

        # 前向收益序列（防御性剔除 None）
        returns = [b.forward_return for b in valid if b.forward_return is not None]
        if not returns:
            return metric

        n = len(returns)

        # 胜率：forward_return > win_threshold
        win_count = sum(1 for r in returns if r > self.win_threshold)
        metric.win_rate = win_count / n
        metric.avg_return = statistics.mean(returns)
        metric.median_return = statistics.median(returns)

        # MFE / MAE 平均
        mfes = [b.mfe for b in valid if b.mfe is not None]
        maes = [b.mae for b in valid if b.mae is not None]
        metric.avg_mfe = statistics.mean(mfes) if mfes else None
        metric.avg_mae = statistics.mean(maes) if maes else None

        # 盈亏比：总盈利 / |总亏损|，无亏损时返回 None
        gross_profit = sum(max(0.0, r) for r in returns)
        gross_loss = abs(sum(min(0.0, r) for r in returns))
        if gross_loss > 0:
            metric.profit_factor = gross_profit / gross_loss
        else:
            metric.profit_factor = None

        # 收益波动（样本标准差）
        metric.std_return = statistics.stdev(returns) if n > 1 else 0.0
        metric.max_return = max(returns)
        metric.min_return = min(returns)

        return metric

    def _group_signals(
        self,
        signals: List[SignalOccurrence],
        backtests: List[HorizonBacktest],
        pattern_type: str = "all",
        market_regime: str = "all",
        confirmation_state: str = "all",
    ) -> Tuple[List[SignalOccurrence], List[HorizonBacktest]]:
        """按维度筛选信号和对应的回测结果（"all" 表示不筛选该维度）"""
        filtered_signals: List[SignalOccurrence] = []
        for s in signals:
            if pattern_type != "all" and s.pattern_type.value != pattern_type:
                continue
            if market_regime != "all" and s.market_regime.value != market_regime:
                continue
            if confirmation_state != "all" and s.confirmation_state.value != confirmation_state:
                continue
            filtered_signals.append(s)

        # backtests 通过 signal_id 与 signals 关联
        keep_ids = {s.signal_id for s in filtered_signals}
        filtered_backtests = [b for b in backtests if b.signal_id in keep_ids]

        return filtered_signals, filtered_backtests

    # ------------------------------------------------------------------
    # 内部：置信度
    # ------------------------------------------------------------------

    def _confidence_for(self, sample_size: int) -> ConfidenceLabel:
        """基于实例阈值返回置信度标签（默认与模块常量一致：30/100）"""
        if sample_size < self.min_sample_size:
            return ConfidenceLabel.INSUFFICIENT
        elif sample_size < self.empirical_threshold:
            return ConfidenceLabel.LOW_CONFIDENCE
        else:
            return ConfidenceLabel.EMPIRICAL


__all__ = ["EmpiricalCalibrator"]
