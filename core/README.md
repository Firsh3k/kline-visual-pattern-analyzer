# Canonical Core — 统一核心模块

> V5 架构基础，消除 Web/Python/Skill 三端业务逻辑漂移

## 设计目标

1. **Single Source of Truth** — Python 是算法的唯一真实来源
2. **统一数据结构** — 所有输入输出使用标准数据类
3. **统一接口** — 所有算法实现标准抽象基类
4. **统一注册** — 所有算法通过注册中心管理
5. **统一测试** — 所有算法通过标准测试框架验证
6. **Parity Gate** — Web 端通过 cross-runtime parity 测试保证与 Python 一致

## 目录结构

```
core/
├── __init__.py          # 模块入口，导出公共API
├── types.py             # 统一数据结构
├── interfaces.py        # 统一接口定义（抽象基类）
├── registry.py          # 算法注册中心
└── README.md            # 本文档
```

## 核心数据结构

| 数据类 | 用途 |
|--------|------|
| `OHLCV` | 单根K线数据 |
| `StockData` | 股票完整数据容器 |
| `PivotPoint` | Swing High/Low 结构点 |
| `PatternKeyPoint` | 形态关键点（用于标注和解释） |
| `PatternResult` | 形态识别结果 |
| `SignalResult` | 买卖信号结果 |
| `MarketRegime` | 大盘环境判断 |
| `FakeSignalRisk` | 假信号风险评估 |
| `AnalysisResult` | 完整分析结果（聚合所有输出） |

## 核心接口

| 接口 | 用途 |
|------|------|
| `DataProvider` | 数据提供者（同花顺/通达信/WIND/新浪/CSV） |
| `IndicatorCalculator` | 技术指标计算器（MA/MACD/RSI/BOLL等） |
| `PivotDetector` | Pivot点检测器（V5新增） |
| `PatternDetector` | 形态识别器（头肩底/W底/M头等） |
| `SignalGenerator` | 信号生成器 |
| `MarketRegimeAnalyzer` | 大盘环境分析器 |
| `FakeSignalAnalyzer` | 假信号分析器 |
| `StockAnalyzer` | 股票分析器（顶层聚合） |
| `BacktestEngine` | 回测引擎（固定T+3/5/10/20/40） |

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

## Source of Truth 声明

**Python 是所有算法的唯一真实来源（Source of Truth）。**

- 所有新算法必须先在 Python `core/` 中实现
- Web 端 JS 实现必须通过 `tests/parity/` 中的 cross-runtime parity 测试
- Skill 端直接调用 Python core 模块，不重复实现算法
- 任何算法修改必须先改 Python core，再同步 Web JS，最后跑 parity 测试

## 算法注册与调用

```python
from core import registry, PatternDetector, PatternResult, TrendDirection, PatternStage, ConfidenceLevel

# 注册形态
@registry.pattern
class HeadShouldersBottom(PatternDetector):
    pattern_id = "hs_bottom"
    pattern_name = "头肩底"

    def detect(self, klines, pivots=None):
        # 实现识别逻辑
        pass

# 查找形态
detector = registry.get_pattern("hs_bottom")
all_patterns = registry.get_all_patterns()
pattern_ids = registry.get_pattern_ids()
```

## 版本历史

- **V5.0 (2026-10-03)**: 初始架构建立
  - 统一数据结构（10个数据类）
  - 统一接口定义（9个抽象基类）
  - 算法注册中心
  - 统一测试框架（tests/v5/）
  - 明确 Python 为 Source of Truth
