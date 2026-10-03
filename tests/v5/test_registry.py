"""
V5 测试 — 算法注册中心验证

确保注册中心的注册、查找、装饰器功能正常。
"""

import pytest
from core import (
    AlgorithmRegistry, PatternDetector, IndicatorCalculator,
    DataProvider, PatternResult, TrendDirection, PatternStage, ConfidenceLevel,
    OHLCV,
)


class TestAlgorithmRegistry:
    """AlgorithmRegistry 注册中心测试"""

    def test_create_empty_registry(self, clean_registry):
        assert len(clean_registry.get_pattern_ids()) == 0
        assert clean_registry.get_pattern("nonexistent") is None

    def test_register_pattern(self, clean_registry):
        class TestPattern(PatternDetector):
            @property
            def pattern_id(self):
                return "test_pattern"

            @property
            def pattern_name(self):
                return "测试形态"

            def detect(self, klines, pivots=None):
                return []

        clean_registry.register_pattern(TestPattern)

        assert "test_pattern" in clean_registry.get_pattern_ids()
        detector = clean_registry.get_pattern("test_pattern")
        assert detector is not None
        assert detector.pattern_id == "test_pattern"

    def test_get_all_patterns(self, clean_registry):
        class PatternA(PatternDetector):
            @property
            def pattern_id(self):
                return "pattern_a"
            @property
            def pattern_name(self):
                return "形态A"
            def detect(self, klines, pivots=None):
                return []

        class PatternB(PatternDetector):
            @property
            def pattern_id(self):
                return "pattern_b"
            @property
            def pattern_name(self):
                return "形态B"
            def detect(self, klines, pivots=None):
                return []

        clean_registry.register_pattern(PatternA)
        clean_registry.register_pattern(PatternB)

        all_patterns = clean_registry.get_all_patterns()
        assert len(all_patterns) == 2
        ids = {p.pattern_id for p in all_patterns}
        assert "pattern_a" in ids
        assert "pattern_b" in ids

    def test_get_nonexistent_pattern(self, clean_registry):
        assert clean_registry.get_pattern("nonexistent") is None

    def test_register_indicator(self, clean_registry):
        class TestIndicator(IndicatorCalculator):
            def calculate(self, klines, **kwargs):
                return {"ma": [10.0, 10.5]}

        clean_registry.register_indicator("ma", TestIndicator)

        calc = clean_registry.get_indicator("ma")
        assert calc is not None
        result = calc.calculate([])
        assert "ma" in result

    def test_register_data_provider(self, clean_registry):
        from core import StockData

        class TestProvider(DataProvider):
            def get_stock_data(self, code, days=250):
                return StockData(code=code, name="测试", klines=[])
            def get_index_data(self, code, days=250):
                return StockData(code=code, name="指数", klines=[])
            def get_sector_constituents(self, sector_code):
                return []

        clean_registry.register_data_provider("test", TestProvider)

        provider = clean_registry.get_data_provider("test")
        assert provider is not None

    def test_pattern_decorator(self, clean_registry):
        """测试 @registry.pattern 装饰器"""

        @clean_registry.pattern
        class DecoratedPattern(PatternDetector):
            @property
            def pattern_id(self):
                return "decorated"
            @property
            def pattern_name(self):
                return "装饰器形态"
            def detect(self, klines, pivots=None):
                return []

        assert "decorated" in clean_registry.get_pattern_ids()
        detector = clean_registry.get_pattern("decorated")
        assert detector.pattern_name == "装饰器形态"

    def test_indicator_decorator(self, clean_registry):
        """测试 @registry.indicator('name') 装饰器"""

        @clean_registry.indicator("test_ma")
        class DecoratedIndicator(IndicatorCalculator):
            def calculate(self, klines, **kwargs):
                return {"test_ma": [1.0, 2.0]}

        calc = clean_registry.get_indicator("test_ma")
        assert calc is not None
        assert calc.calculate([])['test_ma'] == [1.0, 2.0]

    def test_data_provider_decorator(self, clean_registry):
        """测试 @registry.data_provider('name') 装饰器"""
        from core import StockData

        @clean_registry.data_provider("decorated_provider")
        class DecoratedProvider(DataProvider):
            def get_stock_data(self, code, days=250):
                return StockData(code=code, name="装饰器", klines=[])
            def get_index_data(self, code, days=250):
                return StockData(code=code, name="指数", klines=[])
            def get_sector_constituents(self, sector_code):
                return []

        provider = clean_registry.get_data_provider("decorated_provider")
        assert provider is not None

    def test_registered_pattern_can_detect(self, clean_registry):
        """验证注册的形态识别器可以正常工作"""

        @clean_registry.pattern
        class WorkingPattern(PatternDetector):
            @property
            def pattern_id(self):
                return "working"
            @property
            def pattern_name(self):
                return "可工作形态"
            def detect(self, klines, pivots=None):
                return [PatternResult(
                    pattern_id="working",
                    pattern_name="可工作形态",
                    direction=TrendDirection.BULL,
                    stage=PatternStage.FORMING,
                    score=60,
                    confidence=ConfidenceLevel.B,
                    key_points=[],
                )]

        detector = clean_registry.get_pattern("working")
        klines = [OHLCV(date="2026-01-01", open=10, high=11, low=9, close=10.5, volume=1000)]
        results = detector.detect(klines)
        assert len(results) == 1
        assert results[0].score == 60


class TestGlobalRegistry:
    """全局注册中心单例测试"""

    def test_get_registry_returns_singleton(self):
        from core import get_registry, registry
        assert get_registry() is registry

    def test_global_registry_is_algorithm_registry(self):
        from core import registry, AlgorithmRegistry
        assert isinstance(registry, AlgorithmRegistry)
