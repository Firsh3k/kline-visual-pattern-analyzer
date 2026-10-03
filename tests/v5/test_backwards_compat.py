"""
V5 测试 — 向后兼容性验证

确保 V5 迁移期间，PILOT-001 引入的公共 API 仍然可用。
"""

import pytest


class TestBackwardsCompatibility:
    """向后兼容性测试"""

    def test_detect_swing_pivots_importable(self):
        """验证 from core import detect_swing_pivots 仍然可用"""
        from core import detect_swing_pivots
        assert callable(detect_swing_pivots)

    def test_detect_swing_pivots_in_all(self):
        """验证 detect_swing_pivots 在 __all__ 中"""
        import core
        assert "detect_swing_pivots" in core.__all__

    def test_detect_swing_pivots_works(self):
        """验证 detect_swing_pivots 功能正常"""
        from core import detect_swing_pivots

        # 构造一个有明确 swing high 和 swing low 的序列
        data = [
            {"day": "2026-01-01", "high": 10.0, "low": 9.0},
            {"day": "2026-01-02", "high": 10.5, "low": 9.5},
            {"day": "2026-01-03", "high": 12.0, "low": 10.0},  # swing high
            {"day": "2026-01-04", "high": 11.0, "low": 9.5},
            {"day": "2026-01-05", "high": 10.5, "low": 8.0},   # swing low
            {"day": "2026-01-06", "high": 11.0, "low": 9.0},
            {"day": "2026-01-07", "high": 11.5, "low": 9.5},
        ]

        pivots = detect_swing_pivots(data, left=1, right=1)
        assert len(pivots) >= 2  # 至少有一个 swing high 和一个 swing low

        # 验证返回的字典结构
        for p in pivots:
            assert "index" in p
            assert "type" in p
            assert "price" in p
            assert "date" in p
            assert p["type"] in ("swing_high", "swing_low")

    def test_detect_swing_pivots_empty_data(self):
        """验证空数据返回空列表"""
        from core import detect_swing_pivots
        assert detect_swing_pivots([], left=1, right=1) == []

    def test_detect_swing_pivots_insufficient_data(self):
        """验证数据不足时返回空列表"""
        from core import detect_swing_pivots
        data = [{"high": 10, "low": 9}, {"high": 11, "low": 10}]
        assert detect_swing_pivots(data, left=2, right=2) == []

    def test_core_version(self):
        """验证 core 版本号"""
        import core
        assert hasattr(core, "__version__")
        assert core.__version__.startswith("5.")
