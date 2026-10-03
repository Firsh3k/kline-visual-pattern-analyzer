"""
Canonical Core — K线形态分析工具统一核心模块

V5 新增，用于消除 Web/Python/Skill 三端业务逻辑漂移。

## 架构原则

1. **Single Source of Truth**: Python 是算法的唯一真实来源
2. **统一数据结构**: 所有输入输出使用 core.types 中定义的数据类
3. **统一接口**: 所有算法实现 core.interfaces 中定义的抽象基类
4. **统一注册**: 所有算法通过 core.registry 注册和查找
5. **统一测试**: 所有算法通过 tests/v5 中的测试框架验证
6. **Parity Gate**: Web 端通过 cross-runtime parity 测试保证与 Python 一致

## 目录结构

```
core/
├── __init__.py          # 模块入口，导出公共API
├── types.py             # 统一数据结构（OHLCV, StockData, PatternResult等）
├── interfaces.py        # 统一接口定义（抽象基类）
├── registry.py          # 算法注册中心
└── README.md            # 架构文档

tests/v5/
├── conftest.py          # pytest配置和fixtures
├── test_types.py        # 数据结构测试
├── test_interfaces.py   # 接口测试
└── test_registry.py     # 注册中心测试
```

## 三端调用路径

```
用户输入
    │
    ├─ Web端 (kline_analyzer.html)
    │    └─ JS实现 ← parity测试 → Python core
    │
    ├─ Python端 (scripts/)
    │    └─ 直接调用 core 模块
    │
    └─ Skill端 (skill/scripts/)
         └─ 调用 Python core 模块
```

## 版本

- V5.0 (2026-10-03): 初始架构建立，统一接口/数据结构/注册中心/测试框架
"""

from .types import (
    OHLCV,
    StockData,
    PivotPoint,
    PatternKeyPoint,
    PatternResult,
    SignalResult,
    MarketRegime,
    FakeSignalRisk,
    AnalysisResult,
    TrendDirection,
    PatternStage,
    SignalType,
    ConfidenceLevel,
)

from .interfaces import (
    DataProvider,
    IndicatorCalculator,
    PivotDetector,
    PatternDetector,
    SignalGenerator,
    MarketRegimeAnalyzer,
    FakeSignalAnalyzer,
    StockAnalyzer,
    BacktestEngine,
    CanonicalCoreError,
    DataProviderError,
    PatternDetectionError,
    AnalysisError,
)

from .registry import (
    AlgorithmRegistry,
    registry,
    get_registry,
)

__version__ = "5.0.0"
__all__ = [
    # 数据结构
    "OHLCV",
    "StockData",
    "PivotPoint",
    "PatternKeyPoint",
    "PatternResult",
    "SignalResult",
    "MarketRegime",
    "FakeSignalRisk",
    "AnalysisResult",
    "TrendDirection",
    "PatternStage",
    "SignalType",
    "ConfidenceLevel",
    # 接口
    "DataProvider",
    "IndicatorCalculator",
    "PivotDetector",
    "PatternDetector",
    "SignalGenerator",
    "MarketRegimeAnalyzer",
    "FakeSignalAnalyzer",
    "StockAnalyzer",
    "BacktestEngine",
    # 异常
    "CanonicalCoreError",
    "DataProviderError",
    "PatternDetectionError",
    "AnalysisError",
    # 注册中心
    "AlgorithmRegistry",
    "registry",
    "get_registry",
]
