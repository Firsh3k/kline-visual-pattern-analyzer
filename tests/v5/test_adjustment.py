"""
FIR-23: 复权计算单元测试
"""

import pytest
from core import AdjustmentType
from core.market_data.adjustment import (
    compute_adjustment_factors, apply_adjustment,
)


class TestComputeFactors:
    def test_simple_constant_factor(self):
        # 假设每次分红除权后 hfq 价格始终是 raw 的 2 倍
        raw = [10.0, 10.0, 11.0]
        hfq = [20.0, 20.0, 22.0]
        f = compute_adjustment_factors(raw, hfq)
        assert f == [2.0, 2.0, 2.0]

    def test_increasing_factor(self):
        # 模拟多次除权：因子从 1.0 逐步累积到 2.0
        raw = [10.0, 10.0, 10.0]
        hfq = [10.0, 15.0, 20.0]
        f = compute_adjustment_factors(raw, hfq)
        assert f[0] == 1.0
        assert f[1] == 1.5
        assert f[2] == 2.0

    def test_length_mismatch(self):
        with pytest.raises(ValueError):
            compute_adjustment_factors([1.0, 2.0], [1.0])


class TestApplyAdjustment:
    def test_none_returns_original(self):
        o = [10, 11, 12]
        h = [11, 12, 13]
        l = [9, 10, 11]
        c = [10.5, 11.5, 12.5]
        f = [1.0, 1.1, 1.2]
        ao, ah, al, ac = apply_adjustment(o, h, l, c, f, AdjustmentType.NONE)
        assert ao == o and ac == c

    def test_backward_adjustment(self):
        # 后复权: 直接乘因子
        o = [10, 10, 10]
        h = [11, 11, 11]
        l = [9, 9, 9]
        c = [10, 10, 10]
        f = [1.0, 1.5, 2.0]
        ao, ah, al, ac = apply_adjustment(o, h, l, c, f, AdjustmentType.BACKWARD)
        assert ac == [10.0, 15.0, 20.0]
        assert ao == [10.0, 15.0, 20.0]

    def test_forward_adjustment_normalizes_to_latest(self):
        # 前复权: 最新日价格不变，历史日按比例缩小
        o = [10, 10, 10]
        h = [11, 11, 11]
        l = [9, 9, 9]
        c = [10, 10, 10]
        f = [2.0, 1.5, 1.0]  # 因子从旧到新递减
        ao, ah, al, ac = apply_adjustment(o, h, l, c, f, AdjustmentType.FORWARD)
        # 归一化后 f_norm = [2.0/1.0, 1.5/1.0, 1.0/1.0] = [2.0, 1.5, 1.0]
        # Wait, latest factor is f[-1]=1.0, so normalized = [2.0, 1.5, 1.0]
        # qfq price = raw * normalized_factor
        assert ac == [20.0, 15.0, 10.0]
        # 最新日保持原始价格
        assert ac[-1] == 10.0

    def test_forward_adjustment_with_increasing_factors(self):
        # 后复权因子通常随时间递增（历史价被放大）
        raw_c = [10.0, 10.0, 11.0]
        hfq_c = [10.0, 15.0, 22.0]
        f = compute_adjustment_factors(raw_c, hfq_c)
        # f = [1.0, 1.5, 2.0]
        # 前复权归一化: f_norm = f / f[-1] = [0.5, 0.75, 1.0]
        ao, ah, al, ac = apply_adjustment(
            [10, 10, 11], [11, 11, 12], [9, 9, 10], raw_c, f, AdjustmentType.FORWARD
        )
        # 最新日 = raw * 1.0 = 11.0
        assert ac[-1] == 11.0
        # 最早日 = raw * 0.5 = 5.0
        assert ac[0] == 5.0

    def test_precision(self):
        raw = [100.0, 100.0]
        hfq = [100.0, 133.33]
        f = compute_adjustment_factors(raw, hfq)
        assert abs(f[1] - 1.3333) < 0.001
