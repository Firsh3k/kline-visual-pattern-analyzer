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
