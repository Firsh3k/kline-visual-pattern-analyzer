"""
V5 测试 — 统一接口验证

确保所有抽象基类的接口定义正确，实现类必须实现所有抽象方法。
"""

import pytest
from core import (
    DataProvider, IndicatorCalculator, PivotDetector, PatternDetector,
    SignalGenerator, MarketRegimeAnalyzer, FakeSignalAnalyzer,
    StockAnalyzer, BacktestEngine,
    StockData, OHLCV, PatternResult, SignalResult, MarketRegime,
    FakeSignalRisk, AnalysisResult, PivotPoint,
    TrendDirection, PatternStage, SignalType, ConfidenceLevel,
)


class TestAbstractInterfaces:
    """验证抽象基类不能直接实例化"""

    def test_data_provider_is_abstract(self):
        with pytest.raises(TypeError):
            DataProvider()

    def test_indicator_calculator_is_abstract(self):
        with pytest.raises(TypeError):
            IndicatorCalculator()

    def test_pivot_detector_is_abstract(self):
        with pytest.raises(TypeError):
            PivotDetector()

    def test_pattern_detector_is_abstract(self):
        with pytest.raises(TypeError):
            PatternDetector()

    def test_signal_generator_is_abstract(self):
        with pytest.raises(TypeError):
            SignalGenerator()

    def test_market_regime_analyzer_is_abstract(self):
        with pytest.raises(TypeError):
            MarketRegimeAnalyzer()

    def test_fake_signal_analyzer_is_abstract(self):
        with pytest.raises(TypeError):
            FakeSignalAnalyzer()

    def test_stock_analyzer_is_abstract(self):
        with pytest.raises(TypeError):
            StockAnalyzer()

    def test_backtest_engine_is_abstract(self):
        with pytest.raises(TypeError):
            BacktestEngine()


class TestPatternDetectorInterface:
    """PatternDetector 接口验证"""

    def test_concrete_implementation_works(self):
        """验证具体实现类可以正常工作"""

        class TestPattern(PatternDetector):
            @property
            def pattern_id(self):
                return "test_pattern"

            @property
            def pattern_name(self):
                return "测试形态"

            def detect(self, klines, pivots=None):
                return [PatternResult(
                    pattern_id="test_pattern",
                    pattern_name="测试形态",
                    direction=TrendDirection.BULL,
                    stage=PatternStage.FORMING,
                    score=50,
                    confidence=ConfidenceLevel.C,
                    key_points=[],
                )]

        detector = TestPattern()
        assert detector.pattern_id == "test_pattern"
        assert detector.pattern_name == "测试形态"

        klines = [OHLCV(date="2026-01-01", open=10, high=11, low=9, close=10.5, volume=1000)]
        results = detector.detect(klines)
        assert len(results) == 1
        assert results[0].pattern_id == "test_pattern"

    def test_missing_abstract_method_raises(self):
        """验证缺少抽象方法的类不能实例化"""

        class IncompletePattern(PatternDetector):
            @property
            def pattern_id(self):
                return "incomplete"
            # 缺少 pattern_name 和 detect

        with pytest.raises(TypeError):
            IncompletePattern()


class TestDataProviderInterface:
    """DataProvider 接口验证"""

    def test_concrete_implementation_works(self):
        class TestProvider(DataProvider):
            def get_stock_data(self, code, days=250):
                return StockData(code=code, name="测试", klines=[])

            def get_index_data(self, code, days=250):
                return StockData(code=code, name="指数", klines=[])

            def get_sector_constituents(self, sector_code):
                return ["000001", "000002"]

            def fetch_klines(self, symbol, start_date=None, end_date=None, frequency=None, adjustment=None):
                raise NotImplementedError

            def validate_data(self, series):
                raise NotImplementedError

            def get_trading_calendar(self, exchange, start_date, end_date):
                return []

        provider = TestProvider()
        data = provider.get_stock_data("002436")
        assert data.code == "002436"

        constituents = provider.get_sector_constituents("886015")
        assert len(constituents) == 2


class TestPivotDetectorInterface:
    """PivotDetector 接口验证"""

    def test_concrete_implementation_works(self):
        class TestPivot(PivotDetector):
            def detect(self, klines, left_bars=5, right_bars=5):
                return [PivotPoint(
                    index=0,
                    date="2026-01-01",
                    price=10.5,
                    pivot_type="swing_high",
                    strength=5,
                )]

        detector = TestPivot()
        klines = [OHLCV(date="2026-01-01", open=10, high=11, low=9, close=10.5, volume=1000)]
        pivots = detector.detect(klines)
        assert len(pivots) == 1
        assert pivots[0].pivot_type == "swing_high"


class TestStockAnalyzerInterface:
    """StockAnalyzer 接口验证"""

    def test_concrete_implementation_works(self):
        class TestAnalyzer(StockAnalyzer):
            def analyze(self, code, days=250):
                return AnalysisResult(
                    stock=StockData(code=code, name="测试", klines=[]),
                    patterns=[],
                    buy_signals=[],
                    sell_signals=[],
                )

            def analyze_with_data(self, stock_data):
                return AnalysisResult(
                    stock=stock_data,
                    patterns=[],
                    buy_signals=[],
                    sell_signals=[],
                )

        analyzer = TestAnalyzer()
        result = analyzer.analyze("002436")
        assert result.stock.code == "002436"


class TestBacktestEngineInterface:
    """BacktestEngine 接口验证"""

    def test_concrete_implementation_works(self):
        class TestBacktest(BacktestEngine):
            def backtest(self, code, signal_date, horizon_days=20):
                return {"return": 0.05, "mfe": 0.1, "mae": -0.02}

            def batch_backtest(self, signals, horizon_days=20):
                return {"win_rate": 0.6, "sample_size": len(signals)}

        engine = TestBacktest()
        result = engine.backtest("002436", "2026-09-01", 20)
        assert result['return'] == 0.05

        batch = engine.batch_backtest([{"code": "002436", "date": "2026-09-01"}], 20)
        assert batch['sample_size'] == 1
