"""
Canonical Core — 算法注册中心

统一管理所有算法模块的注册和查找，确保三端调用路径一致。

Source of Truth: Python (此文件)

## 重复注册策略

默认 fail fast：重复注册相同 ID/名称会抛出 DuplicateRegistrationError，
防止 last-import-wins 导致的静默算法替换。

如需显式替换，使用 force=True 参数。
"""

from typing import Dict, Type, List, Optional
from .interfaces import (
    PatternDetector, SignalGenerator, IndicatorCalculator,
    DataProvider, MarketRegimeAnalyzer, FakeSignalAnalyzer,
    PivotDetector, StockAnalyzer, BacktestEngine,
    CanonicalCoreError,
)


class DuplicateRegistrationError(CanonicalCoreError):
    """重复注册异常 — 默认 fail fast，防止静默覆盖"""
    pass


class AlgorithmRegistry:
    """
    算法注册中心

    所有算法模块必须在此注册，通过统一接口查找和调用。
    支持按ID、按类型、按名称查找。

    重复注册默认抛出 DuplicateRegistrationError，使用 force=True 可显式替换。
    """

    def __init__(self):
        self._patterns: Dict[str, Type[PatternDetector]] = {}
        self._indicators: Dict[str, Type[IndicatorCalculator]] = {}
        self._data_providers: Dict[str, Type[DataProvider]] = {}
        self._signal_generators: Dict[str, Type[SignalGenerator]] = {}
        self._market_regime_analyzers: Dict[str, Type[MarketRegimeAnalyzer]] = {}
        self._fake_signal_analyzers: Dict[str, Type[FakeSignalAnalyzer]] = {}
        self._pivot_detectors: Dict[str, Type[PivotDetector]] = {}
        self._stock_analyzers: Dict[str, Type[StockAnalyzer]] = {}
        self._backtest_engines: Dict[str, Type[BacktestEngine]] = {}

    def _check_duplicate(self, registry_name: str, key: str, store: Dict, force: bool):
        """检查重复注册，默认 fail fast"""
        if key in store and not force:
            raise DuplicateRegistrationError(
                f"{registry_name} '{key}' 已注册。如需显式替换，请使用 force=True。"
                f"已注册: {store[key].__name__}"
            )

    # ========== 注册方法 ==========

    def register_pattern(self, detector_cls: Type[PatternDetector], force: bool = False) -> Type[PatternDetector]:
        """
        注册形态识别器

        Args:
            detector_cls: 形态识别器类
            force: 是否强制替换已注册的相同ID（默认False，重复注册抛出异常）

        Raises:
            DuplicateRegistrationError: 相同pattern_id已注册且force=False
        """
        instance = detector_cls()
        self._check_duplicate("Pattern", instance.pattern_id, self._patterns, force)
        self._patterns[instance.pattern_id] = detector_cls
        return detector_cls

    def register_indicator(self, name: str, calculator_cls: Type[IndicatorCalculator], force: bool = False) -> Type[IndicatorCalculator]:
        """注册技术指标计算器"""
        self._check_duplicate("Indicator", name, self._indicators, force)
        self._indicators[name] = calculator_cls
        return calculator_cls

    def register_data_provider(self, name: str, provider_cls: Type[DataProvider], force: bool = False) -> Type[DataProvider]:
        """注册数据提供者"""
        self._check_duplicate("DataProvider", name, self._data_providers, force)
        self._data_providers[name] = provider_cls
        return provider_cls

    def register_signal_generator(self, name: str, generator_cls: Type[SignalGenerator], force: bool = False) -> Type[SignalGenerator]:
        """注册信号生成器"""
        self._check_duplicate("SignalGenerator", name, self._signal_generators, force)
        self._signal_generators[name] = generator_cls
        return generator_cls

    def register_market_regime_analyzer(self, name: str, analyzer_cls: Type[MarketRegimeAnalyzer], force: bool = False) -> Type[MarketRegimeAnalyzer]:
        """注册大盘环境分析器"""
        self._check_duplicate("MarketRegimeAnalyzer", name, self._market_regime_analyzers, force)
        self._market_regime_analyzers[name] = analyzer_cls
        return analyzer_cls

    def register_fake_signal_analyzer(self, name: str, analyzer_cls: Type[FakeSignalAnalyzer], force: bool = False) -> Type[FakeSignalAnalyzer]:
        """注册假信号分析器"""
        self._check_duplicate("FakeSignalAnalyzer", name, self._fake_signal_analyzers, force)
        self._fake_signal_analyzers[name] = analyzer_cls
        return analyzer_cls

    def register_pivot_detector(self, name: str, detector_cls: Type[PivotDetector], force: bool = False) -> Type[PivotDetector]:
        """注册Pivot点检测器"""
        self._check_duplicate("PivotDetector", name, self._pivot_detectors, force)
        self._pivot_detectors[name] = detector_cls
        return detector_cls

    def register_stock_analyzer(self, name: str, analyzer_cls: Type[StockAnalyzer], force: bool = False) -> Type[StockAnalyzer]:
        """注册股票分析器"""
        self._check_duplicate("StockAnalyzer", name, self._stock_analyzers, force)
        self._stock_analyzers[name] = analyzer_cls
        return analyzer_cls

    def register_backtest_engine(self, name: str, engine_cls: Type[BacktestEngine], force: bool = False) -> Type[BacktestEngine]:
        """注册回测引擎"""
        self._check_duplicate("BacktestEngine", name, self._backtest_engines, force)
        self._backtest_engines[name] = engine_cls
        return engine_cls

    # ========== 查找方法 ==========

    def get_pattern(self, pattern_id: str) -> Optional[PatternDetector]:
        """按ID获取形态识别器实例"""
        cls = self._patterns.get(pattern_id)
        return cls() if cls else None

    def get_all_patterns(self) -> List[PatternDetector]:
        """获取所有已注册的形态识别器实例"""
        return [cls() for cls in self._patterns.values()]

    def get_pattern_ids(self) -> List[str]:
        """获取所有已注册的形态ID"""
        return list(self._patterns.keys())

    def get_indicator(self, name: str) -> Optional[IndicatorCalculator]:
        """按名称获取指标计算器实例"""
        cls = self._indicators.get(name)
        return cls() if cls else None

    def get_data_provider(self, name: str) -> Optional[DataProvider]:
        """按名称获取数据提供者实例"""
        cls = self._data_providers.get(name)
        return cls() if cls else None

    def get_signal_generator(self, name: str) -> Optional[SignalGenerator]:
        """按名称获取信号生成器实例"""
        cls = self._signal_generators.get(name)
        return cls() if cls else None

    def get_market_regime_analyzer(self, name: str) -> Optional[MarketRegimeAnalyzer]:
        """按名称获取大盘环境分析器实例"""
        cls = self._market_regime_analyzers.get(name)
        return cls() if cls else None

    def get_fake_signal_analyzer(self, name: str) -> Optional[FakeSignalAnalyzer]:
        """按名称获取假信号分析器实例"""
        cls = self._fake_signal_analyzers.get(name)
        return cls() if cls else None

    def get_pivot_detector(self, name: str = "default") -> Optional[PivotDetector]:
        """按名称获取Pivot点检测器实例"""
        cls = self._pivot_detectors.get(name)
        return cls() if cls else None

    def get_stock_analyzer(self, name: str = "default") -> Optional[StockAnalyzer]:
        """按名称获取股票分析器实例"""
        cls = self._stock_analyzers.get(name)
        return cls() if cls else None

    def get_backtest_engine(self, name: str = "default") -> Optional[BacktestEngine]:
        """按名称获取回测引擎实例"""
        cls = self._backtest_engines.get(name)
        return cls() if cls else None

    # ========== 装饰器（便捷注册） ==========

    def pattern(self, cls: Type[PatternDetector]) -> Type[PatternDetector]:
        """形态识别器装饰器：@registry.pattern"""
        return self.register_pattern(cls)

    def indicator(self, name: str, force: bool = False):
        """指标计算器装饰器：@registry.indicator('ma')"""
        def decorator(cls: Type[IndicatorCalculator]) -> Type[IndicatorCalculator]:
            return self.register_indicator(name, cls, force=force)
        return decorator

    def data_provider(self, name: str, force: bool = False):
        """数据提供者装饰器：@registry.data_provider('sina')"""
        def decorator(cls: Type[DataProvider]) -> Type[DataProvider]:
            return self.register_data_provider(name, cls, force=force)
        return decorator


# ========== 全局注册中心实例 ==========

# 全局单例，所有模块共享
registry = AlgorithmRegistry()


def get_registry() -> AlgorithmRegistry:
    """获取全局注册中心实例"""
    return registry
