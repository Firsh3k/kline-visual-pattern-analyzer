"""
FIR-13: Empirical A-share Calibration — 经验校准子包

将 heuristic priors 转换为 evidence-backed probability metrics。
构建可复现的 A 股校准数据集，使用固定 T+3/5/10/20/40 horizons，
统计各种形态信号的真实历史胜率。

## 模块结构

- `types.py` — 数据类定义（SignalOccurrence, HorizonBacktest, CalibrationMetric, CalibrationDataset）
- `backtest_engine.py` — 固定 horizon 回测引擎（MFE/MAE，数据不足返回 N/A）
- `data_collector.py` — 批量数据采集 + 缓存（基于 FIR-23 MultiSourceDataProvider）
- `signal_generator.py` — 形态信号生成（V4 pattern 逻辑，防 look-ahead）
- `calibrator.py` — 分组统计 + evidence threshold

## 核心原则

1. **防 look-ahead** — 信号只能使用信号日及之前的数据
2. **sample_size 透明** — 每个概率指标附带 sample_size，不足返回 N/A
3. **可复现** — 校准过程可从源数据复现
4. **不修改 V4** — 所有新代码在 core/calibration/ 下
"""

from .types import (
    PatternType,
    ConfirmationState,
    MarketRegime,
    ConfidenceLabel,
    SignalOccurrence,
    HorizonBacktest,
    CalibrationMetric,
    CalibrationGroup,
    CalibrationDataset,
    CalibrationSummary,
    DEFAULT_HORIZONS,
    SAMPLE_INSUFFICIENT,
    SAMPLE_LOW_CONFIDENCE,
    DEFAULT_WIN_THRESHOLD,
    confidence_for_sample_size,
    make_signal_id,
)

# 引擎类（延迟导入避免循环依赖）
from .backtest_engine import FixedHorizonBacktester
from .data_collector import CalibrationDataCollector
from .signal_generator import CalibrationSignalGenerator
from .calibrator import EmpiricalCalibrator

__all__ = [
    # 枚举
    "PatternType",
    "ConfirmationState",
    "MarketRegime",
    "ConfidenceLabel",
    # 数据类
    "SignalOccurrence",
    "HorizonBacktest",
    "CalibrationMetric",
    "CalibrationGroup",
    "CalibrationDataset",
    "CalibrationSummary",
    # 常量
    "DEFAULT_HORIZONS",
    "SAMPLE_INSUFFICIENT",
    "SAMPLE_LOW_CONFIDENCE",
    "DEFAULT_WIN_THRESHOLD",
    # 辅助函数
    "confidence_for_sample_size",
    "make_signal_id",
    # 引擎类
    "FixedHorizonBacktester",
    "CalibrationDataCollector",
    "CalibrationSignalGenerator",
    "EmpiricalCalibrator",
]
