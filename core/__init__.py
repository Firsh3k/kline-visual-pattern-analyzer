"""
Canonical Core — K线形态分析工具统一核心模块

V5 新增，用于消除 Web/Python/Skill 三端业务逻辑漂移。

## 架构模型

**Python Canonical Reference + Mandatory Cross-Runtime Parity**

Python 是算法的规范参考实现（canonical reference implementation）。
Web 端 JS 实现必须通过 cross-runtime parity 测试保证与 Python 一致。
Skill 端直接调用 Python core 模块。

这不是"implement once and every runtime consumes it"的单实现模型，
而是"Python 作为规范参考 + 强制跨运行时一致性校验"的模型。
所有业务规则变更必须：先改 Python core → 同步 Web JS → 跑 parity 测试。

## 向后兼容

`detect_swing_pivots` 是 PILOT-001 引入的公共 API，在 V5 迁移期间保持兼容。
新代码应使用 `core.PivotPoint` 数据类和 `core.PivotDetector` 接口。
"""

# 向后兼容：PILOT-001 公共 API
from .pivots import detect_swing_pivots

# V5 统一数据结构
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
    BacktestResult,
    BacktestSummary,
    TrendDirection,
    PatternStage,
    SignalType,
    ConfidenceLevel,
)

# V5 统一接口定义
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

# V5 算法注册中心
from .registry import (
    AlgorithmRegistry,
    DuplicateRegistrationError,
    registry,
    get_registry,
)

# V5 Phase 1B — Swing Pivot Engine (FIR-12)
from .swing_pivot import SwingPivotEngine, PivotUpdateResult, HistoricalDataMutationError

# Version 语义说明：
# __version__ = "5.1.0" 是 Canonical Core internal/API version，
# 不代表 GitHub product release 已经达到 V5.1.0。
# 正式产品 Release 版本由 Git tag / GitHub Release 决定（如 v4.0.0）。
__version__ = "5.1.0"
__all__ = [
    # 向后兼容 (PILOT-001)
    "detect_swing_pivots",
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
    "BacktestResult",
    "BacktestSummary",
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
    "DuplicateRegistrationError",
    "registry",
    "get_registry",
    # V5 Phase 1B — Swing Pivot Engine (FIR-12)
    "SwingPivotEngine",
    "PivotUpdateResult",
    "HistoricalDataMutationError",
]
