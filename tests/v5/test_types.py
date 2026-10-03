"""
V5 测试 — 统一数据结构验证

确保所有数据类的序列化/反序列化、属性、枚举值正确。
"""

import pytest
import json
from core import (
    OHLCV, StockData, PivotPoint, PatternKeyPoint, PatternResult,
    SignalResult, MarketRegime, FakeSignalRisk, AnalysisResult,
    TrendDirection, PatternStage, SignalType, ConfidenceLevel,
)


class TestOHLCV:
    """OHLCV 单根K线数据"""

    def test_create(self, sample_ohlcv):
        assert sample_ohlcv.date == "2026-09-30"
        assert sample_ohlcv.open == 10.50
        assert sample_ohlcv.high == 11.20
        assert sample_ohlcv.low == 10.30
        assert sample_ohlcv.close == 11.00
        assert sample_ohlcv.volume == 1500000
        assert sample_ohlcv.amount == 16500000.0

    def test_to_dict(self, sample_ohlcv):
        d = sample_ohlcv.to_dict()
        assert d['date'] == "2026-09-30"
        assert d['open'] == 10.50
        assert d['close'] == 11.00

    def test_from_dict(self, sample_ohlcv):
        d = sample_ohlcv.to_dict()
        restored = OHLCV.from_dict(d)
        assert restored.date == sample_ohlcv.date
        assert restored.open == sample_ohlcv.open
        assert restored.close == sample_ohlcv.close

    def test_roundtrip(self, sample_ohlcv):
        """序列化→反序列化 往返测试"""
        restored = OHLCV.from_dict(sample_ohlcv.to_dict())
        assert restored.to_dict() == sample_ohlcv.to_dict()

    def test_json_serializable(self, sample_ohlcv):
        """确保可以序列化为JSON"""
        d = sample_ohlcv.to_dict()
        json_str = json.dumps(d)
        assert json.loads(json_str)['close'] == 11.00


class TestStockData:
    """StockData 股票完整数据"""

    def test_create(self, sample_stock_data):
        assert sample_stock_data.code == "002436"
        assert sample_stock_data.name == "兴森科技"
        assert sample_stock_data.data_length == 20

    def test_latest_close(self, sample_stock_data):
        assert sample_stock_data.latest_close == sample_stock_data.klines[-1].close

    def test_to_dict(self, sample_stock_data):
        d = sample_stock_data.to_dict()
        assert d['code'] == "002436"
        assert len(d['klines']) == 20

    def test_from_dict(self, sample_stock_data):
        d = sample_stock_data.to_dict()
        restored = StockData.from_dict(d)
        assert restored.code == sample_stock_data.code
        assert restored.data_length == sample_stock_data.data_length

    def test_roundtrip(self, sample_stock_data):
        restored = StockData.from_dict(sample_stock_data.to_dict())
        assert restored.to_dict() == sample_stock_data.to_dict()


class TestEnums:
    """枚举值验证"""

    def test_trend_direction(self):
        assert TrendDirection.BULL.value == "bull"
        assert TrendDirection.BEAR.value == "bear"
        assert TrendDirection.NEUTRAL.value == "neutral"

    def test_pattern_stage(self):
        stages = [s.value for s in PatternStage]
        assert "forming" in stages
        assert "breakout_day" in stages
        assert "complete" in stages

    def test_signal_type(self):
        assert SignalType.BUY.value == "buy"
        assert SignalType.SELL.value == "sell"
        assert SignalType.WATCH.value == "watch"
        assert SignalType.NONE.value == "none"

    def test_confidence_level(self):
        levels = [l.value for l in ConfidenceLevel]
        assert "S" in levels
        assert "A" in levels
        assert "B" in levels
        assert "C" in levels
        assert "D" in levels


class TestPatternResult:
    """PatternResult 形态识别结果"""

    def test_create(self):
        result = PatternResult(
            pattern_id="hs_bottom",
            pattern_name="头肩底",
            direction=TrendDirection.BULL,
            stage=PatternStage.BREAKOUT_DAY,
            score=85,
            confidence=ConfidenceLevel.A,
            key_points=[],
        )
        assert result.pattern_id == "hs_bottom"
        assert result.direction == TrendDirection.BULL
        assert result.score == 85

    def test_to_dict(self):
        result = PatternResult(
            pattern_id="w_bottom",
            pattern_name="W底",
            direction=TrendDirection.BULL,
            stage=PatternStage.PRE_BREAK,
            score=70,
            confidence=ConfidenceLevel.B,
            key_points=[],
            neckline_price=10.5,
        )
        d = result.to_dict()
        assert d['pattern_id'] == "w_bottom"
        assert d['direction'] == "bull"
        assert d['stage'] == "pre_break"
        assert d['neckline_price'] == 10.5

    def test_json_serializable(self):
        result = PatternResult(
            pattern_id="m_top",
            pattern_name="M头",
            direction=TrendDirection.BEAR,
            stage=PatternStage.COMPLETE,
            score=90,
            confidence=ConfidenceLevel.S,
            key_points=[],
        )
        d = result.to_dict()
        json_str = json.dumps(d)
        assert json.loads(json_str)['pattern_id'] == "m_top"


class TestSignalResult:
    """SignalResult 信号结果"""

    def test_create(self):
        signal = SignalResult(
            signal_type=SignalType.BUY,
            pattern_id="hs_bottom",
            pattern_name="头肩底",
            score=80,
            confidence=ConfidenceLevel.A,
            trigger_price=10.5,
            target_price=12.0,
            stop_loss=9.8,
        )
        assert signal.signal_type == SignalType.BUY
        assert signal.trigger_price == 10.5

    def test_to_dict(self):
        signal = SignalResult(
            signal_type=SignalType.SELL,
            pattern_id="m_top",
            pattern_name="M头",
            score=75,
            confidence=ConfidenceLevel.B,
            trigger_price=20.0,
            risks=["量价背离", "MACD顶背离"],
        )
        d = signal.to_dict()
        assert d['signal_type'] == "sell"
        assert len(d['risks']) == 2


class TestMarketRegime:
    """MarketRegime 大盘环境"""

    def test_create(self):
        regime = MarketRegime(
            regime="结构性行情",
            score=65,
            trend=TrendDirection.NEUTRAL,
            stage="震荡偏强",
            advice="轻仓参与热点",
            impact_on_stock=5,
        )
        assert regime.regime == "结构性行情"
        assert regime.impact_on_stock == 5

    def test_to_dict(self):
        regime = MarketRegime(
            regime="牛市",
            score=85,
            trend=TrendDirection.BULL,
            impact_on_stock=15,
        )
        d = regime.to_dict()
        assert d['regime'] == "牛市"
        assert d['trend'] == "bull"


class TestAnalysisResult:
    """AnalysisResult 完整分析结果"""

    def test_create(self, sample_stock_data):
        result = AnalysisResult(
            stock=sample_stock_data,
            patterns=[],
            buy_signals=[],
            sell_signals=[],
            overall_score=75,
            recommendation="观察",
            summary="测试摘要",
        )
        assert result.stock.code == "002436"
        assert result.overall_score == 75

    def test_to_dict(self, sample_stock_data):
        result = AnalysisResult(
            stock=sample_stock_data,
            patterns=[],
            buy_signals=[],
            sell_signals=[],
        )
        d = result.to_dict()
        assert d['stock']['code'] == "002436"
        assert 'patterns' in d
        assert 'buy_signals' in d
