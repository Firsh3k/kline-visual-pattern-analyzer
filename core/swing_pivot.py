"""
FIR-12 — Swing Pivot Engine (V5 Phase 1B)

第一套真正进入 Canonical Core 的算法，验证新架构能否解决三端逻辑漂移。

核心设计原则：
1. **确定性** — 相同输入永远产生相同输出，不依赖"视觉上看起来对"
2. **确认机制** — Pivot 只有在右侧有完整确认窗口后才被标记为 confirmed
3. **不可变性** — 已确认的历史 pivot 一旦确认就不可变，即使后续数据变化也不改变
4. **待确认可变性** — 待确认的 pivot（右侧窗口不完整）可能随新数据变化或消失
5. **严格 append-only contract** — update() 要求历史数据不可变，否则 raise HistoricalDataMutationError

关键属性（由测试证明）：
- 修改未来右侧 K 线，会影响尚未确认的 pivot，但不能改变已经确认的历史 pivot
- 已确认 pivot 携带 confirmed_at_index/date，支持 FIR-13 无未来函数回测

Version 语义说明：
- core.__version__ = "5.1.0" 是 Canonical Core internal/API version
- 不代表 GitHub product release 已经达到 V5.1.0
- 正式产品 Release 版本由 Git tag / GitHub Release 决定（如 v4.0.0）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set, Dict, Any, Tuple

from .types import OHLCV, PivotPoint
from .interfaces import PivotDetector


class HistoricalDataMutationError(Exception):
    """
    历史数据被修改/截断时抛出。

    当 update() 检测到：
    - 新数据长度小于上次处理长度（truncate）
    - 旧历史区间的 OHLC 或 date 发生变化（rewrite）
    时抛出此异常。

    调用方应调用 engine.reset() 后重新加载完整数据。
    """
    pass


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


def _validate_bars_param(name: str, value: Any) -> int:
    """
    校验 left_bars / right_bars 参数。

    必须是 int >= 1，不允许 0 / negative / float / invalid value。

    Args:
        name: 参数名（用于错误消息）
        value: 参数值

    Returns:
        校验后的 int 值

    Raises:
        ValueError: 参数无效
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer, got {value!r} (type={type(value).__name__})")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


class SwingPivotEngine(PivotDetector):
    """
    Swing Pivot 检测引擎 — 有状态、确定性、带确认机制、严格 append-only

    与 PILOT-001 (core/pivots.py) 的区别：
    - PILOT-001 是无状态函数，每次重新计算所有 pivot
    - SwingPivotEngine 是有状态引擎，维护已确认 pivot 的不可变历史
    - SwingPivotEngine 区分 confirmed / pending pivot
    - SwingPivotEngine 支持增量更新（新K线到来时只处理新增部分）
    - SwingPivotEngine 严格要求 append-only，历史数据被修改时 raise HistoricalDataMutationError

    确认机制（修正 off-by-one 后）：
    - 一个 pivot 在索引 i 处，需要左侧 left_bars 根 + 右侧 right_bars 根 K 线确认
    - 只要数据中已经存在 right_bars 根右侧 K 线，就应当 confirmed
    - 即当 (i + right_bars) <= (n - 1) 时，confirmed=True
    - 当 (i + right_bars) > (n - 1) 时，confirmed=False（待确认）

    Point-in-Time confirmation metadata：
    - 已确认 pivot：confirmed_at_index = i + right_bars，confirmed_at_date = klines[i + right_bars].date
    - 待确认 pivot：confirmed_at_index = None，confirmed_at_date = None
    - 这是该 Pivot 最早可被交易系统知道已经成立的时间，支持 FIR-13 无未来函数回测
    """

    def __init__(self, left_bars: int = 5, right_bars: int = 5):
        """
        初始化 Swing Pivot 引擎

        Args:
            left_bars: 左侧确认K线数（必须 int >= 1）
            right_bars: 右侧确认K线数（必须 int >= 1）
        """
        self.left_bars = _validate_bars_param("left_bars", left_bars)
        self.right_bars = _validate_bars_param("right_bars", right_bars)

        # 有状态：已确认的 pivot 历史（不可变）
        self._confirmed_pivots: List[PivotPoint] = []
        self._confirmed_indices: Set[int] = set()  # 快速查找已确认的索引

        # 记录上次处理到的数据长度，用于增量更新和 append-only 校验
        self._last_data_length: int = 0

        # 保存上次已处理历史数据的 fingerprint（用于 append-only 校验）
        # fingerprint = tuple of (date, open, high, low, close, volume) for each bar
        self._last_history_fingerprint: Optional[Tuple[Tuple, ...]] = None

    def reset(self) -> None:
        """重置引擎状态，清除所有已确认的 pivot 和历史 fingerprint"""
        self._confirmed_pivots = []
        self._confirmed_indices = set()
        self._last_data_length = 0
        self._last_history_fingerprint = None

    @property
    def confirmed_pivots(self) -> List[PivotPoint]:
        """所有已确认的 pivot（不可变历史）"""
        return list(self._confirmed_pivots)  # 返回副本，防止外部修改

    def _compute_fingerprint(self, klines: List[OHLCV]) -> Tuple[Tuple, ...]:
        """
        计算历史数据的 fingerprint，用于 append-only 校验。

        Args:
            klines: K线数据

        Returns:
            tuple of (date, open, high, low, close, volume) for each bar
        """
        return tuple(
            (k.date, k.open, k.high, k.low, k.close, k.volume)
            for k in klines
        )

    def _validate_append_only(self, klines: List[OHLCV]) -> None:
        """
        严格 append-only contract 校验。

        检查：
        1. 新长度不能小于旧长度（truncate → reject）
        2. 旧历史区间的数据不能发生变化（rewrite → reject）

        如果发现历史被 rewrite/truncate，raise HistoricalDataMutationError。

        Args:
            klines: 新的完整K线数据

        Raises:
            HistoricalDataMutationError: 历史数据被修改或截断
        """
        n = len(klines)
        old_n = self._last_data_length

        # 第一次 update 或 reset 后，没有历史可比较
        if old_n == 0 or self._last_history_fingerprint is None:
            return

        # 检查 1: 新长度不能小于旧长度（truncate）
        if n < old_n:
            raise HistoricalDataMutationError(
                f"Historical data truncated: previous length={old_n}, "
                f"current length={n}. Call engine.reset() and reload full data."
            )

        # 检查 2: 旧历史区间的数据不能发生变化（rewrite）
        new_fingerprint = self._compute_fingerprint(klines[:old_n])
        if new_fingerprint != self._last_history_fingerprint:
            # 找出第一个不同的位置，用于错误消息
            diff_idx = None
            for i in range(min(old_n, n)):
                if new_fingerprint[i] != self._last_history_fingerprint[i]:
                    diff_idx = i
                    break
            raise HistoricalDataMutationError(
                f"Historical data mutated at index {diff_idx}: "
                f"old={self._last_history_fingerprint[diff_idx] if diff_idx is not None else 'N/A'}, "
                f"new={new_fingerprint[diff_idx] if diff_idx is not None else 'N/A'}. "
                f"Call engine.reset() and reload full data."
            )

    def _make_pivot(
        self,
        klines: List[OHLCV],
        i: int,
        pivot_type: str,
        confirmed: bool,
    ) -> PivotPoint:
        """
        创建 PivotPoint 对象，包含 point-in-time confirmation metadata。

        Args:
            klines: K线数据
            i: pivot 索引
            pivot_type: "swing_high" / "swing_low"
            confirmed: 是否已确认

        Returns:
            PivotPoint 对象
        """
        price = klines[i].high if pivot_type == "swing_high" else klines[i].low

        # Point-in-Time confirmation metadata
        if confirmed:
            confirmed_at_index = i + self.right_bars
            confirmed_at_date = klines[confirmed_at_index].date
        else:
            confirmed_at_index = None
            confirmed_at_date = None

        return PivotPoint(
            index=i,
            date=klines[i].date,
            price=price,
            pivot_type=pivot_type,
            strength=min(self.left_bars, self.right_bars),
            confirmed=confirmed,
            confirmed_at_index=confirmed_at_index,
            confirmed_at_date=confirmed_at_date,
        )

    def _scan_range(self, klines: List[OHLCV], start: int, end: int) -> List[PivotPoint]:
        """
        扫描 [start, end) 范围内的 pivot（无状态计算）

        确认机制（修正 off-by-one 后）：
        - 已确认 pivot (confirmed=True)：左侧窗口完整 + 右侧有至少 right_bars 根K线
          即 (i + right_bars) <= (n - 1)
        - 待确认 pivot (confirmed=False)：左侧窗口完整 + 右侧窗口不完整（少于 right_bars 根），
          但基于已有右侧数据满足 swing high/low 条件
          即 (i + right_bars) > (n - 1)

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

            # 判断是否已确认（修正 off-by-one）：
            # 只要数据中已经存在 right_bars 根右侧 K 线，就应当 confirmed
            # 即当 (i + right_bars) <= (n - 1) 时，confirmed=True
            confirmed = (i + self.right_bars) <= (n - 1)

            if is_swing_high:
                pivots.append(self._make_pivot(klines, i, "swing_high", confirmed))
            elif is_swing_low:
                pivots.append(self._make_pivot(klines, i, "swing_low", confirmed))

        return pivots

    def update(self, klines: List[OHLCV]) -> PivotUpdateResult:
        """
        增量更新：处理新到来的K线，确认新的 pivot。

        严格 append-only contract：
        - 新长度不能小于旧长度（truncate → raise HistoricalDataMutationError）
        - 旧历史区间的数据不能发生变化（rewrite → raise HistoricalDataMutationError）
        - 如果发现历史被修改，调用方应 engine.reset() 后重新加载完整数据

        核心属性：
        - 已确认的历史 pivot 不可变，即使后续数据变化也不改变
        - 待确认的 pivot 可能随新数据变化或消失
        - 只处理新增的K线（从 _last_data_length 开始）

        Args:
            klines: 完整的K线数据（包含历史 + 新增）

        Returns:
            PivotUpdateResult: 更新结果（新增已确认 + 当前待确认 + 所有已确认）

        Raises:
            HistoricalDataMutationError: 历史数据被修改或截断
        """
        n = len(klines)

        # 严格 append-only 校验
        self._validate_append_only(klines)

        # 至少需要左侧窗口 + 1根当前K线 + 1根右侧K线
        # 注意：对于 pending pivot，不需要完整的 right_bars 窗口
        if n < self.left_bars + 2:
            # 数据不足，更新 fingerprint 和长度，返回空结果
            self._last_data_length = n
            self._last_history_fingerprint = self._compute_fingerprint(klines)
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

        # 更新上次处理位置和 fingerprint
        self._last_data_length = n
        self._last_history_fingerprint = self._compute_fingerprint(klines)

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

        参数 override 校验：
        - 自定义 left_bars / right_bars 也必须走与 constructor 相同的校验
        - 必须 int >= 1，不允许 0 / negative / float / invalid value

        Args:
            klines: K线数据列表
            left_bars: 左侧确认K线数（如不提供使用引擎默认值）
            right_bars: 右侧确认K线数（如不提供使用引擎默认值）

        Returns:
            List[PivotPoint]: Pivot点列表（按索引升序，包含 confirmed 和 pending）
        """
        # 参数 override 校验（与 constructor 相同的校验逻辑）
        if left_bars is not None:
            left_bars = _validate_bars_param("left_bars", left_bars)
        if right_bars is not None:
            right_bars = _validate_bars_param("right_bars", right_bars)

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
