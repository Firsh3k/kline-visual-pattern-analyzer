"""
Canonical Core — 统一接口定义

所有算法模块必须实现这些接口，确保三端调用路径完全一致。

Source of Truth: Python (此文件)
Web 端通过 cross-runtime parity 测试保证接口一致。
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from .types import (
    StockData, OHLCV, PatternResult, SignalResult,
    MarketRegime, FakeSignalRisk, AnalysisResult, PivotPoint
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
    def backtest(self, code: str, signal_date: str, horizon_days: int = 20) -> Dict[str, Any]:
        """
        回测单个信号

        Args:
            code: 股票代码
            signal_date: 信号日期 YYYY-MM-DD
            horizon_days: 观察窗口天数（3/5/10/20/40）

        Returns:
            Dict[str, Any]: 回测结果，包含 return、MFE、MAE等
        """
        pass

    @abstractmethod
    def batch_backtest(self, signals: List[Dict[str, Any]], horizon_days: int = 20) -> Dict[str, Any]:
        """
        批量回测，统计胜率

        Args:
            signals: 信号列表，每个包含 code 和 date
            horizon_days: 观察窗口天数

        Returns:
            Dict[str, Any]: 统计结果，包含 win_rate、avg_return、sample_size等
        """
        pass


# ========== 异常定义 ==========

class CanonicalCoreError(Exception):
    """Canonical Core 基础异常"""
    pass


class DataProviderError(CanonicalCoreError):
    """数据提供者异常"""
    pass


class PatternDetectionError(CanonicalCoreError):
    """形态识别异常"""
    pass


class AnalysisError(CanonicalCoreError):
    """分析异常"""
    pass
