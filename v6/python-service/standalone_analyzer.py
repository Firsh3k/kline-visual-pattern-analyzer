"""
!!! DEPRECATED / 废弃实验文件 — 禁止运行，禁止作为分析结果来源 !!!

Standalone K-line Pattern Analyzer（早期自包含原型，已内联一份历史 core 快照）
- 本文件是 FIR-31 探索阶段的未完成实验，内联拼接处存在残缺（已知含语法错误，无法运行）。
- 它不依赖仓库根 core，且其中算法不会随 core 更新，已与当前 Canonical Core 漂移。
- 当前生产架构：v6/app-builder/（NestJS child_process 调用 python_analyzer.py）。
- 唯一算法 Source of Truth：仓库根 core/（swing_pivot.py / pivot_patterns.py）。
- 本文件仅作历史留存，不要 import、不要运行、不要参考其算法输出。详见同目录 README.md。
"""
import sys
import os
import json
import urllib.request
import ssl
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any, Tuple
from enum import Enum


# ===== core/types.py =====
"""
Canonical Core — 统一数据结构定义

所有算法（Web/Python/Skill）必须使用这些数据结构作为输入输出，
确保三端数据格式完全一致。

Source of Truth: Python (此文件)
Web 端通过 cross-runtime parity 测试保证字段一致。
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from enum import Enum


class TrendDirection(Enum):
    """趋势方向"""
    BULL = "bull"      # 上升
    BEAR = "bear"      # 下降
    NEUTRAL = "neutral"  # 震荡


class PatternStage(Enum):
    """形态阶段"""
    FORMING = "forming"           # 形成中
    PRE_BREAK = "pre_break"       # 突破前
    BREAKOUT_DAY = "breakout_day"  # 突破日
    POST_BREAK = "post_break"     # 突破后
    RETEST = "retest"             # 回踩
    FAILED_BREAK = "failed_break"  # 突破失败
    COMPLETE = "complete"         # 完成


class SignalType(Enum):
    """信号类型"""
    BUY = "buy"        # 买入
    SELL = "sell"      # 卖出
    WATCH = "watch"    # 观察
    NONE = "none"      # 无信号


class ConfidenceLevel(Enum):
    """置信度等级"""
    S = "S"  # 极强
    A = "A"  # 强
    B = "B"  # 中
    C = "C"  # 弱
    D = "D"  # 极弱


@dataclass
class OHLCV:
    """单根K线数据 — 统一OHLCV格式"""
    date: str           # 日期 YYYY-MM-DD
    open: float         # 开盘价
    high: float         # 最高价
    low: float          # 最低价
    close: float        # 收盘价
    volume: float       # 成交量
    amount: Optional[float] = None  # 成交额（可选）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OHLCV':
        return cls(
            date=str(data['date']),
            open=float(data['open']),
            high=float(data['high']),
            low=float(data['low']),
            close=float(data['close']),
            volume=float(data['volume']),
            amount=float(data['amount']) if data.get('amount') is not None else None,
        )


@dataclass
class StockData:
    """股票完整数据 — 统一股票数据容器"""
    code: str                    # 股票代码 6位数字
    name: str                    # 股票名称
    klines: List[OHLCV]         # K线数据列表（按日期升序）
    market: str = "A股"         # 市场
    source: str = ""            # 数据来源
    last_updated: str = ""      # 最后更新时间

    @property
    def latest_close(self) -> float:
        """最新收盘价"""
        return self.klines[-1].close if self.klines else 0.0

    @property
    def data_length(self) -> int:
        """K线数量"""
        return len(self.klines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'name': self.name,
            'market': self.market,
            'source': self.source,
            'last_updated': self.last_updated,
            'klines': [k.to_dict() for k in self.klines],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StockData':
        return cls(
            code=str(data['code']),
            name=str(data['name']),
            market=data.get('market', 'A股'),
            source=data.get('source', ''),
            last_updated=data.get('last_updated', ''),
            klines=[OHLCV.from_dict(k) for k in data.get('klines', [])],
        )


@dataclass
class PivotPoint:
    """Pivot点 — Swing High / Swing Low 结构点"""
    index: int               # 在K线数组中的索引
    date: str                # 日期
    price: float             # 价格（high for swing high, low for swing low）
    pivot_type: str          # "swing_high" / "swing_low"
    strength: int = 1        # 强度（左右各多少根K线确认）
    confirmed: bool = True   # 是否已确认（右侧有完整确认窗口；False表示待确认，可能随新数据变化）
    confirmed_at_index: Optional[int] = None  # 该Pivot最早可被交易系统知道已经成立的K线索引（i + right_bars）
    confirmed_at_date: Optional[str] = None    # 该Pivot最早可被交易系统知道已经成立的日期

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PatternKeyPoint:
    """形态关键点 — 用于标注和解释"""
    name: str                # 关键点名称（如 "左肩", "头", "右肩", "颈线"）
    index: int               # K线索引
    date: str                # 日期
    price: float             # 价格
    description: str = ""    # 该点的形成原因/判断理由

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PatternResult:
    """形态识别结果 — 统一形态输出格式"""
    pattern_id: str                    # 形态ID（如 "hs_bottom", "w_bottom"）
    pattern_name: str                  # 形态名称（如 "头肩底", "W底"）
    direction: TrendDirection          # 趋势方向（上升/下降）
    stage: PatternStage                # 形态阶段
    score: int                         # 匹配度评分 0-100
    confidence: ConfidenceLevel        # 置信度等级
    key_points: List[PatternKeyPoint]  # 形态关键点列表
    neckline_price: Optional[float] = None   # 颈线价格（如有）
    breakout_price: Optional[float] = None   # 突破价格（如有）
    target_price: Optional[float] = None     # 目标价格（如有）
    stop_loss: Optional[float] = None        # 止损价格（如有）
    notes: str = ""                     # 备注

    def to_dict(self) -> Dict[str, Any]:
        return {
            'pattern_id': self.pattern_id,
            'pattern_name': self.pattern_name,
            'direction': self.direction.value,
            'stage': self.stage.value,
            'score': self.score,
            'confidence': self.confidence.value,
            'key_points': [kp.to_dict() for kp in self.key_points],
            'neckline_price': self.neckline_price,
            'breakout_price': self.breakout_price,
            'target_price': self.target_price,
            'stop_loss': self.stop_loss,
            'notes': self.notes,
        }


@dataclass
class SignalResult:
    """信号结果 — 统一买卖信号输出"""
    signal_type: SignalType            # 信号类型
    pattern_id: str                    # 关联的形态ID
    pattern_name: str                  # 关联的形态名称
    score: int                         # 信号评分 0-100
    confidence: ConfidenceLevel        # 置信度
    trigger_price: float               # 触发价格
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    reason: str = ""                   # 信号理由
    risks: List[str] = field(default_factory=list)  # 风险提示

    def to_dict(self) -> Dict[str, Any]:
        return {
            'signal_type': self.signal_type.value,
            'pattern_id': self.pattern_id,
            'pattern_name': self.pattern_name,
            'score': self.score,
            'confidence': self.confidence.value,
            'trigger_price': self.trigger_price,
            'target_price': self.target_price,
            'stop_loss': self.stop_loss,
            'reason': self.reason,
            'risks': self.risks,
        }


@dataclass
class MarketRegime:
    """大盘环境 — 统一市场环境判断"""
    regime: str                    # 环境类型（牛市/熊市/震荡市/结构性行情）
    score: int                     # 环境评分 0-100
    trend: TrendDirection          # 趋势方向
    stage: str = ""                # 阶段（如 "牛市初期", "熊市末期"）
    advice: str = ""               # 操作建议
    impact_on_stock: int = 0      # 对个股的影响系数（-20 ~ +20）
    details: Dict[str, Any] = field(default_factory=dict)  # 详细指标

    def to_dict(self) -> Dict[str, Any]:
        return {
            'regime': self.regime,
            'score': self.score,
            'trend': self.trend.value,
            'stage': self.stage,
            'advice': self.advice,
            'impact_on_stock': self.impact_on_stock,
            'details': self.details,
        }


@dataclass
class FakeSignalRisk:
    """假信号风险评估 — 统一假信号输出"""
    risk_score: int                    # 风险评分 0-100
    risk_level: str                    # 风险等级（低/中/高/极高）
    risks: List[Dict[str, Any]] = field(default_factory=list)  # 具体风险项
    suggestions: List[str] = field(default_factory=list)        # 规避建议

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisResult:
    """完整分析结果 — 统一股票分析输出（所有算法的最终聚合）"""
    stock: StockData                   # 股票数据
    patterns: List[PatternResult]      # 识别到的形态列表
    buy_signals: List[SignalResult]    # 买入信号列表
    sell_signals: List[SignalResult]   # 卖出信号列表
    market_regime: Optional[MarketRegime] = None  # 大盘环境
    fake_signal_risk: Optional[FakeSignalRisk] = None  # 假信号风险
    overall_score: int = 0             # 综合评分
    recommendation: str = ""           # 推荐操作
    summary: str = ""                  # 分析摘要
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据

    def to_dict(self) -> Dict[str, Any]:
        return {
            'stock': self.stock.to_dict(),
            'patterns': [p.to_dict() for p in self.patterns],
            'buy_signals': [s.to_dict() for s in self.buy_signals],
            'sell_signals': [s.to_dict() for s in self.sell_signals],
            'market_regime': self.market_regime.to_dict() if self.market_regime else None,
            'fake_signal_risk': self.fake_signal_risk.to_dict() if self.fake_signal_risk else None,
            'overall_score': self.overall_score,
            'recommendation': self.recommendation,
            'summary': self.summary,
            'metadata': self.metadata,
        }


@dataclass
class BacktestResult:
    """
    单信号回测结果 — 类型化的回测输出契约

    使用固定 T+3/5/10/20/40 horizon，不缩短窗口。
    future data 不足时返回 None/N/A。
    """
    code: str                          # 股票代码
    signal_date: str                   # 信号日期 YYYY-MM-DD
    horizon_days: int                  # 观察窗口天数（3/5/10/20/40）
    entry_price: float                 # 入场价格（信号日收盘价）
    return_pct: Optional[float] = None  # 收益率（%），None表示数据不足
    mfe_pct: Optional[float] = None     # Maximum Favorable Excursion（最大有利波动 %）
    mae_pct: Optional[float] = None     # Maximum Adverse Excursion（最大不利波动 %）
    final_price: Optional[float] = None  # 窗口结束时价格
    actual_days: Optional[int] = None    # 实际观察天数（可能因数据不足少于horizon）
    is_complete: bool = False            # 是否有完整的horizon数据
    notes: str = ""                      # 备注

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BacktestResult':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class BacktestSummary:
    """
    批量回测汇总 — 类型化的统计输出契约

    只能比较相同 horizon 的样本。
    """
    horizon_days: int                    # 观察窗口天数
    sample_size: int                     # 样本数量
    win_rate: Optional[float] = None     # 胜率（%），None表示无有效样本
    avg_return_pct: Optional[float] = None  # 平均收益率（%）
    median_return_pct: Optional[float] = None  # 收益率中位数（%）
    avg_mfe_pct: Optional[float] = None     # 平均MFE（%）
    avg_mae_pct: Optional[float] = None     # 平均MAE（%）
    max_return_pct: Optional[float] = None   # 最大收益率（%）
    min_return_pct: Optional[float] = None   # 最小收益率（%）
    profit_factor: Optional[float] = None     # 盈亏比（总盈利/总亏损）
    results: List[BacktestResult] = field(default_factory=list)  # 详细结果列表
    incomplete_count: int = 0                 # 数据不完整的样本数
    notes: str = ""                           # 备注

    def to_dict(self) -> Dict[str, Any]:
        return {
            'horizon_days': self.horizon_days,
            'sample_size': self.sample_size,
            'win_rate': self.win_rate,
            'avg_return_pct': self.avg_return_pct,
            'median_return_pct': self.median_return_pct,
            'avg_mfe_pct': self.avg_mfe_pct,
            'avg_mae_pct': self.avg_mae_pct,
            'max_return_pct': self.max_return_pct,
            'min_return_pct': self.min_return_pct,
            'profit_factor': self.profit_factor,
            'incomplete_count': self.incomplete_count,
            'notes': self.notes,
            'results': [r.to_dict() for r in self.results],
        }


# ===========================================================================
# FIR-23: Canonical Market Data Contract
# 以下类型为 FIR-23 新增，作为 FIR-13 Empirical Calibration 的数据基础。
# ===========================================================================


class AdjustmentType(Enum):
    """复权方式"""
    NONE = "none"          # 不复权
    FORWARD = "qfq"        # 前复权
    BACKWARD = "hfq"       # 后复权


class FrequencyType(Enum):
    """K线频率"""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class DataSourceType(Enum):
    """数据源类型"""
    SINA = "sina"
    TENCENT = "tencent"
    IFIND = "ifind"
    TONGDAXIN = "tongdaxin"
    MANUAL = "manual"


class DataQualityFlag(Enum):
    """数据质量标记"""
    OK = "ok"                # 正常
    SUSPENDED = "suspended"  # 停牌
    MISSING = "missing"      # 缺失
    ADJUSTED = "adjusted"    # 已复权
    PARTIAL = "partial"      # 部分数据


@dataclass
class DataProvenance:
    """数据来源溯源 — 每根K线的元数据"""
    source: DataSourceType               # 数据源
    url: str                             # 请求URL
    fetched_at: str                      # 拉取时间 ISO8601
    raw_payload_hash: str                # 原始响应SHA256哈希
    adjustment_applied: AdjustmentType   # 已应用的复权方式
    quality_flag: DataQualityFlag        # 数据质量标记

    def to_dict(self) -> Dict[str, Any]:
        return {
            'source': self.source.value,
            'url': self.url,
            'fetched_at': self.fetched_at,
            'raw_payload_hash': self.raw_payload_hash,
            'adjustment_applied': self.adjustment_applied.value,
            'quality_flag': self.quality_flag.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DataProvenance':
        return cls(
            source=DataSourceType(data['source']),
            url=str(data['url']),
            fetched_at=str(data['fetched_at']),
            raw_payload_hash=str(data['raw_payload_hash']),
            adjustment_applied=AdjustmentType(data['adjustment_applied']),
            quality_flag=DataQualityFlag(data['quality_flag']),
        )


@dataclass
class DataQualityReport:
    """数据质量报告 — 评估 MarketDataSeries 的完整性和一致性"""
    total_days: int                                  # 总自然日数
    expected_trading_days: int                       # 预期交易日数
    actual_bars: int                                 # 实际K线数
    suspended_days: int                              # 停牌天数
    missing_days: int                                # 已知原因缺失天数（如停牌，当前无停牌源=0）
    completeness_ratio: float                        # 完整率 (actual/expected)
    adjustment_consistent: bool                      # 复权一致性（True 仅当 positively 证明；unknown=False）
    unknown_missing_days: int = 0                   # 原因未知的缺失天数（provider/数据失败，blocker6）
    source_consistency: Optional[float] = None      # 多源一致性（相关系数或平均差异比）
    anomalies: List[Dict[str, Any]] = field(default_factory=list)  # 异常列表
    notes: str = ""                                  # 备注

    def to_dict(self) -> Dict[str, Any]:
        return {
            'total_days': self.total_days,
            'expected_trading_days': self.expected_trading_days,
            'actual_bars': self.actual_bars,
            'suspended_days': self.suspended_days,
            'missing_days': self.missing_days,
            'completeness_ratio': self.completeness_ratio,
            'adjustment_consistent': self.adjustment_consistent,
            'unknown_missing_days': self.unknown_missing_days,
            'source_consistency': self.source_consistency,
            'anomalies': self.anomalies,
            'notes': self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DataQualityReport':
        return cls(
            total_days=int(data['total_days']),
            expected_trading_days=int(data['expected_trading_days']),
            actual_bars=int(data['actual_bars']),
            suspended_days=int(data['suspended_days']),
            missing_days=int(data['missing_days']),
            completeness_ratio=float(data['completeness_ratio']),
            adjustment_consistent=bool(data['adjustment_consistent']),
            unknown_missing_days=int(data.get('unknown_missing_days', 0)),
            source_consistency=float(data['source_consistency']) if data.get('source_consistency') is not None else None,
            anomalies=list(data.get('anomalies', [])),
            notes=str(data.get('notes', '')),
        )


@dataclass
class MarketDataBar:
    """单根市场数据K线 — 带 provenance 的增强版OHLCV（provenance 必填，blocker5）"""
    date: str                              # 日期 YYYY-MM-DD
    open: float                            # 开盘价
    high: float                            # 最高价
    low: float                             # 最低价
    close: float                           # 收盘价
    volume: float                          # 成交量
    provenance: DataProvenance            # 数据溯源（必填，不可为 None）
    amount: Optional[float] = None        # 成交额（可选）

    @staticmethod
    def _legacy_provenance() -> 'DataProvenance':
        """legacy 转换占位 provenance（MANUAL + 空字段）——会被校验标记为 untracked。"""
        return DataProvenance(
            source=DataSourceType.MANUAL,
            url="",
            fetched_at="",
            raw_payload_hash="",
            adjustment_applied=AdjustmentType.NONE,
            quality_flag=DataQualityFlag.OK,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            'date': self.date,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'amount': self.amount,
            'provenance': self.provenance.to_dict() if self.provenance else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MarketDataBar':
        prov_data = data.get('provenance')
        provenance = DataProvenance.from_dict(prov_data) if prov_data else cls._legacy_provenance()
        return cls(
            date=str(data['date']),
            open=float(data['open']),
            high=float(data['high']),
            low=float(data['low']),
            close=float(data['close']),
            volume=float(data['volume']),
            provenance=provenance,
            amount=float(data['amount']) if data.get('amount') is not None else None,
        )

    def to_ohlcv(self) -> OHLCV:
        """转换为 legacy OHLCV（供下游算法使用）"""
        return OHLCV(
            date=self.date,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            amount=self.amount,
        )

    @classmethod
    def from_ohlcv(cls, ohlcv: OHLCV, provenance: Optional[DataProvenance] = None) -> 'MarketDataBar':
        """从 legacy OHLCV 构造（未提供 provenance 时构造显式 legacy 占位，而非 None）"""
        return cls(
            date=ohlcv.date,
            open=ohlcv.open,
            high=ohlcv.high,
            low=ohlcv.low,
            close=ohlcv.close,
            volume=ohlcv.volume,
            amount=ohlcv.amount,
            provenance=provenance if provenance is not None else cls._legacy_provenance(),
        )


@dataclass
class MarketDataSeries:
    """市场数据序列 — 带元数据和质量报告的完整K线数据集"""
    symbol: str                              # 证券代码（如 sh600519）
    name: str                                # 证券名称
    frequency: FrequencyType                 # K线频率
    adjustment: AdjustmentType               # 复权方式
    trading_calendar: str                    # 交易日历标识（如 'SSE'）
    source: DataSourceType                   # 数据源
    fetched_at: str                         # 拉取时间 ISO8601
    bars: List[MarketDataBar]               # K线列表（按日期升序）
    schema_version: str = "1.0"            # canonical schema 版本（blocker5）
    quality_report: Optional[DataQualityReport] = None  # 数据质量报告

    @property
    def last_bar_date(self) -> str:
        """最后一根K线日期（派生自 bars[-1].date；空序列返回空串）。

        该属性不参与 dataclass field parity（派生量），但必须出现在 to_dict 中。
        """
        return self.bars[-1].date if self.bars else ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'name': self.name,
            'frequency': self.frequency.value,
            'adjustment': self.adjustment.value,
            'trading_calendar': self.trading_calendar,
            'source': self.source.value,
            'fetched_at': self.fetched_at,
            'schema_version': self.schema_version,
            'last_bar_date': self.last_bar_date,
            'bars': [b.to_dict() for b in self.bars],
            'quality_report': self.quality_report.to_dict() if self.quality_report else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MarketDataSeries':
        qr_data = data.get('quality_report')
        return cls(
            symbol=str(data['symbol']),
            name=str(data['name']),
            frequency=FrequencyType(data['frequency']),
            adjustment=AdjustmentType(data['adjustment']),
            trading_calendar=str(data['trading_calendar']),
            source=DataSourceType(data['source']),
            fetched_at=str(data['fetched_at']),
            bars=[MarketDataBar.from_dict(b) for b in data.get('bars', [])],
            schema_version=str(data.get('schema_version', '1.0')),
            quality_report=DataQualityReport.from_dict(qr_data) if qr_data else None,
        )

    def to_ohlcv_list(self) -> List[OHLCV]:
        """转换为 List[OHLCV] 供下游算法使用"""
        return [b.to_ohlcv() for b in self.bars]

    def to_stock_data(self) -> StockData:
        """转换为 StockData（legacy 接口兼容）"""
        return StockData(
            code=self.symbol,
            name=self.name,
            klines=self.to_ohlcv_list(),
            market="A股",
            source=self.source.value,
            last_updated=self.fetched_at,
        )


# ===== core/interfaces.py =====
"""
Canonical Core — 统一接口定义

所有算法模块必须实现这些接口，确保三端调用路径完全一致。

Source of Truth: Python (此文件)
Web 端通过 cross-runtime parity 测试保证接口一致。
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from typing import (
    StockData, OHLCV, PatternResult, SignalResult,
    MarketRegime, FakeSignalRisk, AnalysisResult, PivotPoint,
    BacktestResult, BacktestSummary,
    MarketDataSeries, DataQualityReport,
    AdjustmentType, FrequencyType,
)


class DataProvider(ABC):
    """
    数据提供者接口

    所有数据源（同花顺、通达信、WIND、新浪财经、CSV）必须实现此接口，
    确保输出统一的 StockData 格式。
    """

    @abstractmethod
    def get_stock_data(self, code: str, days: int = 250) -> StockData:
        """
        获取股票K线数据

        Args:
            code: 股票代码（6位数字）
            days: 获取天数（默认250个交易日）

        Returns:
            StockData: 统一格式的股票数据

        Raises:
            DataProviderError: 数据获取失败
        """
        pass

    @abstractmethod
    def get_index_data(self, code: str, days: int = 250) -> StockData:
        """
        获取指数K线数据（用于大盘环境判断）

        Args:
            code: 指数代码
            days: 获取天数

        Returns:
            StockData: 统一格式的指数数据
        """
        pass

    @abstractmethod
    def get_sector_constituents(self, sector_code: str) -> List[str]:
        """
        获取板块成分股列表

        Args:
            sector_code: 板块代码（如同花顺概念板块代码）

        Returns:
            List[str]: 股票代码列表
        """
        pass

    # ---- FIR-23: Canonical Market Data Contract ----

    @abstractmethod
    def fetch_klines(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        frequency: FrequencyType = FrequencyType.DAILY,
        adjustment: AdjustmentType = AdjustmentType.NONE,
    ) -> MarketDataSeries:
        """
        获取K线数据（统一市场数据契约）

        Args:
            symbol: 证券代码（带市场前缀，如 sh600519）
            start_date: 开始日期 YYYY-MM-DD（可选）
            end_date: 结束日期 YYYY-MM-DD（可选）
            frequency: K线频率
            adjustment: 复权方式

        Returns:
            MarketDataSeries: 带 provenance 和质量报告的数据序列
        """
        pass

    @abstractmethod
    def validate_data(self, series: MarketDataSeries) -> DataQualityReport:
        """
        校验市场数据质量

        Args:
            series: 待校验的数据序列

        Returns:
            DataQualityReport: 质量报告
        """
        pass

    @abstractmethod
    def get_trading_calendar(self, exchange: str, start_date: str, end_date: str) -> List[str]:
        """
        获取交易日历

        Args:
            exchange: 交易所标识（如 'SSE'）
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            List[str]: 交易日列表（升序）
        """
        pass


class IndicatorCalculator(ABC):
    """
    技术指标计算器接口

    所有技术指标（MA、MACD、RSI、BOLL、KDJ等）必须实现此接口。
    """

    @abstractmethod
    def calculate(self, klines: List[OHLCV], **kwargs) -> Dict[str, Any]:
        """
        计算技术指标

        Args:
            klines: K线数据列表
            **kwargs: 指标参数（如 period=20）

        Returns:
            Dict[str, Any]: 指标结果，键为指标名，值为数组或标量
        """
        pass


class PivotDetector(ABC):
    """
    Pivot点检测器接口

    检测 swing-high / swing-low 结构点，是形态识别的基础。
    V5 新增，替代 V4 的宽泛 min/max 窗口。
    """

    @abstractmethod
    def detect(self, klines: List[OHLCV], left_bars: int = 5, right_bars: int = 5) -> List[PivotPoint]:
        """
        检测Pivot点

        Args:
            klines: K线数据列表
            left_bars: 左侧确认K线数
            right_bars: 右侧确认K线数

        Returns:
            List[PivotPoint]: Pivot点列表（按索引升序）
        """
        pass


class PatternDetector(ABC):
    """
    形态识别器接口

    所有形态（头肩底、W底、M头、头肩顶、平台突破等）必须实现此接口。
    """

    @property
    @abstractmethod
    def pattern_id(self) -> str:
        """形态唯一ID（如 "hs_bottom", "w_bottom"）"""
        pass

    @property
    @abstractmethod
    def pattern_name(self) -> str:
        """形态名称（如 "头肩底", "W底"）"""
        pass

    @abstractmethod
    def detect(self, klines: List[OHLCV], pivots: Optional[List[PivotPoint]] = None) -> List[PatternResult]:
        """
        识别形态

        Args:
            klines: K线数据列表
            pivots: 预计算的Pivot点列表（可选，如不提供则内部计算）

        Returns:
            List[PatternResult]: 识别到的形态列表（可能为空）
        """
        pass


class SignalGenerator(ABC):
    """
    信号生成器接口

    根据形态识别结果生成买卖信号。
    """

    @abstractmethod
    def generate(self, patterns: List[PatternResult], klines: List[OHLCV]) -> List[SignalResult]:
        """
        生成买卖信号

        Args:
            patterns: 形态识别结果列表
            klines: K线数据列表

        Returns:
            List[SignalResult]: 信号列表
        """
        pass


class MarketRegimeAnalyzer(ABC):
    """
    大盘环境分析器接口

    判断当前市场处于何种环境（牛市/熊市/震荡市/结构性行情）。
    """

    @abstractmethod
    def analyze(self, index_klines: List[OHLCV]) -> MarketRegime:
        """
        分析大盘环境

        Args:
            index_klines: 指数K线数据

        Returns:
            MarketRegime: 大盘环境判断结果
        """
        pass


class FakeSignalAnalyzer(ABC):
    """
    假信号分析器接口

    评估形态/信号的假突破/假破位风险。
    """

    @abstractmethod
    def analyze(self, pattern: PatternResult, klines: List[OHLCV]) -> FakeSignalRisk:
        """
        分析假信号风险

        Args:
            pattern: 形态识别结果
            klines: K线数据列表

        Returns:
            FakeSignalRisk: 假信号风险评估
        """
        pass


class StockAnalyzer(ABC):
    """
    股票分析器接口 — 顶层聚合接口

    整合数据获取、指标计算、形态识别、信号生成、大盘环境、假信号分析，
    输出完整的 AnalysisResult。
    """

    @abstractmethod
    def analyze(self, code: str, days: int = 250) -> AnalysisResult:
        """
        完整分析一只股票

        Args:
            code: 股票代码
            days: 分析天数（默认250个交易日）

        Returns:
            AnalysisResult: 完整分析结果
        """
        pass

    @abstractmethod
    def analyze_with_data(self, stock_data: StockData) -> AnalysisResult:
        """
        使用已有数据分析股票（用于回测，避免重复获取数据）

        Args:
            stock_data: 已有的股票数据

        Returns:
            AnalysisResult: 完整分析结果
        """
        pass


class BacktestEngine(ABC):
    """
    回测引擎接口

    使用固定 T+3/5/10/20/40 horizon 回测形态/信号的历史胜率。
    """

    @abstractmethod
    def backtest(self, code: str, signal_date: str, horizon_days: int = 20) -> BacktestResult:
        """
        回测单个信号

        Args:
            code: 股票代码
            signal_date: 信号日期 YYYY-MM-DD
            horizon_days: 观察窗口天数（3/5/10/20/40）

        Returns:
            BacktestResult: 类型化的回测结果（return、MFE、MAE等）
            future data 不足时返回 is_complete=False，字段为 None
        """
        pass

    @abstractmethod
    def batch_backtest(self, signals: List[Dict[str, Any]], horizon_days: int = 20) -> BacktestSummary:
        """
        批量回测，统计胜率

        只能比较相同 horizon 的样本。

        Args:
            signals: 信号列表，每个包含 code 和 date
            horizon_days: 观察窗口天数

        Returns:
            BacktestSummary: 类型化的统计结果（win_rate、avg_return、sample_size等）
        """
        pass


# ========== 异常定义 ==========

class CanonicalCoreError(Exception):
    """Canonical Core 基础异常"""
    pass


class DataProviderError(CanonicalCoreError):
    """数据提供者异常"""
    pass


class MarketDataError(CanonicalCoreError):
    """市场数据异常（FIR-23）"""
    pass


class PatternDetectionError(CanonicalCoreError):
    """形态识别异常"""
    pass


class AnalysisError(CanonicalCoreError):
    """分析异常"""
    pass


# ===== core/pivots.py =====
"""Deterministic swing-pivot detection primitives for V5.

This module is intentionally independent from the current production pattern
scoring paths. PILOT-001 establishes a canonical, testable primitive that later
V5 increments can use to migrate W/M/H&S recognition from broad-window extrema
to explicit ordered pivots.

Detection rule
--------------
For a candle at index ``i`` with ``left`` and ``right`` confirmation windows:

* swing high: its high is strictly greater than every high in both neighboring
  windows;
* swing low: its low is strictly lower than every low in both neighboring
  windows.

Strict comparison means equal-price plateaus are rejected rather than choosing
an arbitrary candle. Boundary candles without a complete confirmation window
are excluded. If one unusually wide candle qualifies simultaneously as both a
swing high and a swing low, it is conservatively rejected as ambiguous.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence


Pivot = Dict[str, Any]


def _as_float(candle: Mapping[str, Any], field: str, index: int) -> float:
    if field not in candle:
        raise ValueError(f"candle at index {index} missing required field: {field}")
    try:
        return float(candle[field])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"candle at index {index} has non-numeric {field}: {candle[field]!r}"
        ) from exc


def _date_value(candle: Mapping[str, Any]) -> Any:
    """Return the source date without inventing one.

    The project currently uses both ``day`` and ``date`` across execution paths,
    so the canonical pivot schema accepts either and returns ``None`` if neither
    exists.
    """

    if "day" in candle:
        return candle["day"]
    return candle.get("date")


def detect_swing_pivots(
    data: Sequence[Mapping[str, Any]],
    *,
    left: int = 2,
    right: int = 2,
) -> List[Pivot]:
    """Detect confirmed swing highs and lows in chronological order.

    Parameters
    ----------
    data:
        Sequence of OHLC-like mappings. ``high`` and ``low`` are required.
    left / right:
        Number of completed neighboring candles required on each side. Both
        values must be positive integers.

    Returns
    -------
    list[dict]
        Stable dictionaries with keys ``index``, ``type``, ``price`` and
        ``date``. ``type`` is ``swing_high`` or ``swing_low``.

    Notes
    -----
    This function deliberately performs *confirmed* pivot detection. The final
    ``right`` candles can therefore never be pivots at the time of evaluation,
    which prevents look-ahead ambiguity in later live/rolling integrations.
    """

    if isinstance(left, bool) or not isinstance(left, int) or left < 1:
        raise ValueError("left must be a positive integer")
    if isinstance(right, bool) or not isinstance(right, int) or right < 1:
        raise ValueError("right must be a positive integer")
    if data is None:
        raise ValueError("data must be a sequence, not None")

    n = len(data)
    if n < left + right + 1:
        return []

    highs = [_as_float(candle, "high", i) for i, candle in enumerate(data)]
    lows = [_as_float(candle, "low", i) for i, candle in enumerate(data)]

    pivots: List[Pivot] = []

    for i in range(left, n - right):
        high = highs[i]
        low = lows[i]

        left_highs = highs[i - left : i]
        right_highs = highs[i + 1 : i + right + 1]
        left_lows = lows[i - left : i]
        right_lows = lows[i + 1 : i + right + 1]

        is_swing_high = high > max(left_highs) and high > max(right_highs)
        is_swing_low = low < min(left_lows) and low < min(right_lows)

        # A single wide-range candle can mathematically satisfy both rules.
        # It is ambiguous for ordered-pattern construction, so reject it.
        if is_swing_high and is_swing_low:
            continue

        if is_swing_high:
            pivots.append(
                {
                    "index": i,
                    "type": "swing_high",
                    "price": high,
                    "date": _date_value(data[i]),
                }
            )
        elif is_swing_low:
            pivots.append(
                {
                    "index": i,
                    "type": "swing_low",
                    "price": low,
                    "date": _date_value(data[i]),
                }
            )

    return pivots



# ===== core/swing_pivot.py =====
"""
FIR-12 — Swing Pivot Engine (V5 Phase 1B)

第一套真正进入 Canonical Core 的算法，验证新架构能否解决三端逻辑漂移。

核心设计原则：
1. **确定性** — 相同输入永远产生相同输出，不依赖"视觉上看起来对"
2. **确认机制** — Pivot 只有在右侧有完整确认窗口后才被标记为 confirmed
3. **不可变性** — 已确认的历史 pivot 一旦确认就不可变，即使后续数据变化也不改变
4. **待确认可变性** — 待确认的 pivot（右侧窗口不完整）可能随新数据变化或消失
5. **严格 append-only contract** — update() 要求历史数据不可变，否则 raise HistoricalDataMutationError

关键属性（由测试证明）：
- 修改未来右侧 K 线，会影响尚未确认的 pivot，但不能改变已经确认的历史 pivot
- 已确认 pivot 携带 confirmed_at_index/date，支持 FIR-13 无未来函数回测

Version 语义说明：
- core.__version__ = "5.1.0" 是 Canonical Core internal/API version
- 不代表 GitHub product release 已经达到 V5.1.0
- 正式产品 Release 版本由 Git tag / GitHub Release 决定（如 v4.0.0）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set, Dict, Any, Tuple

from typing import OHLCV, PivotPoint
# interfaces inline PivotDetector


class HistoricalDataMutationError(Exception):
    """
    历史数据被修改/截断时抛出。

    当 update() 检测到：
    - 新数据长度小于上次处理长度（truncate）
    - 旧历史区间的 OHLC 或 date 发生变化（rewrite）
    时抛出此异常。

    调用方应调用 engine.reset() 后重新加载完整数据。
    """
    pass


@dataclass
class PivotUpdateResult:
    """Pivot 更新结果 — 增量更新的返回值"""
    new_confirmed: List[PivotPoint] = field(default_factory=list)  # 本次新增的已确认 pivot
    pending: List[PivotPoint] = field(default_factory=list)         # 当前待确认的 pivot
    all_confirmed: List[PivotPoint] = field(default_factory=list)   # 所有已确认的 pivot（历史 + 新增）

    @property
    def total_confirmed(self) -> int:
        return len(self.all_confirmed)

    @property
    def total_pending(self) -> int:
        return len(self.pending)


def _validate_bars_param(name: str, value: Any) -> int:
    """
    校验 left_bars / right_bars 参数。

    必须是 int >= 1，不允许 0 / negative / float / invalid value。

    Args:
        name: 参数名（用于错误消息）
        value: 参数值

    Returns:
        校验后的 int 值

    Raises:
        ValueError: 参数无效
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer, got {value!r} (type={type(value).__name__})")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


class SwingPivotEngine(PivotDetector):
    """
    Swing Pivot 检测引擎 — 有状态、确定性、带确认机制、严格 append-only

    与 PILOT-001 (core/pivots.py) 的区别：
    - PILOT-001 是无状态函数，每次重新计算所有 pivot
    - SwingPivotEngine 是有状态引擎，维护已确认 pivot 的不可变历史
    - SwingPivotEngine 区分 confirmed / pending pivot
    - SwingPivotEngine 支持增量更新（新K线到来时只处理新增部分）
    - SwingPivotEngine 严格要求 append-only，历史数据被修改时 raise HistoricalDataMutationError

    确认机制（修正 off-by-one 后）：
    - 一个 pivot 在索引 i 处，需要左侧 left_bars 根 + 右侧 right_bars 根 K 线确认
    - 只要数据中已经存在 right_bars 根右侧 K 线，就应当 confirmed
    - 即当 (i + right_bars) <= (n - 1) 时，confirmed=True
    - 当 (i + right_bars) > (n - 1) 时，confirmed=False（待确认）

    Point-in-Time confirmation metadata：
    - 已确认 pivot：confirmed_at_index = i + right_bars，confirmed_at_date = klines[i + right_bars].date
    - 待确认 pivot：confirmed_at_index = None，confirmed_at_date = None
    - 这是该 Pivot 最早可被交易系统知道已经成立的时间，支持 FIR-13 无未来函数回测
    """

    def __init__(self, left_bars: int = 5, right_bars: int = 5):
        """
        初始化 Swing Pivot 引擎

        Args:
            left_bars: 左侧确认K线数（必须 int >= 1）
            right_bars: 右侧确认K线数（必须 int >= 1）
        """
        self.left_bars = _validate_bars_param("left_bars", left_bars)
        self.right_bars = _validate_bars_param("right_bars", right_bars)

        # 有状态：已确认的 pivot 历史（不可变）
        self._confirmed_pivots: List[PivotPoint] = []
        self._confirmed_indices: Set[int] = set()  # 快速查找已确认的索引

        # 记录上次处理到的数据长度，用于增量更新和 append-only 校验
        self._last_data_length: int = 0

        # 保存上次已处理历史数据的 fingerprint（用于 append-only 校验）
        # fingerprint = tuple of (date, open, high, low, close, volume) for each bar
        self._last_history_fingerprint: Optional[Tuple[Tuple, ...]] = None

    def reset(self) -> None:
        """重置引擎状态，清除所有已确认的 pivot 和历史 fingerprint"""
        self._confirmed_pivots = []
        self._confirmed_indices = set()
        self._last_data_length = 0
        self._last_history_fingerprint = None

    @property
    def confirmed_pivots(self) -> List[PivotPoint]:
        """所有已确认的 pivot（不可变历史）"""
        return list(self._confirmed_pivots)  # 返回副本，防止外部修改

    def _compute_fingerprint(self, klines: List[OHLCV]) -> Tuple[Tuple, ...]:
        """
        计算历史数据的 fingerprint，用于 append-only 校验。

        Args:
            klines: K线数据

        Returns:
            tuple of (date, open, high, low, close, volume) for each bar
        """
        return tuple(
            (k.date, k.open, k.high, k.low, k.close, k.volume)
            for k in klines
        )

    def _validate_append_only(self, klines: List[OHLCV]) -> None:
        """
        严格 append-only contract 校验。

        检查：
        1. 新长度不能小于旧长度（truncate → reject）
        2. 旧历史区间的数据不能发生变化（rewrite → reject）

        如果发现历史被 rewrite/truncate，raise HistoricalDataMutationError。

        Args:
            klines: 新的完整K线数据

        Raises:
            HistoricalDataMutationError: 历史数据被修改或截断
        """
        n = len(klines)
        old_n = self._last_data_length

        # 第一次 update 或 reset 后，没有历史可比较
        if old_n == 0 or self._last_history_fingerprint is None:
            return

        # 检查 1: 新长度不能小于旧长度（truncate）
        if n < old_n:
            raise HistoricalDataMutationError(
                f"Historical data truncated: previous length={old_n}, "
                f"current length={n}. Call engine.reset() and reload full data."
            )

        # 检查 2: 旧历史区间的数据不能发生变化（rewrite）
        new_fingerprint = self._compute_fingerprint(klines[:old_n])
        if new_fingerprint != self._last_history_fingerprint:
            # 找出第一个不同的位置，用于错误消息
            diff_idx = None
            for i in range(min(old_n, n)):
                if new_fingerprint[i] != self._last_history_fingerprint[i]:
                    diff_idx = i
                    break
            raise HistoricalDataMutationError(
                f"Historical data mutated at index {diff_idx}: "
                f"old={self._last_history_fingerprint[diff_idx] if diff_idx is not None else 'N/A'}, "
                f"new={new_fingerprint[diff_idx] if diff_idx is not None else 'N/A'}. "
                f"Call engine.reset() and reload full data."
            )

    def _make_pivot(
        self,
        klines: List[OHLCV],
        i: int,
        pivot_type: str,
        confirmed: bool,
    ) -> PivotPoint:
        """
        创建 PivotPoint 对象，包含 point-in-time confirmation metadata。

        Args:
            klines: K线数据
            i: pivot 索引
            pivot_type: "swing_high" / "swing_low"
            confirmed: 是否已确认

        Returns:
            PivotPoint 对象
        """
        price = klines[i].high if pivot_type == "swing_high" else klines[i].low

        # Point-in-Time confirmation metadata
        if confirmed:
            confirmed_at_index = i + self.right_bars
            confirmed_at_date = klines[confirmed_at_index].date
        else:
            confirmed_at_index = None
            confirmed_at_date = None

        return PivotPoint(
            index=i,
            date=klines[i].date,
            price=price,
            pivot_type=pivot_type,
            strength=min(self.left_bars, self.right_bars),
            confirmed=confirmed,
            confirmed_at_index=confirmed_at_index,
            confirmed_at_date=confirmed_at_date,
        )

    def _scan_range(self, klines: List[OHLCV], start: int, end: int) -> List[PivotPoint]:
        """
        扫描 [start, end) 范围内的 pivot（无状态计算）

        确认机制（修正 off-by-one 后）：
        - 已确认 pivot (confirmed=True)：左侧窗口完整 + 右侧有至少 right_bars 根K线
          即 (i + right_bars) <= (n - 1)
        - 待确认 pivot (confirmed=False)：左侧窗口完整 + 右侧窗口不完整（少于 right_bars 根），
          但基于已有右侧数据满足 swing high/low 条件
          即 (i + right_bars) > (n - 1)

        Args:
            klines: K线数据
            start: 起始索引（包含）
            end: 结束索引（不包含）

        Returns:
            扫描到的 pivot 列表（已确认 + 待确认）
        """
        n = len(klines)
        # 至少需要左侧窗口 + 1根当前K线 + 1根右侧K线
        if n < self.left_bars + 2:
            return []

        highs = [k.high for k in klines]
        lows = [k.low for k in klines]

        pivots = []
        # 有效范围：i >= left_bars（左侧窗口完整），i < n - 1（至少有1根右侧K线）
        effective_start = max(start, self.left_bars)
        effective_end = min(end, n - 1)

        for i in range(effective_start, effective_end):
            high = highs[i]
            low = lows[i]

            # 左侧窗口（必须完整）
            left_highs = highs[i - self.left_bars:i]
            left_lows = lows[i - self.left_bars:i]

            # 右侧窗口（可能不完整，取已有的部分）
            right_end = min(i + self.right_bars + 1, n)
            right_highs = highs[i + 1:right_end]
            right_lows = lows[i + 1:right_end]

            # 右侧窗口至少需要1根K线
            if not right_highs or not right_lows:
                continue

            # 判断是否满足条件（基于已有的左侧和右侧数据）
            is_swing_high = high > max(left_highs) and high > max(right_highs)
            is_swing_low = low < min(left_lows) and low < min(right_lows)

            # 同时满足 high 和 low 的宽幅K线，保守拒绝为歧义
            if is_swing_high and is_swing_low:
                continue

            # 判断是否已确认（修正 off-by-one）：
            # 只要数据中已经存在 right_bars 根右侧 K 线，就应当 confirmed
            # 即当 (i + right_bars) <= (n - 1) 时，confirmed=True
            confirmed = (i + self.right_bars) <= (n - 1)

            if is_swing_high:
                pivots.append(self._make_pivot(klines, i, "swing_high", confirmed))
            elif is_swing_low:
                pivots.append(self._make_pivot(klines, i, "swing_low", confirmed))

        return pivots

    def update(self, klines: List[OHLCV]) -> PivotUpdateResult:
        """
        增量更新：处理新到来的K线，确认新的 pivot。

        严格 append-only contract：
        - 新长度不能小于旧长度（truncate → raise HistoricalDataMutationError）
        - 旧历史区间的数据不能发生变化（rewrite → raise HistoricalDataMutationError）
        - 如果发现历史被修改，调用方应 engine.reset() 后重新加载完整数据

        核心属性：
        - 已确认的历史 pivot 不可变，即使后续数据变化也不改变
        - 待确认的 pivot 可能随新数据变化或消失
        - 只处理新增的K线（从 _last_data_length 开始）

        Args:
            klines: 完整的K线数据（包含历史 + 新增）

        Returns:
            PivotUpdateResult: 更新结果（新增已确认 + 当前待确认 + 所有已确认）

        Raises:
            HistoricalDataMutationError: 历史数据被修改或截断
        """
        n = len(klines)

        # 严格 append-only 校验
        self._validate_append_only(klines)

        # 至少需要左侧窗口 + 1根当前K线 + 1根右侧K线
        # 注意：对于 pending pivot，不需要完整的 right_bars 窗口
        if n < self.left_bars + 2:
            # 数据不足，更新 fingerprint 和长度，返回空结果
            self._last_data_length = n
            self._last_history_fingerprint = self._compute_fingerprint(klines)
            return PivotUpdateResult(
                new_confirmed=[],
                pending=[],
                all_confirmed=list(self._confirmed_pivots),
            )

        # 扫描从上次处理位置开始的所有可能 pivot
        # 注意：需要从 _last_data_length - right_bars - 1 开始，
        # 因为之前待确认的 pivot 可能在新数据到来后被确认
        # end 传 n，让 _scan_range 自己决定有效范围（包含 pending）
        scan_start = max(0, self._last_data_length - self.right_bars - 1)
        newly_scanned = self._scan_range(klines, scan_start, n)

        # 分离新增已确认和待确认
        new_confirmed = []
        pending = []

        for pivot in newly_scanned:
            if pivot.confirmed and pivot.index not in self._confirmed_indices:
                # 新确认的 pivot
                self._confirmed_pivots.append(pivot)
                self._confirmed_indices.add(pivot.index)
                new_confirmed.append(pivot)
            elif not pivot.confirmed:
                # 待确认的 pivot
                pending.append(pivot)

        # 按索引排序（确保顺序正确）
        new_confirmed.sort(key=lambda p: p.index)
        pending.sort(key=lambda p: p.index)
        self._confirmed_pivots.sort(key=lambda p: p.index)

        # 更新上次处理位置和 fingerprint
        self._last_data_length = n
        self._last_history_fingerprint = self._compute_fingerprint(klines)

        return PivotUpdateResult(
            new_confirmed=new_confirmed,
            pending=pending,
            all_confirmed=list(self._confirmed_pivots),
        )

    def detect(
        self,
        klines: List[OHLCV],
        left_bars: Optional[int] = None,
        right_bars: Optional[int] = None,
    ) -> List[PivotPoint]:
        """
        实现 PivotDetector 接口 — 检测 pivot 点

        无状态检测：每次都重新计算所有 pivot（已确认 + 待确认）。
        不修改引擎内部状态。

        参数 override 校验：
        - 自定义 left_bars / right_bars 也必须走与 constructor 相同的校验
        - 必须 int >= 1，不允许 0 / negative / float / invalid value

        Args:
            klines: K线数据列表
            left_bars: 左侧确认K线数（如不提供使用引擎默认值）
            right_bars: 右侧确认K线数（如不提供使用引擎默认值）

        Returns:
            List[PivotPoint]: Pivot点列表（按索引升序，包含 confirmed 和 pending）
        """
        # 参数 override 校验（与 constructor 相同的校验逻辑）
        if left_bars is not None:
            left_bars = _validate_bars_param("left_bars", left_bars)
        if right_bars is not None:
            right_bars = _validate_bars_param("right_bars", right_bars)

        # 临时使用自定义参数（不修改引擎状态）
        old_left = self.left_bars
        old_right = self.right_bars

        if left_bars is not None:
            self.left_bars = left_bars
        if right_bars is not None:
            self.right_bars = right_bars

        try:
            # 无状态扫描整个范围
            result = self._scan_range(klines, 0, len(klines))
            return result
        finally:
            # 恢复引擎默认参数
            self.left_bars = old_left
            self.right_bars = old_right

    def detect_confirmed_only(self, klines: List[OHLCV]) -> List[PivotPoint]:
        """
        只返回已确认的 pivot（无状态检测）

        Args:
            klines: K线数据列表

        Returns:
            List[PivotPoint]: 已确认的 pivot 列表
        """
        all_pivots = self.detect(klines)
        return [p for p in all_pivots if p.confirmed]

    def detect_pending_only(self, klines: List[OHLCV]) -> List[PivotPoint]:
        """
        只返回待确认的 pivot（无状态检测）

        Args:
            klines: K线数据列表

        Returns:
            List[PivotPoint]: 待确认的 pivot 列表
        """
        all_pivots = self.detect(klines)
        return [p for p in all_pivots if not p.confirmed]


# ===== core/pivot_patterns.py =====
"""
FIR-12 Pattern Migration — 基于 SwingPivotEngine 的经典形态识别器

四个经典反转形态的 V5 canonical 实现：

- WBottomDetector   W底/双底   (pattern_id="w_bottom",  方向 BULL)
- MTopDetector      M头/双头   (pattern_id="m_top",      方向 BEAR)
- HSBottomDetector  头肩底     (pattern_id="hs_bottom",  方向 BULL，支持倾斜颈线)
- HSTopDetector     头肩顶     (pattern_id="hs_top",      方向 BEAR，支持倾斜颈线)

设计要点（与 V4 的区别）：
1. **只使用已确认的 pivot**（detect_confirmed_only），pending pivot 不参与形态构建，
   从数据源头上杜绝未来函数。
2. **颈线**：W/M 使用水平颈线（两底/顶之间的摆动极值）；H&S 使用左、右两个颈线
   点连线，支持向上/向下倾斜的动态颈线。
3. **突破状态机**（四个形态共用，由基类实现）：
   FORMING → PRE_BREAK → BREAKOUT_DAY → POST_BREAK / RETEST / FAILED_BREAK
4. **评分 0-100**：结构分 + 突破分 + 量能分（H&S 为结构三项 + 突破分），
   FAILED_BREAK 扣 30 分（最低 10 分）。
5. 所有 detector 通过 @registry.pattern 注册到全局注册中心。
"""

from __future__ import annotations

from typing import List, Optional, Callable

from typing import (
    OHLCV,
    PivotPoint,
    PatternResult,
    PatternKeyPoint,
    TrendDirection,
    PatternStage,
    ConfidenceLevel,
)
# interfaces inline PatternDetector
from .swing_pivot import SwingPivotEngine
from .registry import registry


# ============================================================
# FIR-24: H&S Guardrails（证据驱动阈值）
# ============================================================
#
# 阈值来源：scripts/fir24_hs_prominence_analysis.py 在全部缓存 qfq 数据
# （data/calibration/*_qfq.json，约 74 只股票；统计时临时中和本 guardrail，
# 收集到所有通过既有结构闸门的候选，n=738 个 nl_now>0 的 H&S 候选）上统计：
#
#   head_prominence 分布（|head - avg_shoulder|/avg_shoulder，avg_shoulder=(ls+rs)/2）：
#     P10=1.47%  P25=2.71%  P50=5.29%  P75=8.71%  P90=15.01%
#     mean=6.94%  std=6.03%  min=0.33%  max=47.47%
#   按任务规则「max(P25, median/2, floor 3%)」= max(2.71%, 2.65%, 3%) = 3%。
#   该阈值拒绝「平头」候选（如 002415 某候选 prominence=2.72%），即头部与双肩
#   几乎齐平、根本不是 H&S 而是普通双底/震荡的结构。
#
#   颈线外推比率分布（bottom: nl_now/head；top: head/nl_now，n=738，nl_now>0）：
#     P50=1.12  P75=1.93  P90=3.37  P95=5.74  P99=12.55  max=69.9
#   标准健康 fixture 的外推比率 ≈ 1.19（颈线仅比头部高 ~19%）。
#   倾斜颈线一旦斜率较陡，外推到当前 bar 会漂移到远离结构的无意义水平
#   （如 002415 弱候选 nl_now/head=1.82，外推颈线 53.04 远高于现价 32.46；
#   退化候选甚至到 10.8x / 69.9x）。取 1.5：颈线最多比头部高/低 50%，
#   超出即拒绝——健康结构（~1.1-1.3）不受影响。
MIN_HS_HEAD_PROMINENCE: float = 0.03
MAX_NECKLINE_EXTRAPOLATION_RATIO: float = 1.5


def _avg_volume(klines: List[OHLCV], window: int) -> float:
    """计算最近 window 根K线的平均成交量（数据不足时用全部已有数据）"""
    if not klines:
        return 0.0
    seg = klines[-window:] if window < len(klines) else klines
    return sum(k.volume for k in seg) / len(seg)


class PivotPatternDetectorBase(PatternDetector):
    """
    形态识别器基类 — 封装所有四个形态共用的逻辑：

    - pivot 获取（默认内部用 SwingPivotEngine(left=5, right=5) 只取已确认 pivot）
    - 量能确认判断（最近5日均量 > 20日均量 * 1.1）
    - 突破/跌破状态机
    - 置信度映射
    """

    #: pivot 引擎左右窗口参数（子类可覆盖）
    left_bars: int = 5
    right_bars: int = 5

    # ---------- pivot 获取 ----------

    def _get_confirmed_pivots(
        self,
        klines: List[OHLCV],
        pivots: Optional[List[PivotPoint]],
    ) -> List[PivotPoint]:
        """
        获取已确认 pivot。

        - pivots 为 None 时：内部用 SwingPivotEngine(left=5, right=5).detect_confirmed_only()
        - pivots 传入时：过滤出 confirmed=True 的 pivot（pending 不参与形态识别，
          保证与内部计算一致，避免未来函数）
        """
        if pivots is None:
            engine = SwingPivotEngine(left_bars=self.left_bars, right_bars=self.right_bars)
            return engine.detect_confirmed_only(klines)

        n = len(klines)
        result: List[PivotPoint] = []
        for p in pivots:
            # 1) 必须是已确认 pivot（pending 不参与形态识别）
            if not p.confirmed:
                continue
            # 2) 必须携带 point-in-time 确认元数据
            if p.confirmed_at_index is None:
                continue
            # 3) 确认时点不能晚于当前 prefix 的最后一根K线 —— 否则引入未来函数
            if p.confirmed_at_index > n - 1:
                continue
            # 4) pivot index/date 必须属于当前 series/prefix，防止跨 series 错位
            if p.index < 0 or p.index >= n:
                continue
            if p.date != klines[p.index].date:
                continue
            result.append(p)
        return result

    # ---------- 量能 ----------

    def _volume_confirmed(self, klines: List[OHLCV]) -> bool:
        """量能确认：最近5日均量 > 最近20日均量 * 1.1"""
        v20 = _avg_volume(klines, 20)
        v5 = _avg_volume(klines, 5)
        if v20 <= 0:
            return False
        return v5 > v20 * 1.1

    # ---------- 置信度 ----------

    @staticmethod
    def _confidence(score: int) -> ConfidenceLevel:
        """评分 → 置信度映射：≥80 S / ≥65 A / ≥50 B / ≥35 C / <35 D"""
        if score >= 80:
            return ConfidenceLevel.S
        if score >= 65:
            return ConfidenceLevel.A
        if score >= 50:
            return ConfidenceLevel.B
        if score >= 35:
            return ConfidenceLevel.C
        return ConfidenceLevel.D

    # ---------- 颈线突破状态机 ----------

    def _classify_stage(
        self,
        klines: List[OHLCV],
        shape_done_idx: int,
        neckline_fn: Callable[[int], float],
        bullish: bool,
        breakout_mult: float,
    ) -> PatternStage:
        """
        通用突破/跌破状态机。

        Args:
            klines: K线列表
            shape_done_idx: 形态结构完成的最后一个结构点索引（如 W底的右底、
                            H&S的右肩），只在这之后判断突破
            neckline_fn: 给定K线索引，返回该索引处的颈线值
                         （水平颈线为常数函数；倾斜颈线为斜率插值函数）
            bullish: True=看涨形态（向上突破颈线）；False=看跌形态（向下跌破颈线）
            breakout_mult: 突破确认倍数
                           （看涨：收盘 > 颈线 * mult；看跌：收盘 < 颈线 * mult）

        Returns:
            PatternStage 形态阶段
        """
        n = len(klines)
        last = n - 1
        closes = [k.close for k in klines]
        nl_last = neckline_fn(last)

        # 1) 寻找突破/跌破日：前一日收盘在颈线正确一侧，当日收盘越过颈线 * mult
        cross_idx: Optional[int] = None
        for i in range(shape_done_idx + 1, n):
            nl_i = neckline_fn(i)
            nl_prev = neckline_fn(i - 1)
            if bullish:
                crossed = closes[i] > nl_i * breakout_mult and closes[i - 1] <= nl_prev
            else:
                crossed = closes[i] < nl_i * breakout_mult and closes[i - 1] >= nl_prev
            if crossed:
                cross_idx = i
                break

        # 2) 尚未突破：价格接近颈线 ±3% → PRE_BREAK，否则 FORMING
        if cross_idx is None:
            if nl_last > 0 and abs(closes[last] - nl_last) / nl_last <= 0.03:
                return PatternStage.PRE_BREAK
            return PatternStage.FORMING

        # 3) 突破当日
        if cross_idx == last:
            return PatternStage.BREAKOUT_DAY

        if bullish:
            # 3a) 突破失败：突破后连续 ≥3 日收盘跌破颈线，且当前价在颈线下方
            below_streak = 0
            for j in range(last, cross_idx, -1):
                if closes[j] < neckline_fn(j):
                    below_streak += 1
                else:
                    break
            if below_streak >= 3:
                return PatternStage.FAILED_BREAK

            # 3b) 回踩：突破后最低价触及颈线 ±2% 区间，且当前收盘在颈线上方 8% 内
            touched = any(
                neckline_fn(j) * 0.98 <= klines[j].low <= neckline_fn(j) * 1.02
                for j in range(cross_idx, n)
            )
            in_band = nl_last < closes[last] <= nl_last * 1.08
            if touched and in_band:
                return PatternStage.RETEST
            return PatternStage.POST_BREAK
        else:
            # 看跌形态对称：跌破失败 = 连续 ≥3 日收盘站回颈线上方
            above_streak = 0
            for j in range(last, cross_idx, -1):
                if closes[j] > neckline_fn(j):
                    above_streak += 1
                else:
                    break
            if above_streak >= 3:
                return PatternStage.FAILED_BREAK

            # 回抽：跌破后最高价触及颈线 ±2%，且当前收盘在颈线下方 8% 内
            touched = any(
                neckline_fn(j) * 0.98 <= klines[j].high <= neckline_fn(j) * 1.02
                for j in range(cross_idx, n)
            )
            in_band = nl_last * 0.92 <= closes[last] < nl_last
            if touched and in_band:
                return PatternStage.RETEST
            return PatternStage.POST_BREAK

    # ---------- 颈线点定位辅助 ----------

    @staticmethod
    def _pick_extreme_pivot(
        candidates: List[PivotPoint],
        pick_max: bool,
    ) -> Optional[PivotPoint]:
        """从候选 pivot 中取价格最高/最低者；返回副本（含原始 index/date）"""
        if not candidates:
            return None
        if pick_max:
            return max(candidates, key=lambda p: p.price)
        return min(candidates, key=lambda p: p.price)

# ============================================================
# W Bottom — W底/双底（看涨）
# ============================================================

@registry.pattern
class WBottomDetector(PivotPatternDetectorBase):
    """
    W底/双底识别器

    判断条件：
    1. 两个相邻的已确认 swing low（左底 a、右底 b）
    2. 两底价格差距 ≤ 5%
    3. 右底不低于左底的 97%（不创新低）
    4. 颈线 = 两底之间最高的 swing high（水平颈线）
    5. 突破确认：当前收盘 > 颈线 * 1.01

    目标价 = 颈线 + (颈线 - 两底最低价)
    止损   = 右底最低价 * 0.98
    """

    @property
    def pattern_id(self) -> str:
        return "w_bottom"

    @property
    def pattern_name(self) -> str:
        return "W底"

    def detect(self, klines: List[OHLCV], pivots: Optional[List[PivotPoint]] = None) -> List[PatternResult]:
        results: List[PatternResult] = []
        if len(klines) < 15:
            return results

        confirmed = self._get_confirmed_pivots(klines, pivots)
        lows = sorted([p for p in confirmed if p.pivot_type == "swing_low"],
                      key=lambda p: p.index)
        highs = sorted([p for p in confirmed if p.pivot_type == "swing_high"],
                       key=lambda p: p.index)

        # 遍历相邻的一对 swing low（左底、右底）
        for a, b in zip(lows, lows[1:]):
            # --- 形态闸门 ---
            # 右底不低于左底 97%
            if b.price < a.price * 0.97:
                continue
            # 两底价格差距 ≤ 5%
            gap_pct = abs(b.price - a.price) / min(a.price, b.price)
            if gap_pct > 0.05:
                continue

            # --- 颈线：两底之间最高的 swing high ---
            between = [h for h in highs if a.index < h.index < b.index]
            if not between:
                # 缺少对立 pivot（两底之间无已确认 swing high）：拒绝该候选。
                # 不允许退化到无限制的原始 K 线极值（违反 FIR-12 acceptance）。
                continue
            nl_pt = self._pick_extreme_pivot(between, pick_max=True)
            neckline = nl_pt.price

            # 水平颈线：常数函数
            nl_fn = lambda idx, nl=neckline: nl

            stage = self._classify_stage(klines, b.index, nl_fn, bullish=True, breakout_mult=1.01)
            broken = klines[-1].close > neckline * 1.01

            low_min = min(a.price, b.price)
            target = neckline + (neckline - low_min)
            stop = b.price * 0.98

            # --- 评分：两底接近40 + 突破35/15 + 量能25/10 ---
            score = 40 + (35 if broken else 15) + (25 if self._volume_confirmed(klines) else 10)
            if stage == PatternStage.FAILED_BREAK:
                score = max(10, score - 30)

            key_points = [
                PatternKeyPoint("左底", a.index, a.date, a.price, "第一个摆动低点"),
                PatternKeyPoint("右底", b.index, b.date, b.price, "第二个摆动低点（不创新低）"),
                PatternKeyPoint("颈线点", nl_pt.index, nl_pt.date, neckline,
                                "两底之间的最高点"),
            ]
            results.append(PatternResult(
                pattern_id=self.pattern_id,
                pattern_name=self.pattern_name,
                direction=TrendDirection.BULL,
                stage=stage,
                score=score,
                confidence=self._confidence(score),
                key_points=key_points,
                neckline_price=round(neckline, 4),
                breakout_price=round(klines[-1].close, 4) if broken else None,
                target_price=round(target, 4),
                stop_loss=round(stop, 4),
                notes=f"W底：左底{a.price:.2f} 右底{b.price:.2f} 颈线{neckline:.2f}",
            ))
        return results


# ============================================================
# M Top — M头/双头（看跌）
# ============================================================

@registry.pattern
class MTopDetector(PivotPatternDetectorBase):
    """
    M头/双头识别器（与 W Bottom 对称）

    判断条件：
    1. 两个相邻的已确认 swing high（左顶 a、右顶 b）
    2. 两顶价格差距 ≤ 5%
    3. 右顶不高于左顶的 103%（不过度创新高）
    4. 颈线 = 两顶之间最低的 swing low（水平颈线）
    5. 跌破确认：当前收盘 < 颈线 * 0.99

    目标价 = 颈线 - (两顶最高价 - 颈线)
    止损   = 右顶最高价 * 1.02
    """

    @property
    def pattern_id(self) -> str:
        return "m_top"

    @property
    def pattern_name(self) -> str:
        return "M头"

    def detect(self, klines: List[OHLCV], pivots: Optional[List[PivotPoint]] = None) -> List[PatternResult]:
        results: List[PatternResult] = []
        if len(klines) < 15:
            return results

        confirmed = self._get_confirmed_pivots(klines, pivots)
        highs = sorted([p for p in confirmed if p.pivot_type == "swing_high"],
                       key=lambda p: p.index)
        lows = sorted([p for p in confirmed if p.pivot_type == "swing_low"],
                      key=lambda p: p.index)

        for a, b in zip(highs, highs[1:]):
            # --- 形态闸门 ---
            # 右顶不高于左顶 103%
            if b.price > a.price * 1.03:
                continue
            # 两顶价格差距 ≤ 5%
            gap_pct = abs(b.price - a.price) / max(a.price, b.price)
            if gap_pct > 0.05:
                continue

            # --- 颈线：两顶之间最低的 swing low ---
            between = [l for l in lows if a.index < l.index < b.index]
            if not between:
                # 缺少对立 pivot（两顶之间无已确认 swing low）：拒绝该候选
                continue
            nl_pt = self._pick_extreme_pivot(between, pick_max=False)
            neckline = nl_pt.price

            nl_fn = lambda idx, nl=neckline: nl

            stage = self._classify_stage(klines, b.index, nl_fn, bullish=False, breakout_mult=0.99)
            broken = klines[-1].close < neckline * 0.99

            top_max = max(a.price, b.price)
            target = neckline - (top_max - neckline)
            stop = b.price * 1.02

            # --- 评分：两顶对称40 + 跌破35/15 + 量能25/10（与W底对称） ---
            score = 40 + (35 if broken else 15) + (25 if self._volume_confirmed(klines) else 10)
            if stage == PatternStage.FAILED_BREAK:
                score = max(10, score - 30)

            key_points = [
                PatternKeyPoint("左顶", a.index, a.date, a.price, "第一个摆动高点"),
                PatternKeyPoint("右顶", b.index, b.date, b.price, "第二个摆动高点（不过度创新高）"),
                PatternKeyPoint("颈线点", nl_pt.index, nl_pt.date, neckline,
                                "两顶之间的最低点"),
            ]
            results.append(PatternResult(
                pattern_id=self.pattern_id,
                pattern_name=self.pattern_name,
                direction=TrendDirection.BEAR,
                stage=stage,
                score=score,
                confidence=self._confidence(score),
                key_points=key_points,
                neckline_price=round(neckline, 4),
                breakout_price=round(klines[-1].close, 4) if broken else None,
                target_price=round(target, 4),
                stop_loss=round(stop, 4),
                notes=f"M头：左顶{a.price:.2f} 右顶{b.price:.2f} 颈线{neckline:.2f}",
            ))
        return results


# ============================================================
# H&S Bottom — 头肩底（看涨，倾斜颈线）
# ============================================================

@registry.pattern
class HSBottomDetector(PivotPatternDetectorBase):
    """
    头肩底识别器（支持倾斜颈线）

    判断条件：
    1. 三个相邻的已确认 swing low：左肩 ls、头部 h、右肩 rs
    2. 头部低于左肩：h.price < ls.price
    3. 右肩高于头部：rs.price > h.price
    4. 左右肩对称：|ls.price - rs.price| / ls.price ≤ 20%
    5. 颈线：左肩→头部之间最高的 swing high（左颈线点）与
       头部→右肩之间最高的 swing high（右颈线点）连线，支持倾斜
       斜率 = (右颈线价 - 左颈线价) / (右颈线索引 - 左颈线索引)
       当前颈线值 = 左颈线价 + 斜率 * (当前索引 - 左颈线索引)
    6. 突破确认：当前收盘 > 当前颈线值 * 0.98

    目标价 = 当前颈线值 + (当前颈线值 - 头部价)
    止损   = 头部价 * 0.98
    """

    @property
    def pattern_id(self) -> str:
        return "hs_bottom"

    @property
    def pattern_name(self) -> str:
        return "头肩底"

    def detect(self, klines: List[OHLCV], pivots: Optional[List[PivotPoint]] = None) -> List[PatternResult]:
        results: List[PatternResult] = []
        if len(klines) < 25:
            return results

        confirmed = self._get_confirmed_pivots(klines, pivots)
        lows = sorted([p for p in confirmed if p.pivot_type == "swing_low"],
                      key=lambda p: p.index)
        highs = sorted([p for p in confirmed if p.pivot_type == "swing_high"],
                       key=lambda p: p.index)

        # 遍历相邻三连 swing low：左肩、头、右肩
        for ls, head, rs in zip(lows, lows[1:], lows[2:]):
            # --- 形态闸门 ---
            # 头部必须低于左肩
            if head.price >= ls.price:
                continue
            # 右肩必须高于头部
            if rs.price <= head.price:
                continue
            # 左右肩价格差距 ≤ 20%
            if ls.price <= 0 or abs(ls.price - rs.price) / ls.price > 0.20:
                continue

            # --- FIR-24 guardrail 1a：最小头-肩突出度 ---
            # head 必须相对双肩平均价有足够「突出」，否则是平头/普通双底而非 H&S。
            avg_shoulder = (ls.price + rs.price) / 2.0
            if avg_shoulder <= 0:
                continue
            head_prominence = abs(head.price - avg_shoulder) / avg_shoulder
            if head_prominence < MIN_HS_HEAD_PROMINENCE:
                continue

            # --- 颈线两点 ---
            left_between = [h for h in highs if ls.index < h.index < head.index]
            if not left_between:
                # 左颈线区间缺少已确认对立 pivot：拒绝该候选
                continue
            ln = self._pick_extreme_pivot(left_between, pick_max=True)

            right_between = [h for h in highs if head.index < h.index < rs.index]
            if not right_between:
                # 右颈线区间缺少已确认对立 pivot：拒绝该候选
                continue
            rn = self._pick_extreme_pivot(right_between, pick_max=True)

            # 倾斜颈线：线性插值
            if rn.index != ln.index:
                slope = (rn.price - ln.price) / (rn.index - ln.index)
            else:
                slope = 0.0
            nl_fn = lambda idx, ln=ln, slope=slope: ln.price + slope * (idx - ln.index)

            stage = self._classify_stage(klines, rs.index, nl_fn, bullish=True, breakout_mult=0.98)
            nl_now = nl_fn(len(klines) - 1)

            # --- FIR-24 guardrail 1b：倾斜颈线外推合理性 ---
            # 外推颈线必须为正；倾斜颈线（slope != 0）外推值不能远离结构
            # （不能比头部高过 MAX_NECKLINE_EXTRAPOLATION_RATIO 倍）。
            # 水平颈线（slope == 0）外推=常数，不受此限制。
            if nl_now <= 0:
                continue
            if slope != 0.0 and nl_now > head.price * MAX_NECKLINE_EXTRAPOLATION_RATIO:
                continue

            broken = klines[-1].close > nl_now * 0.98

            target = nl_now + (nl_now - head.price)
            stop = head.price * 0.98

            # --- 评分：左肩高于头20 + 右肩高于头20 + 左右肩对称20 + 突破40/10 ---
            score = 20 + 20 + 20 + (40 if broken else 10)
            if stage == PatternStage.FAILED_BREAK:
                score = max(10, score - 30)

            key_points = [
                PatternKeyPoint("左肩", ls.index, ls.date, ls.price, "左肩摆动低点"),
                PatternKeyPoint("头", head.index, head.date, head.price, "头部摆动低点（最低）"),
                PatternKeyPoint("右肩", rs.index, rs.date, rs.price, "右肩摆动低点（高于头部）"),
                PatternKeyPoint("左颈线点", ln.index, ln.date, ln.price,
                                "左肩→头部之间的反弹高点"),
                PatternKeyPoint("右颈线点", rn.index, rn.date, rn.price,
                                "头部→右肩之间的反弹高点"),
            ]
            results.append(PatternResult(
                pattern_id=self.pattern_id,
                pattern_name=self.pattern_name,
                direction=TrendDirection.BULL,
                stage=stage,
                score=score,
                confidence=self._confidence(score),
                key_points=key_points,
                neckline_price=round(nl_now, 4),
                breakout_price=round(klines[-1].close, 4) if broken else None,
                target_price=round(target, 4),
                stop_loss=round(stop, 4),
                notes=f"头肩底：左肩{ls.price:.2f} 头{head.price:.2f} "
                      f"右肩{rs.price:.2f} 颈线{ln.price:.2f}→{rn.price:.2f}(斜率{slope:.4f})",
            ))
        return results


# ============================================================
# H&S Top — 头肩顶（看跌，倾斜颈线）
# ============================================================

@registry.pattern
class HSTopDetector(PivotPatternDetectorBase):
    """
    头肩顶识别器（与头肩底对称，支持倾斜颈线）

    判断条件：
    1. 三个相邻的已确认 swing high：左肩 ls、头部 h、右肩 rs
    2. 头部高于左肩：h.price > ls.price
    3. 右肩低于头部：rs.price < h.price
    4. 左右肩对称：|ls.price - rs.price| / ls.price ≤ 20%
    5. 颈线：左肩→头部之间最低的 swing low（左颈线点）与
       头部→右肩之间最低的 swing low（右颈线点）连线
    6. 跌破确认：当前收盘 < 当前颈线值 * 1.02

    目标价 = 当前颈线值 - (头部价 - 当前颈线值)
    止损   = 头部价 * 1.02
    """

    @property
    def pattern_id(self) -> str:
        return "hs_top"

    @property
    def pattern_name(self) -> str:
        return "头肩顶"

    def detect(self, klines: List[OHLCV], pivots: Optional[List[PivotPoint]] = None) -> List[PatternResult]:
        results: List[PatternResult] = []
        if len(klines) < 25:
            return results

        confirmed = self._get_confirmed_pivots(klines, pivots)
        highs = sorted([p for p in confirmed if p.pivot_type == "swing_high"],
                       key=lambda p: p.index)
        lows = sorted([p for p in confirmed if p.pivot_type == "swing_low"],
                      key=lambda p: p.index)

        for ls, head, rs in zip(highs, highs[1:], highs[2:]):
            # --- 形态闸门 ---
            # 头部必须高于左肩
            if head.price <= ls.price:
                continue
            # 右肩必须低于头部
            if rs.price >= head.price:
                continue
            # 左右肩价格差距 ≤ 20%
            if ls.price <= 0 or abs(ls.price - rs.price) / ls.price > 0.20:
                continue

            # --- FIR-24 guardrail 1a：最小头-肩突出度（与头肩底对称） ---
            avg_shoulder = (ls.price + rs.price) / 2.0
            if avg_shoulder <= 0:
                continue
            head_prominence = abs(head.price - avg_shoulder) / avg_shoulder
            if head_prominence < MIN_HS_HEAD_PROMINENCE:
                continue

            # --- 颈线两点 ---
            left_between = [l for l in lows if ls.index < l.index < head.index]
            if not left_between:
                # 左颈线区间缺少已确认对立 pivot：拒绝该候选
                continue
            ln = self._pick_extreme_pivot(left_between, pick_max=False)

            right_between = [l for l in lows if head.index < l.index < rs.index]
            if not right_between:
                # 右颈线区间缺少已确认对立 pivot：拒绝该候选
                continue
            rn = self._pick_extreme_pivot(right_between, pick_max=False)

            # 倾斜颈线：线性插值
            if rn.index != ln.index:
                slope = (rn.price - ln.price) / (rn.index - ln.index)
            else:
                slope = 0.0
            nl_fn = lambda idx, ln=ln, slope=slope: ln.price + slope * (idx - ln.index)

            stage = self._classify_stage(klines, rs.index, nl_fn, bullish=False, breakout_mult=1.02)
            nl_now = nl_fn(len(klines) - 1)

            # --- FIR-24 guardrail 1b：倾斜颈线外推合理性（与头肩底对称） ---
            # 头肩顶颈线应在头部下方；外推值必须为正，倾斜颈线外推不能比头部
            # 低过 MAX_NECKLINE_EXTRAPOLATION_RATIO 倍（否则外推失控）。
            if nl_now <= 0:
                continue
            if slope != 0.0 and nl_now < head.price / MAX_NECKLINE_EXTRAPOLATION_RATIO:
                continue

            broken = klines[-1].close < nl_now * 1.02

            target = nl_now - (head.price - nl_now)
            stop = head.price * 1.02

            # --- 评分：左肩低于头20 + 右肩低于头20 + 左右肩对称20 + 跌破40/10 ---
            score = 20 + 20 + 20 + (40 if broken else 10)
            if stage == PatternStage.FAILED_BREAK:
                score = max(10, score - 30)

            key_points = [
                PatternKeyPoint("左肩", ls.index, ls.date, ls.price, "左肩摆动高点"),
                PatternKeyPoint("头", head.index, head.date, head.price, "头部摆动高点（最高）"),
                PatternKeyPoint("右肩", rs.index, rs.date, rs.price, "右肩摆动高点（低于头部）"),
                PatternKeyPoint("左颈线点", ln.index, ln.date, ln.price,
                                "左肩→头部之间的回调低点"),
                PatternKeyPoint("右颈线点", rn.index, rn.date, rn.price,
                                "头部→右肩之间的回调低点"),
            ]
            results.append(PatternResult(
                pattern_id=self.pattern_id,
                pattern_name=self.pattern_name,
                direction=TrendDirection.BEAR,
                stage=stage,
                score=score,
                confidence=self._confidence(score),
                key_points=key_points,
                neckline_price=round(nl_now, 4),
                breakout_price=round(klines[-1].close, 4) if broken else None,
                target_price=round(target, 4),
                stop_loss=round(stop, 4),
                notes=f"头肩顶：左肩{ls.price:.2f} 头{head.price:.2f} "
                      f"右肩{rs.price:.2f} 颈线{ln.price:.2f}→{rn.price:.2f}(斜率{slope:.4f})",
            ))
        return results


# ===== core/registry.py =====
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
# interfaces inline (
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


# ===== core/market_data/__init__.py =====
"""
core.market_data — FIR-23 市场数据契约实现

提供统一的市场数据获取、校验、复权和交易日历工具。
"""

from .sina_provider import SinaDataProvider
from .tencent_provider import TencentDataProvider
from .multi_source import MultiSourceDataProvider
from .trading_calendar import (
    is_trading_day,
    get_trading_days,
    next_trading_day,
    prev_trading_day,
    count_trading_days,
    derive_calendar_id,
    CALENDAR_SSE,
    CALENDAR_SZSE,
    CALENDAR_BSE,
    CALENDAR_UNKNOWN,
)
from .adjustment import compute_adjustment_factors, apply_adjustment
from .validation import (
    validate_ohlc_invariants, normalize_bars, validate_adjustment_consistency,
    AdjustmentConsistency, assess_series_quality,
)
from .aggregation import aggregate_daily_bars
from .policy import UseCase, DEFAULT_POLICY, adjustment_for

__all__ = [
    "SinaDataProvider",
    "TencentDataProvider",
    "MultiSourceDataProvider",
    "is_trading_day",
    "get_trading_days",
    "next_trading_day",
    "prev_trading_day",
    "count_trading_days",
    "derive_calendar_id",
    "CALENDAR_SSE",
    "CALENDAR_SZSE",
    "CALENDAR_BSE",
    "CALENDAR_UNKNOWN",
    "compute_adjustment_factors",
    "apply_adjustment",
    "validate_ohlc_invariants",
    "normalize_bars",
    "validate_adjustment_consistency",
    "AdjustmentConsistency",
    "assess_series_quality",
    "aggregate_daily_bars",
    "UseCase",
    "DEFAULT_POLICY",
    "adjustment_for",
]


# ===== core/market_data/sina_provider.py =====
"""
SinaDataProvider — 新浪财经数据源

API: https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_{market}{code}=/CN_MarketDataService.getKLineData
    ?symbol={market}{code}&scale=240&ma=no&datalen={datalen}

注意：新浪返回不复权数据，不支持复权参数。
若需复权，需结合 TencentDataProvider 的 hfq 数据反推因子。
"""

import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional

from ..interfaces import DataProvider, MarketDataError
from ..types import (
    MarketDataBar, MarketDataSeries, DataQualityReport,
    DataProvenance, DataQualityFlag, DataSourceType,
    AdjustmentType, FrequencyType, StockData, OHLCV,
)
from .trading_calendar import get_trading_days, is_trading_day, derive_calendar_id
from .validation import assess_series_quality, normalize_bars


_SINA_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
    "var%20_{market}{code}=/CN_MarketDataService.getKLineData"
    "?symbol={market}{code}&scale=240&ma=no&datalen={datalen}"
)


def _market_prefix(code: str) -> Optional[str]:
    """根据股票代码判断市场前缀

    注意：920xxx 是北交所 2024-04-22 起启用的新代码段，必须在通用
    9xx→sh 之前识别，否则北交所新股会被错误路由到上海。

    Returns:
        "sh" / "sz" / "bj" / None
        无法识别时返回 None（fail-closed，调用方必须处理，不静默回退 sz）
    """
    # P0: 北交所 920xxx 新代码段优先识别
    if code.startswith("920"):
        return "bj"
    # 上海: 6xx, 5xx
    if code.startswith(("6", "5")):
        return "sh"
    # 上海 B 股: 仅 900xxx（权威范围 900000-900999）
    # 901xxx+ 不在权威上海 B 股分配表中 → None（fail-closed）
    if code.startswith("9") and code[:3] < "901":
        return "sh"
    # 深圳: 0xx, 3xx
    if code.startswith(("0", "3")):
        return "sz"
    # 北京: 4xx(历史), 8xx(历史)
    if code.startswith(("4", "8")):
        return "bj"
    # 930xxx+ 及其他无法识别的代码 → None（fail-closed）
    return None


def _http_get(url: str, timeout: int = 15) -> str:
    """HTTP GET 请求，返回响应文本"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; kline-pattern-analyzer/5.2)",
        "Referer": "https://finance.sina.com.cn/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise MarketDataError(f"Sina request failed: {e}") from e


def _parse_jsonp(text: str) -> list:
    """解析 JSONP 响应，提取括号内的 JSON 数组"""
    m = re.search(r"\((.*)\)", text, re.DOTALL)
    if not m:
        raise MarketDataError(f"Cannot parse JSONP response: {text[:200]}")
    return json.loads(m.group(1))


class SinaDataProvider(DataProvider):
    """新浪财经数据源（不复权）"""

    SOURCE = DataSourceType.SINA

    def fetch_klines(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        frequency: FrequencyType = FrequencyType.DAILY,
        adjustment: AdjustmentType = AdjustmentType.NONE,
    ) -> MarketDataSeries:
        # 提取纯数字代码
        code = symbol.lstrip("shzabjSHZABJ")
        market = _market_prefix(code)
        if market is None:
            raise MarketDataError(
                f"Sina provider cannot determine market prefix for symbol '{symbol}' "
                f"(numeric code '{code}'). Unrecognized exchange code range."
            )
        full_symbol = f"{market}{code}"

        # 新浪仅支持日线；其他频率暂不支持
        if frequency != FrequencyType.DAILY:
            raise MarketDataError(f"Sina provider only supports DAILY frequency, got {frequency}")

        # 新浪不支持复权
        if adjustment != AdjustmentType.NONE:
            raise NotImplementedError(
                "Sina API does not support adjustment. "
                "Use TencentDataProvider for qfq/hfq, or MultiSourceDataProvider."
            )

        # 计算 datalen
        datalen = 1023  # 最大可获取天数
        if start_date:
            try:
                sd = datetime.strptime(start_date, "%Y-%m-%d")
                today = datetime.now()
                days_needed = (today - sd).days + 10
                datalen = min(1023, max(30, int(days_needed * 1.5)))
            except ValueError:
                pass

        url = _SINA_URL.format(market=market, code=code, datalen=datalen)
        raw_text = _http_get(url)
        raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        fetched_at = datetime.now(timezone.utc).isoformat()

        records = _parse_jsonp(raw_text)
        if not records:
            raise MarketDataError(f"No data returned for {full_symbol}")

        bars: List[MarketDataBar] = []
        for rec in records:
            day = rec["day"]
            # 日期过滤
            if start_date and day < start_date:
                continue
            if end_date and day > end_date:
                continue
            prov = DataProvenance(
                source=self.SOURCE,
                url=url,
                fetched_at=fetched_at,
                raw_payload_hash=raw_hash,
                adjustment_applied=adjustment,
                quality_flag=DataQualityFlag.OK,
            )
            bars.append(MarketDataBar(
                date=day,
                open=float(rec["open"]),
                high=float(rec["high"]),
                low=float(rec["low"]),
                close=float(rec["close"]),
                volume=float(rec["volume"]),
                amount=None,
                provenance=prov,
            ))

        if not bars:
            raise MarketDataError(f"No bars after date filter for {full_symbol}")

        # Blocker 6: 确定性去重 + 强制升序
        bars, _ = normalize_bars(bars)

        series = MarketDataSeries(
            symbol=full_symbol,
            name=full_symbol,
            frequency=frequency,
            adjustment=adjustment,
            trading_calendar=derive_calendar_id(full_symbol),
            source=self.SOURCE,
            fetched_at=fetched_at,
            bars=bars,
        )
        series.quality_report = self.validate_data(series)
        return series

    def validate_data(self, series: MarketDataSeries) -> DataQualityReport:
        """校验数据质量（Blocker 2 & 6：真实校验，恒真重言式已移除）"""
        return assess_series_quality(series)

    def get_trading_calendar(self, exchange: str, start_date: str, end_date: str) -> List[str]:
        return get_trading_days(start_date, end_date)

    # ---- 兼容旧接口 ----

    def get_stock_data(self, code: str, days: int = 250) -> StockData:
        series = self.fetch_klines(symbol=code, adjustment=AdjustmentType.NONE)
        # 只取最近 days 根
        bars = series.bars[-days:] if len(series.bars) > days else series.bars
        klines = [OHLCV(date=b.date, open=b.open, high=b.high, low=b.low,
                        close=b.close, volume=b.volume, amount=b.amount) for b in bars]
        return StockData(code=code, name=code, klines=klines, source="sina")

    def get_index_data(self, code: str, days: int = 250) -> StockData:
        return self.get_stock_data(code, days)

    def get_sector_constituents(self, sector_code: str) -> List[str]:
        raise NotImplementedError("Sina provider does not support sector constituents")


# ===== Main Entry Point =====
def analyze_stock_standalone(code: str, days: int = 370) -> Dict[str, Any]:
    """自包含分析入口"""
    try:
        from core.market_data.sina_provider import SinaDataProvider
        provider = SinaDataProvider()
        stock_data = provider.get_stock_data(code, days=days)
        klines = stock_data.klines
    except Exception as e:
        return {"error": f"数据获取失败: {str(e)}"}
    
    if len(klines) < 30:
        return {"error": f"K线数据不足，仅{len(klines)}根"}
    
    # Swing Pivot
    try:
        from core.swing_pivot import SwingPivotEngine
        engine = SwingPivotEngine(left_bars=5, right_bars=5)
        pivots = engine.detect(klines)
    except Exception as e:
        pivots = []
    
    # Pattern Detection
    patterns = {}
    try:
        from core.pivot_patterns import WBottomDetector, MTopDetector, HSBottomDetector, HSTopDetector
        detectors = {
            "w_bottom": WBottomDetector(),
            "m_top": MTopDetector(),
            "hs_bottom": HSBottomDetector(),
            "hs_top": HSTopDetector(),
        }
        for pid, detector in detectors.items():
            try:
                results = detector.detect(klines, pivots)
                patterns[pid] = [p.to_dict() if hasattr(p, 'to_dict') else str(p) for p in results]
            except Exception as e:
                patterns[pid] = []
    except Exception as e:
        patterns = {"error": str(e)}
    
    # 计算推荐度
    bull_score = max([p.get('score', 0) for p in patterns.get('w_bottom', [])] + [p.get('score', 0) for p in patterns.get('hs_bottom', [])], default=0)
    bear_score = max([p.get('score', 0) for p in patterns.get('m_top', [])] + [p.get('score', 0) for p in patterns.get('hs_top', [])], default=0)
    
    if bear_score >= 60 and bear_score > bull_score:
        attention_score = max(0, 100 - bear_score)
        level = "S级·风险警示"
        advice = "见顶信号强烈，建议止盈止损"
    elif bull_score >= 80:
        attention_score = min(100, bull_score + 10)
        level = "A级·强烈推荐"
        advice = "形态明确，可重点关注"
    elif bull_score >= 60:
        attention_score = bull_score
        level = "B级·推荐关注"
        advice = "形态较好，值得跟踪"
    elif bull_score >= 40:
        attention_score = bull_score
        level = "C级·值得观察"
        advice = "形态初现，继续观察"
    else:
        attention_score = max(bull_score, 20)
        level = "D级·一般"
        advice = "无明显形态，观望为主"
    
    return {
        "code": code,
        "name": getattr(stock_data, 'name', code),
        "data_length": len(klines),
        "latest_date": str(klines[-1].date) if hasattr(klines[-1], 'date') else '',
        "latest_close": klines[-1].close,
        "klines": [k.to_dict() if hasattr(k, 'to_dict') else {'date': str(k.date), 'open': k.open, 'high': k.high, 'low': k.low, 'close': k.close, 'volume': k.volume} for k in klines],
        "patterns": patterns,
        "attention": {
            "score": attention_score,
            "level": level,
            "advice": advice,
            "bull_score": bull_score,
            "bear_score": bear_score,
        },
        "engine": "python-canonical-core-standalone",
        "core_version": "5.2.0",
    }

if __name__ == '__main__':
    code = sys.argv[1] if len(sys.argv) > 1 else '603068'
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 370
    result = analyze_stock_standalone(code, days)
    print(json.dumps(result, ensure_ascii=False, default=str))
