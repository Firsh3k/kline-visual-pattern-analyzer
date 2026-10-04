# Canonical Core — 统一核心模块

> V5 架构基础，消除 Web/Python/Skill 三端业务逻辑漂移

## 架构模型

**Python Canonical Reference + Mandatory Cross-Runtime Parity**

Python 是算法的规范参考实现（canonical reference implementation）。
Web 端 JS 实现必须通过 cross-runtime parity 测试保证与 Python 一致。
Skill 端直接调用 Python core 模块。

这不是"implement once and every runtime consumes it"的单实现模型，
而是"Python 作为规范参考 + 强制跨运行时一致性校验"的模型。
所有业务规则变更必须：先改 Python core → 同步 Web JS → 跑 parity 测试。

## 设计目标

1. **Python Canonical Reference** — Python 是算法的规范参考实现
2. **统一数据结构** — 所有输入输出使用标准数据类（12个数据类）
3. **统一接口** — 所有算法实现标准抽象基类（9个接口）
4. **统一注册** — 所有算法通过注册中心管理（默认 fail fast on duplicate）
5. **统一测试** — 所有算法通过标准测试框架验证（tests/v5/）
6. **Mandatory Parity Gate** — Web 端通过 cross-runtime parity 测试保证与 Python 一致
7. **类型化输出契约** — BacktestResult/BacktestSummary 等回测输出使用类型化数据类，而非自由格式 Dict

## 目录结构

```
core/
├── __init__.py          # 模块入口，导出公共API（含向后兼容 detect_swing_pivots）
├── types.py             # 统一数据结构（12个数据类 + 4个枚举）
├── interfaces.py        # 统一接口定义（9个抽象基类）
├── registry.py          # 算法注册中心（fail fast on duplicate）
├── pivots.py            # PILOT-001 swing-pivot 检测（向后兼容）
└── README.md            # 本文档

tests/v5/
├── __init__.py
├── conftest.py              # pytest配置和fixtures
├── test_types.py            # 数据结构测试（24个）
├── test_interfaces.py       # 接口测试（15个）
├── test_registry.py         # 注册中心测试（含duplicate registration，19个）
├── test_contract.py         # 契约测试（字段名/接口签名/序列化稳定性，14个）
├── test_backwards_compat.py # 向后兼容测试（detect_swing_pivots，6个）
└── test_backtest_result.py  # BacktestResult/BacktestSummary测试（17个）
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

## 架构模型声明

**Python Canonical Reference + Mandatory Cross-Runtime Parity**

Python 是所有算法的规范参考实现（canonical reference implementation）。
这不是"implement once and every runtime consumes it"的单实现模型，
而是"Python 作为规范参考 + 强制跨运行时一致性校验"的模型。

- 所有新算法必须先在 Python `core/` 中实现（规范参考）
- Web 端 JS 实现必须通过 `tests/parity/` 中的 cross-runtime parity 测试（强制一致性）
- Skill 端直接调用 Python core 模块，不重复实现算法
- 任何算法修改必须：先改 Python core → 同步 Web JS → 跑 parity 测试
- 契约测试（`tests/v5/test_contract.py`）验证 Python 端的字段名/接口签名稳定性，作为 parity 的基础

### 为什么不选择单实现模型？

当前架构选择"canonical reference + parity"而非"single implementation consumed by all runtimes"，原因：
1. Web 端是纯前端 jspage，无法直接调用 Python（需要后端/API/WASM，属于 V5+ 架构升级）
2. Skill 端运行在 Agent 环境，可以直接调用 Python
3. **Canonical Schema Cross-Runtime Parity**（`tests/v5/run_canonical_parity.py`）已验证新 `core/types.py` 契约的 100% 字段一致性：
   - 4 个枚举（19 个枚举值）完全一致
   - 11 个数据类（90 个字段）完全一致
   - null 语义（nullable）逐字段验证
   - 嵌套结构（List[X] / Optional[X]）验证
   - 该测试已接入 CI，任何 schema 漂移都会被阻断
4. 现有 `run_cross_parity.py` 验证的是 legacy production outputs（V4 业务逻辑），与 canonical schema parity 是两层独立的一致性保障
5. 未来如果需要真正的单实现，可以通过 WASM 或后端 API 迁移，当前契约测试为迁移提供了稳定性保障

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

- **V5.2.0 (2026-10-04)**: FIR-23 Canonical Market Data Contract
  - 新增 4 个枚举（AdjustmentType/FrequencyType/DataSourceType/DataQualityFlag）
  - 新增 4 个数据类（DataProvenance/MarketDataBar/MarketDataSeries/DataQualityReport）
  - DataProvider 接口扩展（fetch_klines/validate_data/get_trading_calendar）
  - SinaDataProvider / TencentDataProvider / MultiSourceDataProvider
  - A股交易日历（2020-2027）
  - 复权因子计算（前复权/后复权）
  - Parity 100% 通过（8 enums, 15 dataclasses, 126 fields）

- **V5.1.0 (2026-10-03)**: FIR-12 Swing Pivot Engine
  - SwingPivotEngine + PivotPatternDetectors

- **V5.0 (2026-10-03)**: 初始架构建立
  - 统一数据结构（10个数据类）
  - 统一接口定义（9个抽象基类）
  - 算法注册中心
  - 统一测试框架（tests/v5/）
  - 明确 Python 为 Source of Truth

---

## Market Data Contract (FIR-23)

### 类型体系概览

| 类型 | 用途 |
|------|------|
| `AdjustmentType` | 复权方式：none/qfq/hfq |
| `FrequencyType` | K线频率：daily/weekly/monthly |
| `DataSourceType` | 数据源：sina/tencent/ifind/tongdaxin/manual |
| `DataQualityFlag` | 质量标记：ok/suspended/missing/adjusted/partial |
| `DataProvenance` | 单根K线的数据溯源（source/url/fetched_at/hash/adjustment/flag） |
| `MarketDataBar` | 带 provenance 的增强 OHLCV |
| `MarketDataSeries` | 完整数据序列（symbol+元数据+bars+quality_report） |
| `DataQualityReport` | 数据质量报告（完整率/缺失/异常） |

### 数据源对比

| 能力 | Sina | Tencent |
|------|------|---------|
| 日线 | ✅ | ✅ |
| 周线/月线 | ❌ | ✅ |
| 前复权(qfq) | ❌（需自行计算） | ✅ 原生 |
| 后复权(hfq) | ❌（需自行计算） | ✅ 原生 |
| 成交额 | ❌ | ❌ |
| 数据量 | 最多1023日 | 最多1023日 |
| 延迟 | 较低 | 较低 |

### 复权策略

- **Tencent** 原生支持 qfq/hfq，直接使用
- **Sina** 仅返回不复权数据；如需复权，通过 `adjustment.py` 从 Tencent hfq/none 数据反推因子
- 前复权：以最新交易日收盘价为基准，历史价格向下调整
- 后复权：以上市首日为基准，历史价格向上调整

### 交易日历规则

- 周六、周日休市（**即使国务院安排调休上班，股市仍休市**）
- 法定节假日休市（2020-2025 硬编码，2026-2027 估算）
- 提供 `is_trading_day` / `get_trading_days` / `next_trading_day` / `prev_trading_day`

### 缺失数据处理

- **停牌日**：不生成 K 线 bar，在 `DataQualityReport.suspended_days` 中记录
- **数据源缺失**：MultiSource 自动降级到备源；仍缺失则记录 `missing_days`
- **禁止静默前向填充**（no forward-fill）
- **价格跳变 >20%** 标记为 anomaly（除权日除外）

### Provenance 设计

每根 `MarketDataBar` 携带 `DataProvenance`，记录：
- 数据源（sina/tencent/...）
- 请求 URL
- 拉取时间（ISO8601）
- 原始响应 SHA256 哈希
- 已应用的复权方式
- 数据质量标记

这确保了每一根 K 线都可追溯到原始 API 响应。

### 使用示例

```python
from core.market_data import SinaDataProvider, TencentDataProvider, MultiSourceDataProvider
from core import AdjustmentType, FrequencyType

# 单源
provider = SinaDataProvider()
series = provider.fetch_klines(symbol="sh600519", adjustment=AdjustmentType.NONE)
print(f"Got {len(series.bars)} bars, completeness={series.quality_report.completeness_ratio}")

# 腾讯复权
tx = TencentDataProvider()
qfq = tx.fetch_klines(symbol="sh600519", adjustment=AdjustmentType.FORWARD)

# 多源容错 + 交叉验证
multi = MultiSourceDataProvider(cross_validate=True)
series = multi.fetch_klines(symbol="sh600519")

# 转换为 legacy OHLCV
klines = series.to_ohlcv_list()
stock_data = series.to_stock_data()
```
