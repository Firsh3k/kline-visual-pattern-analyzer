"""
FIR-13: Empirical Calibration — 固定 horizon 回测引擎

对每个 SignalOccurrence，计算 T+3/5/10/20/40 交易日的前向收益、MFE、MAE。
数据不足时返回 sufficient_data=False，不缩短窗口、不估算。

核心规则：
- 入场价 = 信号日收盘价（signal.entry_price）
- forward_return = (close_at_horizon - entry_price) / entry_price
- MFE = max((high[t] - entry_price) / entry_price) for t in [signal_idx+1, signal_idx+horizon]
- MAE = min((low[t] - entry_price) / entry_price) for t in [signal_idx+1, signal_idx+horizon]
- win = forward_return > win_threshold (默认 0.0)
- 防 look-ahead：只使用信号日之后的数据计算前向指标
"""

from __future__ import annotations

from typing import List, Optional, Dict

from core.types import OHLCV
from core.calibration.types import (
    SignalOccurrence,
    HorizonBacktest,
    CalibrationDataset,
    DEFAULT_HORIZONS,
    DEFAULT_WIN_THRESHOLD,
)


class FixedHorizonBacktester:
    """固定 horizon 回测引擎 — T+3/5/10/20/40 交易日

    核心规则：
    - 入场价 = 信号日收盘价（signal.entry_price）
    - forward_return = (close_at_horizon - entry_price) / entry_price
    - MFE = max((high[t] - entry_price) / entry_price) for t in [signal_idx+1, signal_idx+horizon]
    - MAE = min((low[t] - entry_price) / entry_price) for t in [signal_idx+1, signal_idx+horizon]
    - win = forward_return > win_threshold (默认 0.0)
    - 数据不足（信号日后不足 horizon 个交易日）→ sufficient_data=False，收益字段=None，不缩短窗口
    """

    def __init__(self, win_threshold: float = DEFAULT_WIN_THRESHOLD):
        """
        Args:
            win_threshold: 胜率阈值，forward_return > threshold 视为 win（默认 0.0 = 盈利即 win）
        """
        self.win_threshold = win_threshold

    def backtest_signal(
        self,
        signal: SignalOccurrence,
        klines: List[OHLCV],
        horizon: int,
    ) -> HorizonBacktest:
        """对单个信号的单个 horizon 进行回测

        Args:
            signal: 信号出现记录（含 signal_date, entry_price, signal_id）
            klines: 该股票的完整K线数据（按日期升序），包含信号日之后的数据
            horizon: 观察窗口交易日数（3/5/10/20/40）

        Returns:
            HorizonBacktest: 回测结果
        """
        if horizon <= 0:
            raise ValueError(f"horizon must be positive, got {horizon}")
        if not klines:
            raise ValueError("klines is empty")

        # 找到 signal_date 在 klines 中的索引（精确匹配日期）
        signal_idx = self._find_signal_index(klines, signal.signal_date)

        entry_price = signal.entry_price

        # 前向窗口：signal_idx+1 到 signal_idx+horizon（不含端点）
        window_start = signal_idx + 1
        window_end = signal_idx + 1 + horizon
        window = klines[window_start:window_end]

        actual_bars = len(window)

        # 数据不足 — 不缩短窗口、不估算
        if actual_bars < horizon:
            return HorizonBacktest(
                signal_id=signal.signal_id,
                horizon=horizon,
                forward_return=None,
                mfe=None,
                mae=None,
                win=None,
                sufficient_data=False,
                actual_bars=actual_bars,
                final_price=None,
            )

        # 数据充足 — 计算前向指标
        # forward_return 使用 horizon 结束时的收盘价
        final_price = window[-1].close
        forward_return = (final_price - entry_price) / entry_price

        # MFE: horizon 内最大有利波动（用 high 价）
        mfe = max((k.high - entry_price) / entry_price for k in window)

        # MAE: horizon 内最大不利波动（用 low 价）
        mae = min((k.low - entry_price) / entry_price for k in window)

        # win 标志
        win = forward_return > self.win_threshold

        return HorizonBacktest(
            signal_id=signal.signal_id,
            horizon=horizon,
            forward_return=forward_return,
            mfe=mfe,
            mae=mae,
            win=win,
            sufficient_data=True,
            actual_bars=actual_bars,
            final_price=final_price,
        )

    def backtest_all_horizons(
        self,
        signal: SignalOccurrence,
        klines: List[OHLCV],
        horizons: Optional[List[int]] = None,
    ) -> List[HorizonBacktest]:
        """对单个信号的所有 horizons 进行回测

        Args:
            signal: 信号出现记录
            klines: 该股票的完整K线数据（按日期升序）
            horizons: 观察窗口列表，默认为 DEFAULT_HORIZONS = (3, 5, 10, 20, 40)

        Returns:
            List[HorizonBacktest]: 每个 horizon 一条回测结果
        """
        if horizons is None:
            horizons = list(DEFAULT_HORIZONS)
        return [self.backtest_signal(signal, klines, h) for h in horizons]

    def backtest_dataset(
        self,
        dataset: CalibrationDataset,
        klines_by_code: Dict[str, List[OHLCV]],
    ) -> List[HorizonBacktest]:
        """对整个数据集的所有信号进行回测

        Args:
            dataset: 校准数据集（含所有信号）
            klines_by_code: 股票代码 → K线列表 的映射

        Returns:
            List[HorizonBacktest]: 所有信号 × 所有 horizon 的回测结果
        """
        results: List[HorizonBacktest] = []
        horizons = list(dataset.horizons)

        for signal in dataset.signals:
            klines = klines_by_code.get(signal.stock_code)
            if klines is None:
                # 该股票无K线数据 — 所有 horizon 都标记为数据不足
                for h in horizons:
                    results.append(HorizonBacktest(
                        signal_id=signal.signal_id,
                        horizon=h,
                        forward_return=None,
                        mfe=None,
                        mae=None,
                        win=None,
                        sufficient_data=False,
                        actual_bars=0,
                        final_price=None,
                    ))
                continue

            results.extend(self.backtest_all_horizons(signal, klines, horizons))

        return results

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _find_signal_index(klines: List[OHLCV], signal_date: str) -> int:
        """在 klines 中精确匹配 signal_date，返回索引

        Raises:
            ValueError: 如果 signal_date 在 klines 中找不到
        """
        for idx, k in enumerate(klines):
            if k.date == signal_date:
                return idx
        raise ValueError(
            f"signal_date '{signal_date}' not found in klines. "
            f"Available dates: {klines[0].date} ~ {klines[-1].date} "
            f"({len(klines)} bars)"
        )
