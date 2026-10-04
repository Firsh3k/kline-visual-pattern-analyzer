"""
FIR-12 — Swing Pivot Engine 测试

核心验收标准（用户明确要求）：
1. 不要用"视觉上看起来对"作为验收标准
2. 修改未来右侧 K 线，会影响尚未确认的 pivot，但不能改变已经确认的历史 pivot

测试分类：
- TestBasicFunctionality: 基础功能
- TestConfirmationMechanism: 确认机制（confirmed vs pending）
- TestImmutabilityOfConfirmedPivots: 已确认pivot的不可变性（核心）
- TestMutabilityOfPendingPivots: 待确认pivot的可变性（核心）
- TestIncrementalUpdate: 增量更新
- TestDeterminism: 确定性
- TestEdgeCases: 边界条件
"""

import pytest
import copy
from core import (
    SwingPivotEngine,
    PivotUpdateResult,
    PivotPoint,
    OHLCV,
)
from core.swing_pivot import HistoricalDataMutationError


# ========== 辅助函数 ==========

def make_klines(prices_high_low, start_date="2026-01-01"):
    """
    构造K线数据

    Args:
        prices_high_low: 列表，每个元素是 (high, low) 元组
        start_date: 起始日期

    Returns:
        List[OHLCV]
    """
    from datetime import datetime, timedelta
    klines = []
    base_date = datetime.strptime(start_date, "%Y-%m-%d")
    for i, (high, low) in enumerate(prices_high_low):
        date = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")
        # open/close 取中间值，volume 固定
        open_price = (high + low) / 2
        close_price = (high + low) / 2
        klines.append(OHLCV(
            date=date,
            open=open_price,
            high=high,
            low=low,
            close=close_price,
            volume=1000.0,
        ))
    return klines


def make_wave_pattern(n_left=3, peak=10.0, base=5.0, n_right=3):
    """
    构造一个简单的波浪形态，中间有一个明显的 swing high

    价格序列: base -> 上升 -> peak -> 下降 -> base
    总长度: n_left + 1 + n_right

    Returns:
        List[OHLCV], peak_index
    """
    prices = []
    # 左侧上升
    for i in range(n_left):
        price = base + (peak - base) * (i + 1) / (n_left + 1)
        prices.append((price + 0.1, price - 0.1))
    # 峰值
    prices.append((peak + 0.1, peak - 0.1))
    # 右侧下降
    for i in range(n_right):
        price = peak - (peak - base) * (i + 1) / (n_right + 1)
        prices.append((price + 0.1, price - 0.1))

    klines = make_klines(prices)
    peak_index = n_left
    return klines, peak_index


# ========== 基础功能测试 ==========

class TestBasicFunctionality:
    """SwingPivotEngine 基础功能测试"""

    def test_engine_creation(self):
        """引擎可以正常创建"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        assert engine.left_bars == 2
        assert engine.right_bars == 2
        assert engine.confirmed_pivots == []

    def test_invalid_left_bars(self):
        """left_bars 必须为正整数"""
        with pytest.raises(ValueError):
            SwingPivotEngine(left_bars=0)
        with pytest.raises(ValueError):
            SwingPivotEngine(left_bars=-1)
        with pytest.raises(ValueError):
            SwingPivotEngine(left_bars=1.5)

    def test_invalid_right_bars(self):
        """right_bars 必须为正整数"""
        with pytest.raises(ValueError):
            SwingPivotEngine(right_bars=0)

    def test_detect_returns_list(self):
        """detect 返回列表"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=3, n_right=3)
        result = engine.detect(klines)
        assert isinstance(result, list)

    def test_detect_returns_pivot_points(self):
        """detect 返回 PivotPoint 对象"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=3, n_right=3)
        result = engine.detect(klines)
        for p in result:
            assert isinstance(p, PivotPoint)

    def test_reset_clears_state(self):
        """reset 清除所有状态"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=3, n_right=3)
        engine.update(klines)
        assert len(engine.confirmed_pivots) > 0

        engine.reset()
        assert engine.confirmed_pivots == []


# ========== 确认机制测试 ==========

class TestConfirmationMechanism:
    """Pivot 确认机制测试"""

    def test_confirmed_pivot_has_complete_right_window(self):
        """已确认的 pivot 右侧有完整的确认窗口"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        # 构造足够长的数据，确保峰值右侧有完整窗口
        klines, peak_idx = make_wave_pattern(n_left=3, n_right=5)
        result = engine.detect(klines)

        # 找到峰值处的 pivot
        peak_pivots = [p for p in result if p.index == peak_idx]
        assert len(peak_pivots) == 1
        assert peak_pivots[0].confirmed is True

    def test_pending_pivot_has_incomplete_right_window(self):
        """待确认的 pivot 右侧确认窗口不完整"""
        engine = SwingPivotEngine(left_bars=2, right_bars=5)
        # 峰值右侧只有 3 根K线，但需要 5 根确认
        klines, peak_idx = make_wave_pattern(n_left=3, n_right=3)
        result = engine.detect(klines)

        # 找到峰值处的 pivot
        peak_pivots = [p for p in result if p.index == peak_idx]
        assert len(peak_pivots) == 1
        assert peak_pivots[0].confirmed is False

    def test_pending_becomes_confirmed_with_more_data(self):
        """待确认的 pivot 在更多数据到来后变为已确认"""
        engine = SwingPivotEngine(left_bars=2, right_bars=3)

        # 第一阶段：峰值右侧只有 2 根K线，需要 3 根确认 → 待确认
        klines_partial, peak_idx = make_wave_pattern(n_left=3, n_right=2)
        result_partial = engine.detect(klines_partial)
        peak_pivot_partial = [p for p in result_partial if p.index == peak_idx][0]
        assert peak_pivot_partial.confirmed is False

        # 第二阶段：峰值右侧有 5 根K线，超过需要的 3 根 → 已确认
        klines_full, _ = make_wave_pattern(n_left=3, n_right=5)
        result_full = engine.detect(klines_full)
        peak_pivot_full = [p for p in result_full if p.index == peak_idx][0]
        assert peak_pivot_full.confirmed is True

    def test_detect_confirmed_only(self):
        """detect_confirmed_only 只返回已确认的 pivot"""
        engine = SwingPivotEngine(left_bars=2, right_bars=5)
        klines, _ = make_wave_pattern(n_left=3, n_right=3)
        result = engine.detect_confirmed_only(klines)
        for p in result:
            assert p.confirmed is True

    def test_detect_pending_only(self):
        """detect_pending_only 只返回待确认的 pivot"""
        engine = SwingPivotEngine(left_bars=2, right_bars=5)
        klines, _ = make_wave_pattern(n_left=3, n_right=3)
        result = engine.detect_pending_only(klines)
        for p in result:
            assert p.confirmed is False


# ========== 核心测试：已确认 pivot 的不可变性 ==========

class TestImmutabilityOfConfirmedPivots:
    """
    核心验收标准：已确认的历史 pivot 不可变

    修改未来右侧 K 线，不能改变已经确认的历史 pivot。
    """

    def test_modifying_future_bars_does_not_change_confirmed_pivot(self):
        """
        核心测试：修改未来右侧K线，不改变已确认的pivot

        场景：
        1. 构造数据，峰值在索引 5，右侧有足够确认窗口（right_bars=2）
        2. 运行引擎，峰值处的 pivot 已确认
        3. 修改峰值右侧远处的K线（索引 > 5 + right_bars）
        4. 再次运行引擎
        5. 验证已确认的 pivot 没有改变（价格、类型、索引都相同）
        """
        engine = SwingPivotEngine(left_bars=2, right_bars=2)

        # 构造数据：左侧上升 -> 峰值(索引5) -> 右侧下降 -> 平台
        # 总长度 12，峰值索引 5，右侧有 6 根K线（足够确认）
        prices = [
            (5.1, 4.9),   # 0
            (6.1, 5.9),   # 1
            (7.1, 6.9),   # 2
            (8.1, 7.9),   # 3
            (9.1, 8.9),   # 4
            (10.1, 9.9),  # 5 — 峰值
            (9.1, 8.9),   # 6
            (8.1, 7.9),   # 7
            (7.1, 6.9),   # 8
            (6.1, 5.9),   # 9
            (5.1, 4.9),   # 10
            (5.1, 4.9),   # 11
        ]
        klines_original = make_klines(prices)

        # 第一次检测
        result_original = engine.detect(klines_original)
        peak_pivot_original = [p for p in result_original if p.index == 5]
        assert len(peak_pivot_original) == 1
        assert peak_pivot_original[0].confirmed is True
        assert peak_pivot_original[0].pivot_type == "swing_high"
        original_price = peak_pivot_original[0].price
        original_index = peak_pivot_original[0].index

        # 修改未来右侧的K线（索引 9, 10, 11 — 远离峰值确认窗口）
        prices_modified = copy.deepcopy(prices)
        prices_modified[9] = (15.1, 14.9)   # 大幅上涨
        prices_modified[10] = (16.1, 15.9)
        prices_modified[11] = (17.1, 16.9)
        klines_modified = make_klines(prices_modified)

        # 第二次检测
        result_modified = engine.detect(klines_modified)
        peak_pivot_modified = [p for p in result_modified if p.index == 5]
        assert len(peak_pivot_modified) == 1
        assert peak_pivot_modified[0].confirmed is True

        # 核心断言：已确认的 pivot 没有改变
        assert peak_pivot_modified[0].price == original_price
        assert peak_pivot_modified[0].index == original_index
        assert peak_pivot_modified[0].pivot_type == "swing_high"

    def test_confirmed_pivot_immutable_with_incremental_update(self):
        """
        核心测试：增量更新时，已确认的 pivot 不可变

        场景：
        1. 使用有状态引擎，先 update 一批数据（包含已确认 pivot）
        2. 记录已确认 pivot 的状态
        3. 再 update 一批新数据（修改/添加未来K线）
        4. 验证历史已确认 pivot 没有改变
        """
        engine = SwingPivotEngine(left_bars=2, right_bars=2)

        # 第一阶段：数据包含已确认的 pivot
        prices_phase1 = [
            (5.1, 4.9),   # 0
            (6.1, 5.9),   # 1
            (7.1, 6.9),   # 2
            (8.1, 7.9),   # 3
            (9.1, 8.9),   # 4
            (10.1, 9.9),  # 5 — 峰值（已确认，因为右侧有 6,7,8,9）
            (9.1, 8.9),   # 6
            (8.1, 7.9),   # 7
            (7.1, 6.9),   # 8
            (6.1, 5.9),   # 9
        ]
        klines_phase1 = make_klines(prices_phase1)
        result_phase1 = engine.update(klines_phase1)

        # 验证峰值 pivot 已确认
        confirmed_phase1 = [p for p in result_phase1.all_confirmed if p.index == 5]
        assert len(confirmed_phase1) == 1
        assert confirmed_phase1[0].confirmed is True
        original_price = confirmed_phase1[0].price

        # 第二阶段：添加新数据（未来K线大幅波动）
        prices_phase2 = copy.deepcopy(prices_phase1)
        prices_phase2.extend([
            (20.1, 19.9),  # 10 — 大幅上涨
            (25.1, 24.9),  # 11
            (15.1, 14.9),  # 12 — 大幅下跌
        ])
        klines_phase2 = make_klines(prices_phase2)
        result_phase2 = engine.update(klines_phase2)

        # 核心断言：历史已确认 pivot 没有改变
        confirmed_phase2 = [p for p in result_phase2.all_confirmed if p.index == 5]
        assert len(confirmed_phase2) == 1
        assert confirmed_phase2[0].price == original_price
        assert confirmed_phase2[0].confirmed is True
        assert confirmed_phase2[0].pivot_type == "swing_high"

        # 验证引擎内部状态也没有改变
        engine_confirmed = [p for p in engine.confirmed_pivots if p.index == 5]
        assert len(engine_confirmed) == 1
        assert engine_confirmed[0].price == original_price

    def test_modifying_bars_within_confirmation_window_can_affect_pivot(self):
        """
        补充测试：修改确认窗口内的K线，可以影响 pivot（但这是合理的，因为窗口内的数据直接参与判断）

        注意：这与"已确认pivot不可变"不矛盾。
        已确认pivot不可变是指：一旦 pivot 被确认，修改确认窗口之外的未来数据不影响它。
        但如果修改确认窗口之内的数据，pivot 可能会改变（因为判断条件变了）。
        这个测试明确区分这两种情况。
        """
        engine = SwingPivotEngine(left_bars=2, right_bars=2)

        # 构造数据，峰值在索引 4
        prices = [
            (5.1, 4.9),   # 0
            (6.1, 5.9),   # 1
            (7.1, 6.9),   # 2
            (8.1, 7.9),   # 3
            (10.1, 9.9),  # 4 — 峰值
            (9.1, 8.9),   # 5 — 确认窗口内
            (8.1, 7.9),   # 6 — 确认窗口内
            (7.1, 6.9),   # 7
            (6.1, 5.9),   # 8
        ]
        klines_original = make_klines(prices)
        result_original = engine.detect(klines_original)
        peak_pivot_original = [p for p in result_original if p.index == 4]
        assert len(peak_pivot_original) == 1
        assert peak_pivot_original[0].confirmed is True

        # 修改确认窗口内的K线（索引 5 — 峰值右侧第一根）
        # 让它的 high 超过峰值，这样峰值就不再是 swing high
        prices_modified = copy.deepcopy(prices)
        prices_modified[5] = (12.1, 11.9)  # 超过峰值
        klines_modified = make_klines(prices_modified)
        result_modified = engine.detect(klines_modified)

        # 峰值处的 pivot 应该消失了（因为确认窗口内的数据改变了判断条件）
        peak_pivot_modified = [p for p in result_modified if p.index == 4]
        assert len(peak_pivot_modified) == 0

        # 这个测试说明：确认窗口内的数据可以影响 pivot，这是合理的
        # 而"已确认pivot不可变"是指确认窗口之外的未来数据不影响已确认pivot


# ========== 核心测试：待确认 pivot 的可变性 ==========

class TestMutabilityOfPendingPivots:
    """
    核心验收标准：待确认的 pivot 可变

    修改未来右侧 K 线，会影响尚未确认的 pivot（可能被确认、可能消失、可能改变类型）。
    """

    def test_pending_pivot_can_be_confirmed_by_future_data(self):
        """
        核心测试：待确认的 pivot 可以被未来数据确认

        场景：
        1. 构造数据，峰值右侧确认窗口不完整（pivot 待确认）
        2. 添加未来K线，使确认窗口完整
        3. 验证 pivot 从待确认变为已确认
        """
        engine = SwingPivotEngine(left_bars=2, right_bars=3)

        # 第一阶段：峰值右侧只有 2 根K线，需要 3 根确认 → 待确认
        prices_partial = [
            (5.1, 4.9),   # 0
            (6.1, 5.9),   # 1
            (7.1, 6.9),   # 2
            (10.1, 9.9),  # 3 — 峰值
            (9.1, 8.9),   # 4
            (8.1, 7.9),   # 5
        ]
        klines_partial = make_klines(prices_partial)
        result_partial = engine.detect(klines_partial)
        peak_pivot_partial = [p for p in result_partial if p.index == 3]
        assert len(peak_pivot_partial) == 1
        assert peak_pivot_partial[0].confirmed is False

        # 第二阶段：添加未来K线，峰值右侧有 3 根确认 → 已确认
        prices_full = copy.deepcopy(prices_partial)
        prices_full.extend([
            (7.1, 6.9),   # 6 — 第3根确认K线
            (6.1, 5.9),   # 7
        ])
        klines_full = make_klines(prices_full)
        result_full = engine.detect(klines_full)
        peak_pivot_full = [p for p in result_full if p.index == 3]
        assert len(peak_pivot_full) == 1
        assert peak_pivot_full[0].confirmed is True

    def test_pending_pivot_can_disappear_with_future_data(self):
        """
        核心测试：待确认的 pivot 可以因未来数据而消失

        场景：
        1. 构造数据，峰值右侧确认窗口不完整（pivot 待确认）
        2. 添加未来K线，新K线的 high 超过峰值
        3. 验证 pivot 消失了（因为不再满足 swing high 条件）
        """
        engine = SwingPivotEngine(left_bars=2, right_bars=3)

        # 第一阶段：峰值待确认
        prices_partial = [
            (5.1, 4.9),   # 0
            (6.1, 5.9),   # 1
            (7.1, 6.9),   # 2
            (10.1, 9.9),  # 3 — 峰值
            (9.1, 8.9),   # 4
            (8.1, 7.9),   # 5
        ]
        klines_partial = make_klines(prices_partial)
        result_partial = engine.detect(klines_partial)
        peak_pivot_partial = [p for p in result_partial if p.index == 3]
        assert len(peak_pivot_partial) == 1
        assert peak_pivot_partial[0].confirmed is False

        # 第二阶段：添加未来K线，新K线 high 超过峰值
        prices_modified = copy.deepcopy(prices_partial)
        prices_modified.extend([
            (12.1, 11.9),  # 6 — 超过峰值！
            (13.1, 12.9),  # 7 — 新峰值
            (12.1, 11.9),  # 8 — 新峰值右侧确认
            (11.1, 10.9),  # 9
        ])
        klines_modified = make_klines(prices_modified)
        result_modified = engine.detect(klines_modified)

        # 峰值处的 pivot 应该消失了
        peak_pivot_modified = [p for p in result_modified if p.index == 3]
        assert len(peak_pivot_modified) == 0

        # 新的峰值应该在索引 7
        new_peaks = [p for p in result_modified if p.pivot_type == "swing_high"]
        assert len(new_peaks) >= 1
        assert any(p.index == 7 for p in new_peaks)


# ========== 增量更新测试 ==========

class TestIncrementalUpdate:
    """增量更新测试"""

    def test_update_returns_pivot_update_result(self):
        """update 返回 PivotUpdateResult"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=3, n_right=3)
        result = engine.update(klines)
        assert isinstance(result, PivotUpdateResult)

    def test_update_new_confirmed(self):
        """update 返回新增的已确认 pivot"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, peak_idx = make_wave_pattern(n_left=3, n_right=5)
        result = engine.update(klines)
        assert len(result.new_confirmed) > 0
        assert any(p.index == peak_idx for p in result.new_confirmed)

    def test_update_pending(self):
        """update 返回当前待确认的 pivot"""
        engine = SwingPivotEngine(left_bars=2, right_bars=5)
        klines, _ = make_wave_pattern(n_left=3, n_right=3)
        result = engine.update(klines)
        assert len(result.pending) > 0

    def test_incremental_update_accumulates_confirmed_pivots(self):
        """增量更新累积已确认的 pivot"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)

        # 第一阶段
        klines1, _ = make_wave_pattern(n_left=3, n_right=3)
        result1 = engine.update(klines1)
        count1 = len(result1.all_confirmed)

        # 第二阶段：添加更多数据（可能产生新的 pivot）
        prices2 = [(k.high, k.low) for k in klines1]
        prices2.extend([
            (4.1, 3.9),  # 新低（可能产生 swing low）
            (5.1, 4.9),
            (6.1, 5.9),
        ])
        klines2 = make_klines(prices2)
        result2 = engine.update(klines2)

        # 已确认 pivot 数量应该 >= 第一阶段
        assert len(result2.all_confirmed) >= count1

    def test_update_idempotent_for_same_data(self):
        """对相同数据重复 update 是幂等的（不会重复添加已确认 pivot）"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=3, n_right=3)

        result1 = engine.update(klines)
        count1 = len(result1.all_confirmed)

        # 用相同数据再次 update
        result2 = engine.update(klines)
        count2 = len(result2.all_confirmed)

        # 数量应该相同（不会重复添加）
        assert count1 == count2
        # 新增的已确认 pivot 应该为空
        assert len(result2.new_confirmed) == 0


# ========== 确定性测试 ==========

class TestDeterminism:
    """确定性测试 — 相同输入永远产生相同输出"""

    def test_same_input_same_output(self):
        """相同输入产生相同输出"""
        engine1 = SwingPivotEngine(left_bars=2, right_bars=2)
        engine2 = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=3, n_right=3)

        result1 = engine1.detect(klines)
        result2 = engine2.detect(klines)

        assert len(result1) == len(result2)
        for p1, p2 in zip(result1, result2):
            assert p1.index == p2.index
            assert p1.price == p2.price
            assert p1.pivot_type == p2.pivot_type
            assert p1.confirmed == p2.confirmed

    def test_detect_does_not_modify_engine_state(self):
        """detect 不修改引擎内部状态（无状态）"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=3, n_right=3)

        # detect 之前
        confirmed_before = len(engine.confirmed_pivots)

        # detect
        engine.detect(klines)

        # detect 之后，状态不变
        confirmed_after = len(engine.confirmed_pivots)
        assert confirmed_before == confirmed_after

    def test_detect_with_custom_params_does_not_change_defaults(self):
        """detect 使用自定义参数不改变引擎默认参数"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=3, n_right=3)

        engine.detect(klines, left_bars=3, right_bars=3)

        # 默认参数不变
        assert engine.left_bars == 2
        assert engine.right_bars == 2


# ========== 边界条件测试 ==========

class TestEdgeCases:
    """边界条件测试"""

    def test_insufficient_data_returns_empty(self):
        """数据不足时返回空列表"""
        engine = SwingPivotEngine(left_bars=5, right_bars=5)
        klines = make_klines([(10.1, 9.9)] * 5)  # 只有 5 根，需要 11 根
        result = engine.detect(klines)
        assert result == []

    def test_empty_data_returns_empty(self):
        """空数据返回空列表"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        result = engine.detect([])
        assert result == []

    def test_flat_market_no_pivots(self):
        """横盘市场没有 pivot（所有价格相同）"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines = make_klines([(10.0, 10.0)] * 20)
        result = engine.detect(klines)
        assert result == []

    def test_single_wide_candle_rejected_as_ambiguous(self):
        """单根宽幅K线同时满足 swing high 和 swing low 时被拒绝（歧义）"""
        engine = SwingPivotEngine(left_bars=1, right_bars=1)
        # 构造数据：中间一根K线 high 很高，low 很低
        prices = [
            (10.0, 10.0),  # 0
            (20.0, 1.0),   # 1 — 宽幅K线（high=20, low=1）
            (10.0, 10.0),  # 2
        ]
        klines = make_klines(prices)
        result = engine.detect(klines)

        # 索引 1 处不应该有 pivot（因为同时满足 high 和 low，被拒绝为歧义）
        pivot_at_1 = [p for p in result if p.index == 1]
        assert len(pivot_at_1) == 0

    def test_pivot_point_has_confirmed_field(self):
        """PivotPoint 有 confirmed 字段"""
        pivot = PivotPoint(
            index=0,
            date="2026-01-01",
            price=10.0,
            pivot_type="swing_high",
        )
        assert hasattr(pivot, 'confirmed')
        assert pivot.confirmed is True  # 默认 True

        pivot_pending = PivotPoint(
            index=0,
            date="2026-01-01",
            price=10.0,
            pivot_type="swing_high",
            confirmed=False,
        )
        assert pivot_pending.confirmed is False

    def test_pivot_point_to_dict_includes_confirmed(self):
        """PivotPoint.to_dict() 包含 confirmed 字段"""
        pivot = PivotPoint(
            index=0,
            date="2026-01-01",
            price=10.0,
            pivot_type="swing_high",
            confirmed=False,
        )
        d = pivot.to_dict()
        assert 'confirmed' in d
        assert d['confirmed'] is False


# ============================================================
# Blocker 1: 严格边界测试 — Confirmation off-by-one
# ============================================================

class TestConfirmationBoundary:
    """
    严格边界测试：确认 Pivot confirmation 没有 off-by-one 错误。

    定义：Pivot 位于 index i，只要数据中已经存在 right_bars 根右侧 K 线，就应当 confirmed。
    即当 (i + right_bars) <= (n - 1) 时，confirmed=True。

    严格边界：
    - 右侧 right_bars - 1 根 → pending
    - 右侧 right_bars     根 → confirmed
    - 右侧 right_bars + 1 根 → confirmed
    """

    def test_swing_high_right_bars_minus_1_is_pending(self):
        """swing high: 右侧 right_bars-1 根 → pending"""
        engine = SwingPivotEngine(left_bars=2, right_bars=3)
        # 峰值索引 2，右侧只有 2 根（right_bars-1=2）
        klines, peak_idx = make_wave_pattern(n_left=2, n_right=2)
        result = engine.detect(klines)
        peak = [p for p in result if p.index == peak_idx][0]
        assert peak.confirmed is False
        assert peak.confirmed_at_index is None
        assert peak.confirmed_at_date is None

    def test_swing_high_right_bars_exact_is_confirmed(self):
        """swing high: 右侧 right_bars 根 → confirmed（边界关键点）"""
        engine = SwingPivotEngine(left_bars=2, right_bars=3)
        # 峰值索引 2，右侧正好 3 根（right_bars=3）
        klines, peak_idx = make_wave_pattern(n_left=2, n_right=3)
        result = engine.detect(klines)
        peak = [p for p in result if p.index == peak_idx][0]
        assert peak.confirmed is True
        # confirmed_at_index = i + right_bars = 2 + 3 = 5
        assert peak.confirmed_at_index == peak_idx + 3
        assert peak.confirmed_at_date == klines[peak_idx + 3].date

    def test_swing_high_right_bars_plus_1_is_confirmed(self):
        """swing high: 右侧 right_bars+1 根 → confirmed"""
        engine = SwingPivotEngine(left_bars=2, right_bars=3)
        # 峰值索引 2，右侧有 4 根（right_bars+1=4）
        klines, peak_idx = make_wave_pattern(n_left=2, n_right=4)
        result = engine.detect(klines)
        peak = [p for p in result if p.index == peak_idx][0]
        assert peak.confirmed is True
        # confirmed_at_index 仍然是 i + right_bars = 2 + 3 = 5（最早确认时间）
        assert peak.confirmed_at_index == peak_idx + 3
        assert peak.confirmed_at_date == klines[peak_idx + 3].date

    def test_swing_low_boundary(self):
        """swing low: 同样的边界规则适用"""
        engine = SwingPivotEngine(left_bars=2, right_bars=3)
        # 构造 swing low 模式（V型底）
        prices = [
            (10.1, 9.9),   # 0
            (9.1, 8.9),    # 1
            (8.1, 7.9),    # 2 — 谷底
            (9.1, 8.9),    # 3
            (10.1, 9.9),   # 4 — 右侧第2根（right_bars-1=2）→ pending
        ]
        klines_pending = make_klines(prices)
        result_pending = engine.detect(klines_pending)
        valley_pending = [p for p in result_pending if p.index == 2 and p.pivot_type == "swing_low"]
        assert len(valley_pending) == 1
        assert valley_pending[0].confirmed is False

        # 添加第3根右侧K线 → confirmed
        prices.append((11.1, 10.9))  # 5 — 右侧第3根（right_bars=3）→ confirmed
        klines_confirmed = make_klines(prices)
        result_confirmed = engine.detect(klines_confirmed)
        valley_confirmed = [p for p in result_confirmed if p.index == 2 and p.pivot_type == "swing_low"][0]
        assert valley_confirmed.confirmed is True
        assert valley_confirmed.confirmed_at_index == 2 + 3  # i + right_bars
        assert valley_confirmed.confirmed_at_date == klines_confirmed[5].date

    def test_update_boundary_transition(self):
        """update(): 从 pending 到 confirmed 的边界过渡"""
        engine = SwingPivotEngine(left_bars=2, right_bars=3)

        # 第一阶段：峰值右侧只有 2 根 → pending
        klines_partial, peak_idx = make_wave_pattern(n_left=2, n_right=2)
        result1 = engine.update(klines_partial)
        assert len(result1.pending) > 0
        peak_pending = [p for p in result1.pending if p.index == peak_idx][0]
        assert peak_pending.confirmed is False

        # 第二阶段：手动 append 1 根K线，峰值右侧正好 3 根 → confirmed
        import copy
        klines_full = copy.deepcopy(klines_partial)
        last_date = klines_full[-1].date
        from datetime import datetime, timedelta
        next_date = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        klines_full.append(OHLCV(
            date=next_date, open=6.0, high=6.1, low=5.9, close=6.0, volume=1000.0
        ))
        result2 = engine.update(klines_full)
        assert len(result2.new_confirmed) > 0
        peak_confirmed = [p for p in result2.new_confirmed if p.index == peak_idx][0]
        assert peak_confirmed.confirmed is True
        assert peak_confirmed.confirmed_at_index == peak_idx + 3


# ============================================================
# Blocker 2: Point-in-Time confirmation metadata
# ============================================================

class TestPointInTimeMetadata:
    """
    Point-in-Time confirmation metadata 测试。

    已确认 Pivot：
    - confirmed_at_index = i + right_bars
    - confirmed_at_date = klines[i + right_bars].date

    Pending Pivot：
    - confirmed_at_index = None
    - confirmed_at_date = None
    """

    def test_confirmed_pivot_has_metadata(self):
        """已确认 pivot 必须有 confirmed_at_index 和 confirmed_at_date"""
        engine = SwingPivotEngine(left_bars=2, right_bars=3)
        klines, peak_idx = make_wave_pattern(n_left=2, n_right=5)
        result = engine.detect(klines)
        peak = [p for p in result if p.index == peak_idx][0]
        assert peak.confirmed is True
        assert peak.confirmed_at_index == peak_idx + 3
        assert peak.confirmed_at_date == klines[peak_idx + 3].date
        assert isinstance(peak.confirmed_at_index, int)
        assert isinstance(peak.confirmed_at_date, str)

    def test_pending_pivot_has_none_metadata(self):
        """待确认 pivot 的 confirmed_at_index/date 必须为 None"""
        engine = SwingPivotEngine(left_bars=2, right_bars=5)
        klines, peak_idx = make_wave_pattern(n_left=2, n_right=2)
        result = engine.detect(klines)
        peak = [p for p in result if p.index == peak_idx][0]
        assert peak.confirmed is False
        assert peak.confirmed_at_index is None
        assert peak.confirmed_at_date is None

    def test_metadata_earliest_confirmation_time(self):
        """confirmed_at_index 是最早可被交易系统知道已经成立的时间"""
        engine = SwingPivotEngine(left_bars=2, right_bars=3)
        # 峰值索引 2，右侧有 10 根K线
        klines, peak_idx = make_wave_pattern(n_left=2, n_right=10)
        result = engine.detect(klines)
        peak = [p for p in result if p.index == peak_idx][0]
        # 即使右侧有 10 根，confirmed_at_index 仍然是 i + right_bars = 5
        # 这是最早确认时间，不是最后一根K线的时间
        assert peak.confirmed_at_index == peak_idx + 3
        assert peak.confirmed_at_index < len(klines) - 1

    def test_to_dict_includes_metadata(self):
        """PivotPoint.to_dict() 包含 confirmed_at_index 和 confirmed_at_date"""
        pivot = PivotPoint(
            index=0,
            date="2026-01-01",
            price=10.0,
            pivot_type="swing_high",
            confirmed=True,
            confirmed_at_index=5,
            confirmed_at_date="2026-01-08",
        )
        d = pivot.to_dict()
        assert 'confirmed_at_index' in d
        assert 'confirmed_at_date' in d
        assert d['confirmed_at_index'] == 5
        assert d['confirmed_at_date'] == "2026-01-08"


# ============================================================
# Blocker 2: 防 look-ahead 测试 — Prefix replay / Expanding window
# ============================================================

class TestLookAheadPrevention:
    """
    防 look-ahead 测试：用 prefix replay / expanding window 验证无未来函数。

    完整历史中 Pivot 在 index 20, right_bars = 5：
    - 数据只到 index 24 → 不得作为 confirmed pivot 使用
    - 数据到 index 25 → 第一次允许作为 confirmed pivot
    - index 25 以后 → confirmed_at_index 永远保持 25

    还要验证：对完整历史数组一次性 detect 得到的 Pivot，
    其 confirmed_at_index/date 与逐日 replay 得到的首次确认时间完全一致。
    """

    def _make_long_series_with_pivot_at_20(self, n_right=10):
        """构造一个长序列，在 index 20 处有一个明显的 swing high"""
        prices = []
        # 0-19: 缓慢上升
        for i in range(20):
            base = 5.0 + i * 0.3
            prices.append((base + 0.2, base - 0.2))
        # 20: 峰值
        prices.append((15.0, 14.8))
        # 21+: 下降
        for i in range(n_right):
            base = 14.0 - i * 0.3
            prices.append((base + 0.2, base - 0.2))
        return make_klines(prices)

    def test_data_to_24_not_confirmed(self):
        """数据只到 index 24 → pivot 不得作为 confirmed"""
        engine = SwingPivotEngine(left_bars=3, right_bars=5)
        klines = self._make_long_series_with_pivot_at_20(n_right=4)  # 到 index 24
        assert len(klines) == 25  # 0-24
        result = engine.detect(klines)
        pivot_20 = [p for p in result if p.index == 20]
        if pivot_20:
            assert pivot_20[0].confirmed is False
            assert pivot_20[0].confirmed_at_index is None

    def test_data_to_25_first_confirmed(self):
        """数据到 index 25 → 第一次允许作为 confirmed pivot"""
        engine = SwingPivotEngine(left_bars=3, right_bars=5)
        klines = self._make_long_series_with_pivot_at_20(n_right=5)  # 到 index 25
        assert len(klines) == 26  # 0-25
        result = engine.detect(klines)
        pivot_20 = [p for p in result if p.index == 20][0]
        assert pivot_20.confirmed is True
        # confirmed_at_index = i + right_bars = 20 + 5 = 25
        assert pivot_20.confirmed_at_index == 25
        assert pivot_20.confirmed_at_date == klines[25].date

    def test_data_after_25_confirmed_at_index_unchanged(self):
        """index 25 以后 → confirmed_at_index 永远保持 25"""
        engine = SwingPivotEngine(left_bars=3, right_bars=5)
        # 到 index 35（远超过确认窗口）
        klines = self._make_long_series_with_pivot_at_20(n_right=15)
        result = engine.detect(klines)
        pivot_20 = [p for p in result if p.index == 20][0]
        assert pivot_20.confirmed is True
        # confirmed_at_index 仍然是 25，不是 35
        assert pivot_20.confirmed_at_index == 25
        assert pivot_20.confirmed_at_date == klines[25].date

    def test_detect_vs_replay_first_confirmation_consistent(self):
        """
        一次性 detect 与逐日 replay 的首次确认时间完全一致。

        这是未来 FIR-13 的硬 Gate。
        """
        engine = SwingPivotEngine(left_bars=3, right_bars=5)
        full_klines = self._make_long_series_with_pivot_at_20(n_right=15)

        # 方法1: 一次性 detect 完整历史
        full_result = engine.detect(full_klines)
        pivot_from_detect = [p for p in full_result if p.index == 20][0]
        detect_confirmed_at = pivot_from_detect.confirmed_at_index

        # 方法2: 逐日 replay（expanding window），找到首次确认的时间
        replay_engine = SwingPivotEngine(left_bars=3, right_bars=5)
        first_confirmed_at = None
        for end_idx in range(21, len(full_klines)):
            prefix = full_klines[:end_idx + 1]
            result = replay_engine.detect(prefix)
            pivot_20 = [p for p in result if p.index == 20]
            if pivot_20 and pivot_20[0].confirmed:
                first_confirmed_at = end_idx
                break

        # 两种方法的首次确认时间必须一致
        assert first_confirmed_at is not None
        assert detect_confirmed_at == first_confirmed_at
        assert detect_confirmed_at == 25  # i + right_bars = 20 + 5

    def test_incremental_update_replay_consistent(self):
        """
        增量 update() 与一次性 detect() 的 confirmed_at_index 一致。
        """
        engine = SwingPivotEngine(left_bars=3, right_bars=5)
        full_klines = self._make_long_series_with_pivot_at_20(n_right=15)

        # 增量 update
        for end_idx in range(len(full_klines)):
            engine.update(full_klines[:end_idx + 1])

        pivot_from_update = [p for p in engine.confirmed_pivots if p.index == 20][0]

        # 一次性 detect
        full_result = engine.detect(full_klines)
        pivot_from_detect = [p for p in full_result if p.index == 20][0]

        assert pivot_from_update.confirmed_at_index == pivot_from_detect.confirmed_at_index
        assert pivot_from_update.confirmed_at_date == pivot_from_detect.confirmed_at_date


# ============================================================
# Blocker 3: 严格 append-only contract
# ============================================================

class TestAppendOnlyContract:
    """
    严格 append-only contract 测试。

    Engine 保存上一次已处理 history fingerprint。
    至少检查：
    - 新长度不能小于旧长度
    - 旧历史区间的数据不能发生变化

    如果发现历史被 rewrite/truncate：raise HistoricalDataMutationError。
    """

    def test_normal_append_passes(self):
        """正常 append → PASS"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines1, _ = make_wave_pattern(n_left=2, n_right=2)
        result1 = engine.update(klines1)
        assert result1 is not None

        # 正常 append：手动添加新K线（保持历史数据不变）
        import copy
        from datetime import datetime, timedelta
        klines2 = copy.deepcopy(klines1)
        last_date = klines2[-1].date
        for i in range(3):
            next_date = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=i+1)).strftime("%Y-%m-%d")
            klines2.append(OHLCV(
                date=next_date, open=5.0, high=5.1, low=4.9, close=5.0, volume=1000.0
            ))
        result2 = engine.update(klines2)
        assert result2 is not None

    def test_same_data_idempotent(self):
        """相同数据重复 update → idempotent"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=2, n_right=5)

        result1 = engine.update(klines)
        result2 = engine.update(klines)  # 相同数据重复 update

        # 第二次不应该有新确认的 pivot
        assert len(result2.new_confirmed) == 0
        assert result2.total_confirmed == result1.total_confirmed

    def test_historical_truncate_rejected(self):
        """历史 truncate → reject"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines_long, _ = make_wave_pattern(n_left=2, n_right=5)
        engine.update(klines_long)

        # 截断：只传前半部分
        klines_short = klines_long[:5]
        with pytest.raises(HistoricalDataMutationError, match="truncated"):
            engine.update(klines_short)

    def test_historical_ohlc_modified_rejected(self):
        """历史已确认区间 OHLC 被修改 → reject"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=2, n_right=5)
        engine.update(klines)

        # 修改历史 OHLC（index 0 的 high）
        import copy
        klines_modified = copy.deepcopy(klines)
        klines_modified[0] = OHLCV(
            date=klines_modified[0].date,
            open=klines_modified[0].open,
            high=999.99,  # 修改！
            low=klines_modified[0].low,
            close=klines_modified[0].close,
            volume=klines_modified[0].volume,
        )
        # 添加一根新K线（否则长度相同会被视为 idempotent）
        klines_modified.append(OHLCV(
            date="2026-02-01", open=1.0, high=1.1, low=0.9, close=1.0, volume=1000.0
        ))

        with pytest.raises(HistoricalDataMutationError, match="mutated"):
            engine.update(klines_modified)

    def test_historical_date_modified_rejected(self):
        """历史 date 被修改 → reject"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=2, n_right=5)
        engine.update(klines)

        # 修改历史 date
        import copy
        klines_modified = copy.deepcopy(klines)
        klines_modified[0] = OHLCV(
            date="1999-01-01",  # 修改！
            open=klines_modified[0].open,
            high=klines_modified[0].high,
            low=klines_modified[0].low,
            close=klines_modified[0].close,
            volume=klines_modified[0].volume,
        )
        klines_modified.append(OHLCV(
            date="2026-02-01", open=1.0, high=1.1, low=0.9, close=1.0, volume=1000.0
        ))

        with pytest.raises(HistoricalDataMutationError, match="mutated"):
            engine.update(klines_modified)

    def test_reset_then_reload_modified_history_passes(self):
        """reset 后重新加载修改历史 → PASS"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=2, n_right=5)
        engine.update(klines)

        # reset
        engine.reset()
        assert engine.confirmed_pivots == []

        # 重新加载修改后的历史（应该 PASS，因为 reset 清除了 fingerprint）
        import copy
        klines_modified = copy.deepcopy(klines)
        klines_modified[0] = OHLCV(
            date=klines_modified[0].date,
            open=klines_modified[0].open,
            high=999.99,
            low=klines_modified[0].low,
            close=klines_modified[0].close,
            volume=klines_modified[0].volume,
        )
        result = engine.update(klines_modified)
        assert result is not None

    def test_error_message_includes_reset_guidance(self):
        """HistoricalDataMutationError 错误消息包含 reset 指导"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines_long, _ = make_wave_pattern(n_left=2, n_right=5)
        engine.update(klines_long)

        klines_short = klines_long[:5]
        try:
            engine.update(klines_short)
            assert False, "应该抛出 HistoricalDataMutationError"
        except HistoricalDataMutationError as e:
            assert "reset" in str(e).lower()


# ============================================================
# 小问题 1: 参数 override 校验
# ============================================================

class TestParameterOverrideValidation:
    """
    参数 override 校验测试。

    detect(..., left_bars=?, right_bars=?) 自定义值也必须走与 constructor 相同的校验：
    - int >= 1
    - 不允许 0 / negative / float / invalid value
    """

    def test_detect_with_zero_left_bars_rejected(self):
        """detect(left_bars=0) → reject"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=2, n_right=5)
        with pytest.raises(ValueError, match="left_bars"):
            engine.detect(klines, left_bars=0)

    def test_detect_with_negative_right_bars_rejected(self):
        """detect(right_bars=-1) → reject"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=2, n_right=5)
        with pytest.raises(ValueError, match="right_bars"):
            engine.detect(klines, right_bars=-1)

    def test_detect_with_float_left_bars_rejected(self):
        """detect(left_bars=2.5) → reject"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=2, n_right=5)
        with pytest.raises(ValueError, match="left_bars"):
            engine.detect(klines, left_bars=2.5)

    def test_detect_with_string_right_bars_rejected(self):
        """detect(right_bars="3") → reject"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=2, n_right=5)
        with pytest.raises(ValueError, match="right_bars"):
            engine.detect(klines, right_bars="3")

    def test_detect_with_valid_override_works(self):
        """detect(left_bars=3, right_bars=4) → 正常工作，不修改引擎默认值"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=3, n_right=5)

        result = engine.detect(klines, left_bars=3, right_bars=4)
        assert result is not None

        # 引擎默认值不被修改
        assert engine.left_bars == 2
        assert engine.right_bars == 2

    def test_constructor_with_invalid_params_rejected(self):
        """constructor 也拒绝无效参数"""
        with pytest.raises(ValueError):
            SwingPivotEngine(left_bars=0, right_bars=2)
        with pytest.raises(ValueError):
            SwingPivotEngine(left_bars=2, right_bars=-1)
        with pytest.raises(ValueError):
            SwingPivotEngine(left_bars=2.5, right_bars=2)
        with pytest.raises(ValueError):
            SwingPivotEngine(left_bars="2", right_bars=2)

    def test_bool_not_accepted_as_int(self):
        """bool 不被接受为 int（True != 1）"""
        engine = SwingPivotEngine(left_bars=2, right_bars=2)
        klines, _ = make_wave_pattern(n_left=2, n_right=5)
        with pytest.raises(ValueError):
            engine.detect(klines, left_bars=True)

