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
    AdjustmentType,
    FrequencyType,
    DataSourceType,
    DataQualityFlag,
    DataProvenance,
    MarketDataBar,
    MarketDataSeries,
    DataQualityReport,
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
    MarketDataError,
)

# V5 算法注册中心
from .registry import (
    AlgorithmRegistry,
    DuplicateRegistrationError,
    registry,
    get_registry,
)

# V5 Phase 1B — Swing Pivot Engine (FIR-12 Engine Foundation, merged from main)
from .swing_pivot import SwingPivotEngine, PivotUpdateResult, HistoricalDataMutationError

# V5 FIR-12 Phase-2 — 基于 SwingPivotEngine 的经典形态识别器
from .pivot_patterns import (
    PivotPatternDetectorBase,
    WBottomDetector,
    MTopDetector,
    HSBottomDetector,
    HSTopDetector,
    MIN_HS_HEAD_PROMINENCE,
    MAX_NECKLINE_EXTRAPOLATION_RATIO,
)

# FIR-15 Sector Validation 模块说明：
# 故意不从 core.__init__ 重新导出 FIR-15 的公共类型，以避免虚假的 JS↔Python parity 声明。
# FIR-14 parity extractor 目前只覆盖 core/types.py 中的 canonical contracts。
# FIR-15 类型是 Python-only/internal，通过 core.sector_validation 命名空间访问：
#   from core.sector_validation import validate_sector, SectorProvenance, ...
# 当 FIR-15 contracts 需要 JS parity 时，应单独扩展 parity extractor 并明确声明覆盖范围。

# Version 语义说明：
# __version__ = "5.1.0" 是 Canonical Core internal/API version，
# 不代表 GitHub product release 已经达到 V5.1.0。
# 正式产品 Release 版本由 Git tag / GitHub Release 决定（如 v4.0.0）。
__version__ = "5.2.0"
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
    # FIR-23 市场数据契约
    "AdjustmentType",
    "FrequencyType",
    "DataSourceType",
    "DataQualityFlag",
    "DataProvenance",
    "MarketDataBar",
    "MarketDataSeries",
    "DataQualityReport",
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
    "MarketDataError",
    # 注册中心
    "AlgorithmRegistry",
    "DuplicateRegistrationError",
    "registry",
    "get_registry",
    # V5 Phase 1B — Swing Pivot Engine (FIR-12 Engine Foundation)
    "SwingPivotEngine",
    "PivotUpdateResult",
    "HistoricalDataMutationError",
    # V5 FIR-12 Phase-2 — Pivot 形态识别器
    "PivotPatternDetectorBase",
    "WBottomDetector",
    "MTopDetector",
    "HSBottomDetector",
    "HSTopDetector",
    "MIN_HS_HEAD_PROMINENCE",
    "MAX_NECKLINE_EXTRAPOLATION_RATIO",
    # 注意：FIR-15 Sector Validation 类型故意不导出到 core.__init__，
    # 保持 core.sector_validation 命名空间，避免虚假 JS↔Python parity 声明。
    # 详见模块顶部注释。
]
