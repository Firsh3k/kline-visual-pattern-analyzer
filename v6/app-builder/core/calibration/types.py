"""
FIR-13: Empirical A-share Calibration — 数据类型定义

将 heuristic priors 转换为 evidence-backed probability metrics。
所有校准相关的数据结构在此定义，确保信号生成、回测、统计三层之间
的接口严格类型化。

核心设计原则：
1. **sample_size 透明** — 每个概率指标必须附带 sample_size
2. **evidence threshold** — 样本不足时不编造概率，返回 N/A
3. **可复现** — 所有字段可序列化，支持从源数据重建
4. **防 look-ahead** — SignalOccurrence 只携带信号日及之前可知的信息
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any, Tuple
from enum import Enum

# ===========================================================================
# 枚举定义
# ===========================================================================


class PatternType(str, Enum):
    """形态类型 — 与 V4 pattern_scanner.py 六种形态对齐

    上涨形态（多头信号）：
    - ma_bull: 均线多头排列+突破
    - w_bottom: W底（双底）
    - hs_bottom: 头肩底
    - platform_break: 平台突破

    下跌形态（空头信号）：
    - ma_bear: 均线空头排列+破位
    - m_top: M头（双顶）
    - hs_top: 头肩顶
    - platform_break_down: 平台破位
    """
    MA_BULL = "ma_bull"
    MA_BEAR = "ma_bear"
    W_BOTTOM = "w_bottom"
    M_TOP = "m_top"
    HS_BOTTOM = "hs_bottom"
    HS_TOP = "hs_top"
    PLATFORM_BREAK = "platform_break"
    PLATFORM_BREAK_DOWN = "platform_break_down"

    @property
    def is_bullish(self) -> bool:
        """是否为看涨形态"""
        return self in (
            PatternType.MA_BULL,
            PatternType.W_BOTTOM,
            PatternType.HS_BOTTOM,
            PatternType.PLATFORM_BREAK,
        )

    @property
    def is_bearish(self) -> bool:
        """是否为看跌形态"""
        return self in (
            PatternType.MA_BEAR,
            PatternType.M_TOP,
            PatternType.HS_TOP,
            PatternType.PLATFORM_BREAK_DOWN,
        )


class ConfirmationState(str, Enum):
    """确认状态 — 信号产生时的形态确认阶段

    与 core.types.PatternStage 对齐，但仅保留校准关心的子集。
    """
    PRE_BREAK = "pre_break"           # 突破前（形态形成中，尚未突破）
    BREAKOUT_DAY = "breakout_day"     # 突破日（当天突破关键位）
    POST_BREAK = "post_break"         # 突破后（突破已确认，未回踩）
    RETEST = "retest"                 # 回踩（突破后回踩关键位）
    FAILED_BREAK = "failed_break"     # 突破失败（假突破）


class MarketRegime(str, Enum):
    """大盘环境 — 基于指数趋势的简单三分法

    使用上证指数（sh000001）的 60 日均线方向 + 价格位置判断：
    - BULL: 指数 > MA60 且 MA60 向上
    - BEAR: 指数 < MA60 且 MA60 向下
    - RANGE: 其他（震荡）
    """
    BULL = "bull"
    BEAR = "bear"
    RANGE = "range"


class ConfidenceLabel(str, Enum):
    """证据置信度标签 — 基于 sample size 的 evidence threshold

    - INSUFFICIENT: sample_size < 30 → 不报告概率，所有指标返回 N/A
    - LOW_CONFIDENCE: 30 <= sample_size < 100 → 标记低置信度
    - EMPIRICAL: sample_size >= 100 → 经验校准值
    """
    INSUFFICIENT = "insufficient"
    LOW_CONFIDENCE = "low_confidence"
    EMPIRICAL = "empirical"


# ===========================================================================
# 常量
# ===========================================================================

# 固定回测 horizons（交易日）
DEFAULT_HORIZONS: Tuple[int, ...] = (3, 5, 10, 20, 40)

# Evidence thresholds
SAMPLE_INSUFFICIENT = 30   # < 30 → N/A
SAMPLE_LOW_CONFIDENCE = 100  # < 100 → low_confidence, >= 100 → empirical

# 胜率阈值（forward_return > threshold 视为 win）
DEFAULT_WIN_THRESHOLD = 0.0  # 0% = 只要盈利即算 win


# ===========================================================================
# 核心数据类
# ===========================================================================


@dataclass
class SignalOccurrence:
    """
    信号出现 — 某只股票在某一天产生的一个形态信号

    这是校准的原子单位。每个 SignalOccurrence 随后会被回测引擎
    计算多个 horizon 的前向收益。

    防 look-ahead 保证：所有字段只使用 signal_date 及之前的数据。
    """
    signal_id: str                    # 唯一标识（code + date + pattern_type + idx）
    stock_code: str                   # 股票代码（6位数字，如 "600519"）
    signal_date: str                  # 信号产生日期 YYYY-MM-DD（形态确认日）
    pattern_type: PatternType         # 形态类型
    entry_price: float                # 入场价格（信号日收盘价）
    confirmation_state: ConfirmationState  # 确认状态
    market_regime: MarketRegime       # 信号日的大盘环境
    pattern_score: int = 0            # 形态匹配度评分 0-100（来自 V4 scanner）
    breakout_price: Optional[float] = None  # 突破价格（如有）
    neckline_price: Optional[float] = None  # 颈线价格（如有）
    notes: str = ""                   # 备注
    # ---- FIR-24: provenance / quality manifest ----
    data_source: str = ""             # "sina" / "tencent" / "multi_source"
    adjustment: str = ""              # "qfq" / "hfq" / "raw"
    data_quality: str = "OK"          # "OK" / "has_missing" / "has_suspension" / "unverified_calendar"
    detector_version: str = ""        # "canonical_v5.2.0" / "builtin_v1" / "legacy"
    pattern_confidence: str = ""       # "high" / "medium" / "low"
    guardrail_flags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'signal_id': self.signal_id,
            'stock_code': self.stock_code,
            'signal_date': self.signal_date,
            'pattern_type': self.pattern_type.value,
            'entry_price': self.entry_price,
            'confirmation_state': self.confirmation_state.value,
            'market_regime': self.market_regime.value,
            'pattern_score': self.pattern_score,
            'breakout_price': self.breakout_price,
            'neckline_price': self.neckline_price,
            'notes': self.notes,
            'data_source': self.data_source,
            'adjustment': self.adjustment,
            'data_quality': self.data_quality,
            'detector_version': self.detector_version,
            'pattern_confidence': self.pattern_confidence,
            'guardrail_flags': list(self.guardrail_flags),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SignalOccurrence':
        return cls(
            signal_id=str(data['signal_id']),
            stock_code=str(data['stock_code']),
            signal_date=str(data['signal_date']),
            pattern_type=PatternType(data['pattern_type']),
            entry_price=float(data['entry_price']),
            confirmation_state=ConfirmationState(data['confirmation_state']),
            market_regime=MarketRegime(data['market_regime']),
            pattern_score=int(data.get('pattern_score', 0)),
            breakout_price=float(data['breakout_price']) if data.get('breakout_price') is not None else None,
            neckline_price=float(data['neckline_price']) if data.get('neckline_price') is not None else None,
            notes=str(data.get('notes', '')),
            data_source=str(data.get('data_source', '')),
            adjustment=str(data.get('adjustment', '')),
            data_quality=str(data.get('data_quality', 'OK')),
            detector_version=str(data.get('detector_version', '')),
            pattern_confidence=str(data.get('pattern_confidence', '')),
            guardrail_flags=list(data.get('guardrail_flags', [])),
        )


@dataclass
class HorizonBacktest:
    """
    单信号单 horizon 的回测结果

    对每个 SignalOccurrence，为每个 horizon (3/5/10/20/40) 生成一条记录。
    数据不足时 sufficient_data=False，收益字段为 None。
    """
    signal_id: str                    # 关联 SignalOccurrence.signal_id
    horizon: int                      # 观察窗口交易日数（3/5/10/20/40）
    forward_return: Optional[float] = None   # 前向收益率（小数，如 0.05 = 5%）
    mfe: Optional[float] = None              # Maximum Favorable Excursion（horizon 内最大有利波动，小数；方向感知：正数=有利）
    mae: Optional[float] = None              # Maximum Adverse Excursion（horizon 内最大不利波动，小数；方向感知：负数=不利）
    win: Optional[bool] = None               # 是否盈利（方向感知：看涨=forward_return>threshold；看跌=forward_return<-threshold）
    sufficient_data: bool = False            # 是否有足够的未来数据完成该 horizon
    actual_bars: Optional[int] = None        # 实际可用的未来K线数（可能不足 horizon）
    final_price: Optional[float] = None      # horizon 结束时的收盘价
    direction: str = "bullish"               # 形态方向："bullish" 或 "bearish"
    directional_profit: Optional[float] = None  # 方向调整后的收益（看涨=forward_return，看跌=-forward_return；正数=按形态方向盈利）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HorizonBacktest':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class CalibrationMetric:
    """
    校准指标 — 某一分组在某一 horizon 下的统计结果

    sample_size 决定 confidence_label：
    - < 30 → INSUFFICIENT，所有概率指标为 None
    - 30-99 → LOW_CONFIDENCE
    - >= 100 → EMPIRICAL
    """
    horizon: int                                  # 观察窗口
    sample_size: int                              # 有效样本数（sufficient_data=True 的数量）
    total_signals: int                            # 总信号数（含数据不足的）
    confidence_label: ConfidenceLabel             # 证据置信度标签
    win_rate: Optional[float] = None              # 胜率（0-1 小数，基于方向感知的 win）
    avg_return: Optional[float] = None            # 平均前向收益（小数，raw forward_return，反映价格实际涨跌方向）
    median_return: Optional[float] = None         # 中位数前向收益（小数，raw forward_return）
    avg_mfe: Optional[float] = None               # 平均 MFE（小数，方向感知）
    avg_mae: Optional[float] = None               # 平均 MAE（小数，方向感知，负值）
    profit_factor: Optional[float] = None         # 盈亏比（总方向盈利/|总方向亏损|，基于 directional_profit）
    std_return: Optional[float] = None            # 收益标准差（小数，raw forward_return）
    max_return: Optional[float] = None            # 最大收益（小数，raw forward_return）
    min_return: Optional[float] = None            # 最小收益（小数，raw forward_return）
    avg_directional_profit: Optional[float] = None  # 平均方向调整收益（小数，directional_profit 均值；正数=按形态方向整体盈利）

    def to_dict(self) -> Dict[str, Any]:
        return {
            'horizon': self.horizon,
            'sample_size': self.sample_size,
            'total_signals': self.total_signals,
            'confidence_label': self.confidence_label.value,
            'win_rate': self.win_rate,
            'avg_return': self.avg_return,
            'median_return': self.median_return,
            'avg_mfe': self.avg_mfe,
            'avg_mae': self.avg_mae,
            'profit_factor': self.profit_factor,
            'std_return': self.std_return,
            'max_return': self.max_return,
            'min_return': self.min_return,
            'avg_directional_profit': self.avg_directional_profit,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CalibrationMetric':
        return cls(
            horizon=int(data['horizon']),
            sample_size=int(data['sample_size']),
            total_signals=int(data.get('total_signals', data['sample_size'])),
            confidence_label=ConfidenceLabel(data['confidence_label']),
            win_rate=float(data['win_rate']) if data.get('win_rate') is not None else None,
            avg_return=float(data['avg_return']) if data.get('avg_return') is not None else None,
            median_return=float(data['median_return']) if data.get('median_return') is not None else None,
            avg_mfe=float(data['avg_mfe']) if data.get('avg_mfe') is not None else None,
            avg_mae=float(data['avg_mae']) if data.get('avg_mae') is not None else None,
            profit_factor=float(data['profit_factor']) if data.get('profit_factor') is not None else None,
            std_return=float(data['std_return']) if data.get('std_return') is not None else None,
            max_return=float(data['max_return']) if data.get('max_return') is not None else None,
            min_return=float(data['min_return']) if data.get('min_return') is not None else None,
            avg_directional_profit=float(data['avg_directional_profit']) if data.get('avg_directional_profit') is not None else None,
        )


@dataclass
class CalibrationGroup:
    """
    校准分组 — 按 (pattern_type, market_regime, confirmation_state) 分组的结果

    每个分组包含所有 horizon 的 CalibrationMetric。
    分组维度可以是部分维度（如只按 pattern_type，此时 regime/confirmation 为 "all"）。
    """
    pattern_type: str                    # 形态类型（"all" 表示全部）
    market_regime: str                   # 大盘环境（"all" 表示全部）
    confirmation_state: str              # 确认状态（"all" 表示全部）
    metrics_by_horizon: Dict[int, CalibrationMetric] = field(default_factory=dict)  # horizon -> metric

    @property
    def group_key(self) -> Tuple[str, str, str]:
        return (self.pattern_type, self.market_regime, self.confirmation_state)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'pattern_type': self.pattern_type,
            'market_regime': self.market_regime,
            'confirmation_state': self.confirmation_state,
            'metrics_by_horizon': {str(h): m.to_dict() for h, m in self.metrics_by_horizon.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CalibrationGroup':
        metrics = {}
        for h_str, m_data in data.get('metrics_by_horizon', {}).items():
            metrics[int(h_str)] = CalibrationMetric.from_dict(m_data)
        return cls(
            pattern_type=str(data['pattern_type']),
            market_regime=str(data['market_regime']),
            confirmation_state=str(data['confirmation_state']),
            metrics_by_horizon=metrics,
        )


@dataclass
class CalibrationDataset:
    """
    校准数据集 — 所有信号和回测结果的完整集合

    支持序列化/反序列化，确保校准过程可从源数据复现。
    """
    version: str = "1.0"                          # 数据集版本
    created_at: str = ""                           # 创建时间 ISO8601
    stock_pool: List[str] = field(default_factory=list)  # 股票池
    start_date: str = ""                           # 数据起始日期
    end_date: str = ""                             # 数据结束日期
    horizons: List[int] = field(default_factory=lambda: list(DEFAULT_HORIZONS))
    win_threshold: float = DEFAULT_WIN_THRESHOLD   # 胜率阈值
    signals: List[SignalOccurrence] = field(default_factory=list)
    backtests: List[HorizonBacktest] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)  # 数据来源、参数等元信息

    @property
    def signal_count(self) -> int:
        return len(self.signals)

    @property
    def backtest_count(self) -> int:
        return len(self.backtests)

    def get_signal(self, signal_id: str) -> Optional[SignalOccurrence]:
        for s in self.signals:
            if s.signal_id == signal_id:
                return s
        return None

    def get_backtests_for_signal(self, signal_id: str) -> List[HorizonBacktest]:
        return [b for b in self.backtests if b.signal_id == signal_id]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'version': self.version,
            'created_at': self.created_at,
            'stock_pool': self.stock_pool,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'horizons': self.horizons,
            'win_threshold': self.win_threshold,
            'signals': [s.to_dict() for s in self.signals],
            'backtests': [b.to_dict() for b in self.backtests],
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CalibrationDataset':
        return cls(
            version=str(data.get('version', '1.0')),
            created_at=str(data.get('created_at', '')),
            stock_pool=list(data.get('stock_pool', [])),
            start_date=str(data.get('start_date', '')),
            end_date=str(data.get('end_date', '')),
            horizons=list(data.get('horizons', list(DEFAULT_HORIZONS))),
            win_threshold=float(data.get('win_threshold', DEFAULT_WIN_THRESHOLD)),
            signals=[SignalOccurrence.from_dict(s) for s in data.get('signals', [])],
            backtests=[HorizonBacktest.from_dict(b) for b in data.get('backtests', [])],
            metadata=dict(data.get('metadata', {})),
        )


@dataclass
class CalibrationSummary:
    """
    校准汇总 — 所有分组的统计结果集合

    包含多种维度的分组：
    - 按 pattern_type
    - 按 market_regime
    - 按 confirmation_state
    - 交叉分组（pattern × regime, pattern × confirmation）
    """
    version: str = "1.0"
    created_at: str = ""
    total_signals: int = 0
    stock_count: int = 0
    date_range: str = ""
    groups: List[CalibrationGroup] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def find_group(
        self,
        pattern_type: str = "all",
        market_regime: str = "all",
        confirmation_state: str = "all",
    ) -> Optional[CalibrationGroup]:
        """查找指定分组"""
        for g in self.groups:
            if (g.pattern_type == pattern_type and
                    g.market_regime == market_regime and
                    g.confirmation_state == confirmation_state):
                return g
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'version': self.version,
            'created_at': self.created_at,
            'total_signals': self.total_signals,
            'stock_count': self.stock_count,
            'date_range': self.date_range,
            'groups': [g.to_dict() for g in self.groups],
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CalibrationSummary':
        return cls(
            version=str(data.get('version', '1.0')),
            created_at=str(data.get('created_at', '')),
            total_signals=int(data.get('total_signals', 0)),
            stock_count=int(data.get('stock_count', 0)),
            date_range=str(data.get('date_range', '')),
            groups=[CalibrationGroup.from_dict(g) for g in data.get('groups', [])],
            metadata=dict(data.get('metadata', {})),
        )


# ===========================================================================
# 辅助函数
# ===========================================================================


def confidence_for_sample_size(n: int) -> ConfidenceLabel:
    """根据样本量返回置信度标签"""
    if n < SAMPLE_INSUFFICIENT:
        return ConfidenceLabel.INSUFFICIENT
    elif n < SAMPLE_LOW_CONFIDENCE:
        return ConfidenceLabel.LOW_CONFIDENCE
    else:
        return ConfidenceLabel.EMPIRICAL


def make_signal_id(stock_code: str, signal_date: str, pattern_type: str, idx: int = 0) -> str:
    """生成信号唯一 ID"""
    return f"{stock_code}_{signal_date}_{pattern_type}_{idx}"
