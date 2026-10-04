"""
FIR-12 — Swing Pivot Engine (V5 Phase 1B)

第一套真正进入 Canonical Core 的算法，验证新架构能否解决三端逻辑漂移。

核心设计原则：
1. **确定性** — 相同输入永远产生相同输出，不依赖"视觉上看起来对"
2. **确认机制** — Pivot 只有在右侧有完整确认窗口后才被标记为 confirmed
3. **不可变性** — 已确认的历史 pivot 一旦确认就不可变，即使后续数据变化也不改变
4. **待确认可变性** — 待确认的 pivot（右侧窗口不完整）可能随新数据变化或消失

关键属性（由测试证明）：
- 修改未来右侧 K 线，会影响尚未确认的 pivot，但不能改变已经确认的历史 pivot
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set, Dict, Any

from .types import OHLCV, PivotPoint
from .interfaces import PivotDetector


@dataclass
class PivotUpdateResult:
    """Pivot 更新结果 — 增量更新的返回值"""
    new_confirmed: List[PivotPoint] = field(default_factory=list)  # 本次新增的已确认 pivot
    pending: List[PivotPoint] = field(default_factory=list)         # 当前待确认的 pivot
    all_confirmed: List[PivotPoint] = field(default_factory=list)   # 所有已确认的 pivot（历史 + 新增）

    @property
    def total_confirmed(self) -> int:
        return len(self.all_confirmed)

    @property
    def total_pending(self) -> int:
        return len(self.pending)


class SwingPivotEngine(PivotDetector):
    """
    Swing Pivot 检测引擎 — 有状态、确定性、带确认机制

    与 PILOT-001 (core/pivots.py) 的区别：
    - PILOT-001 是无状态函数，每次重新计算所有 pivot
    - SwingPivotEngine 是有状态引擎，维护已确认 pivot 的不可变历史
    - SwingPivotEngine 区分 confirmed / pending pivot
    - SwingPivotEngine 支持增量更新（新K线到来时只处理新增部分）

    确认机制：
    - 一个 pivot 在索引 i 处，需要左侧 left_bars 根 + 右侧 right_bars 根 K 线确认
    - 如果 i + right_bars < len(klines) - 1（右侧窗口完整），则 confirmed=True
    - 如果 i + right_bars >= len(klines) - 1（右侧窗口不完整），则 confirmed=False
    - 已确认的 pivot 一旦确认就不可变
    """

    def __init__(self, left_bars: int = 5, right_bars: int = 5):
        """
        初始化 Swing Pivot 引擎

        Args:
            left_bars: 左侧确认K线数（必须 >= 1）
            right_bars: 右侧确认K线数（必须 >= 1）
        """
        if not isinstance(left_bars, int) or left_bars < 1:
            raise ValueError(f"left_bars must be a positive integer, got {left_bars!r}")
        if not isinstance(right_bars, int) or right_bars < 1:
            raise ValueError(f"right_bars must be a positive integer, got {right_bars!r}")

        self.left_bars = left_bars
        self.right_bars = right_bars

        # 有状态：已确认的 pivot 历史（不可变）
        self._confirmed_pivots: List[PivotPoint] = []
        self._confirmed_indices: Set[int] = set()  # 快速查找已确认的索引

        # 记录上次处理到的数据长度，用于增量更新
        self._last_data_length: int = 0

    def reset(self) -> None:
        """重置引擎状态，清除所有已确认的 pivot"""
        self._confirmed_pivots = []
        self._confirmed_indices = set()
        self._last_data_length = 0

    @property
    def confirmed_pivots(self) -> List[PivotPoint]:
        """所有已确认的 pivot（不可变历史）"""
        return list(self._confirmed_pivots)  # 返回副本，防止外部修改

    def _is_swing_high(self, highs: List[float], i: int) -> bool:
        """检查索引 i 是否为 swing high"""
        high = highs[i]
        left_window = highs[i - self.left_bars:i]
        right_window = highs[i + 1:i + self.right_bars + 1]

        if not left_window or not right_window:
            return False

        return high > max(left_window) and high > max(right_window)

    def _is_swing_low(self, lows: List[float], i: int) -> bool:
        """检查索引 i 是否为 swing low"""
        low = lows[i]
        left_window = lows[i - self.left_bars:i]
        right_window = lows[i + 1:i + self.right_bars + 1]

        if not left_window or not right_window:
            return False

        return low < min(left_window) and low < min(right_window)

    def _make_pivot(self, klines: List[OHLCV], i: int, pivot_type: str, confirmed: bool) -> PivotPoint:
        """创建 PivotPoint 对象"""
        price = klines[i].high if pivot_type == "swing_high" else klines[i].low
        return PivotPoint(
            index=i,
            date=klines[i].date,
            price=price,
            pivot_type=pivot_type,
            strength=min(self.left_bars, self.right_bars),
            confirmed=confirmed,
        )

    def _scan_range(self, klines: List[OHLCV], start: int, end: int) -> List[PivotPoint]:
        """
        扫描 [start, end) 范围内的 pivot（无状态计算）

        确认机制：
        - 已确认 pivot (confirmed=True)：左侧窗口完整 + 右侧窗口完整
        - 待确认 pivot (confirmed=False)：左侧窗口完整 + 右侧窗口不完整，
          但基于已有右侧数据满足 swing high/low 条件

        Args:
            klines: K线数据
            start: 起始索引（包含）
            end: 结束索引（不包含）

        Returns:
            扫描到的 pivot 列表（已确认 + 待确认）
        """
        n = len(klines)
        # 至少需要左侧窗口 + 1根当前K线 + 1根右侧K线
        if n < self.left_bars + 2:
            return []

        highs = [k.high for k in klines]
        lows = [k.low for k in klines]

        pivots = []
        # 有效范围：i >= left_bars（左侧窗口完整），i < n - 1（至少有1根右侧K线）
        effective_start = max(start, self.left_bars)
        effective_end = min(end, n - 1)

        for i in range(effective_start, effective_end):
            high = highs[i]
            low = lows[i]

            # 左侧窗口（必须完整）
            left_highs = highs[i - self.left_bars:i]
            left_lows = lows[i - self.left_bars:i]

            # 右侧窗口（可能不完整，取已有的部分）
            right_end = min(i + self.right_bars + 1, n)
            right_highs = highs[i + 1:right_end]
            right_lows = lows[i + 1:right_end]

            # 右侧窗口至少需要1根K线
            if not right_highs or not right_lows:
                continue

            # 判断是否满足条件（基于已有的左侧和右侧数据）
            is_swing_high = high > max(left_highs) and high > max(right_highs)
            is_swing_low = low < min(left_lows) and low < min(right_lows)

            # 同时满足 high 和 low 的宽幅K线，保守拒绝为歧义
            if is_swing_high and is_swing_low:
                continue

            # 判断是否已确认：右侧确认窗口是否完整
            # i + right_bars < n - 1 意味着 i 右侧有至少 right_bars 根完整K线
            confirmed = (i + self.right_bars) < (n - 1)

            if is_swing_high:
                pivots.append(self._make_pivot(klines, i, "swing_high", confirmed))
            elif is_swing_low:
                pivots.append(self._make_pivot(klines, i, "swing_low", confirmed))

        return pivots

    def update(self, klines: List[OHLCV]) -> PivotUpdateResult:
        """
        增量更新：处理新到来的K线，确认新的 pivot

        核心属性：
        - 已确认的历史 pivot 不可变，即使后续数据变化也不改变
        - 待确认的 pivot 可能随新数据变化或消失
        - 只处理新增的K线（从 _last_data_length 开始）

        Args:
            klines: 完整的K线数据（包含历史 + 新增）

        Returns:
            PivotUpdateResult: 更新结果（新增已确认 + 当前待确认 + 所有已确认）
        """
        n = len(klines)

        # 至少需要左侧窗口 + 1根当前K线 + 1根右侧K线
        # 注意：对于 pending pivot，不需要完整的 right_bars 窗口
        if n < self.left_bars + 2:
            # 数据不足，返回空结果
            self._last_data_length = n
            return PivotUpdateResult(
                new_confirmed=[],
                pending=[],
                all_confirmed=list(self._confirmed_pivots),
            )

        # 扫描从上次处理位置开始的所有可能 pivot
        # 注意：需要从 _last_data_length - right_bars - 1 开始，
        # 因为之前待确认的 pivot 可能在新数据到来后被确认
        # end 传 n，让 _scan_range 自己决定有效范围（包含 pending）
        scan_start = max(0, self._last_data_length - self.right_bars - 1)
        newly_scanned = self._scan_range(klines, scan_start, n)

        # 分离新增已确认和待确认
        new_confirmed = []
        pending = []

        for pivot in newly_scanned:
            if pivot.confirmed and pivot.index not in self._confirmed_indices:
                # 新确认的 pivot
                self._confirmed_pivots.append(pivot)
                self._confirmed_indices.add(pivot.index)
                new_confirmed.append(pivot)
            elif not pivot.confirmed:
                # 待确认的 pivot
                pending.append(pivot)

        # 按索引排序（确保顺序正确）
        new_confirmed.sort(key=lambda p: p.index)
        pending.sort(key=lambda p: p.index)
        self._confirmed_pivots.sort(key=lambda p: p.index)

        # 更新上次处理位置
        self._last_data_length = n

        return PivotUpdateResult(
            new_confirmed=new_confirmed,
            pending=pending,
            all_confirmed=list(self._confirmed_pivots),
        )

    def detect(
        self,
        klines: List[OHLCV],
        left_bars: Optional[int] = None,
        right_bars: Optional[int] = None,
    ) -> List[PivotPoint]:
        """
        实现 PivotDetector 接口 — 检测 pivot 点

        无状态检测：每次都重新计算所有 pivot（已确认 + 待确认）。
        不修改引擎内部状态。

        Args:
            klines: K线数据列表
            left_bars: 左侧确认K线数（如不提供使用引擎默认值）
            right_bars: 右侧确认K线数（如不提供使用引擎默认值）

        Returns:
            List[PivotPoint]: Pivot点列表（按索引升序，包含 confirmed 和 pending）
        """
        # 临时使用自定义参数（不修改引擎状态）
        old_left = self.left_bars
        old_right = self.right_bars

        if left_bars is not None:
            self.left_bars = left_bars
        if right_bars is not None:
            self.right_bars = right_bars

        try:
            # 无状态扫描整个范围
            result = self._scan_range(klines, 0, len(klines))
            return result
        finally:
            # 恢复引擎默认参数
            self.left_bars = old_left
            self.right_bars = old_right

    def detect_confirmed_only(self, klines: List[OHLCV]) -> List[PivotPoint]:
        """
        只返回已确认的 pivot（无状态检测）

        Args:
            klines: K线数据列表

        Returns:
            List[PivotPoint]: 已确认的 pivot 列表
        """
        all_pivots = self.detect(klines)
        return [p for p in all_pivots if p.confirmed]

    def detect_pending_only(self, klines: List[OHLCV]) -> List[PivotPoint]:
        """
        只返回待确认的 pivot（无状态检测）

        Args:
            klines: K线数据列表

        Returns:
            List[PivotPoint]: 待确认的 pivot 列表
        """
        all_pivots = self.detect(klines)
        return [p for p in all_pivots if not p.confirmed]
