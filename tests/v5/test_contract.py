"""
V5 测试 — Canonical Core 契约测试

验证 core schema/字段名/接口签名在运行时边界上的稳定性。
这是 cross-runtime parity 的基础：Python core 定义契约，Web JS 必须遵守。

架构模型：Python Canonical Reference + Mandatory Cross-Runtime Parity
- Python 是规范参考实现
- Web JS 实现必须通过 parity 测试保证字段一致
- 本测试验证 Python 端的契约稳定性
"""

import pytest
import json
from core import (
    OHLCV, StockData, PivotPoint, PatternKeyPoint, PatternResult,
    SignalResult, MarketRegime, FakeSignalRisk, AnalysisResult,
    BacktestResult, BacktestSummary,
    TrendDirection, PatternStage, SignalType, ConfidenceLevel,
    DataProvider, IndicatorCalculator, PivotDetector, PatternDetector,
    SignalGenerator, MarketRegimeAnalyzer, FakeSignalAnalyzer,
    StockAnalyzer, BacktestEngine,
)


class TestDataContractStability:
    """数据结构契约稳定性 — 字段名必须稳定，Web JS 依赖这些字段"""

    def test_ohlcv_fields(self):
        """OHLCV 必须包含 date/open/high/low/close/volume 字段"""
        ohlcv = OHLCV(date="2026-01-01", open=10, high=11, low=9, close=10.5, volume=1000)
        d = ohlcv.to_dict()
        required_fields = ['date', 'open', 'high', 'low', 'close', 'volume']
        for f in required_fields:
            assert f in d, f"OHLCV 缺少必填字段: {f}"

    def test_pattern_result_fields(self):
        """PatternResult 必须包含稳定的字段名"""
        result = PatternResult(
            pattern_id="hs_bottom",
            pattern_name="头肩底",
            direction=TrendDirection.BULL,
            stage=PatternStage.FORMING,
            score=80,
            confidence=ConfidenceLevel.A,
            key_points=[],
        )
        d = result.to_dict()
        required_fields = [
            'pattern_id', 'pattern_name', 'direction', 'stage',
            'score', 'confidence', 'key_points',
        ]
        for f in required_fields:
            assert f in d, f"PatternResult 缺少必填字段: {f}"

    def test_signal_result_fields(self):
        """SignalResult 必须包含稳定的字段名"""
        signal = SignalResult(
            signal_type=SignalType.BUY,
            pattern_id="hs_bottom",
            pattern_name="头肩底",
            score=80,
            confidence=ConfidenceLevel.A,
            trigger_price=10.5,
        )
        d = signal.to_dict()
        required_fields = [
            'signal_type', 'pattern_id', 'pattern_name', 'score',
            'confidence', 'trigger_price',
        ]
        for f in required_fields:
            assert f in d, f"SignalResult 缺少必填字段: {f}"

    def test_backtest_result_fields(self):
        """BacktestResult 必须包含稳定的字段名"""
        result = BacktestResult(
            code="002436",
            signal_date="2026-09-01",
            horizon_days=20,
            entry_price=10.0,
            is_complete=True,
        )
        d = result.to_dict()
        required_fields = [
            'code', 'signal_date', 'horizon_days', 'entry_price',
            'return_pct', 'mfe_pct', 'mae_pct', 'is_complete',
        ]
        for f in required_fields:
            assert f in d, f"BacktestResult 缺少必填字段: {f}"

    def test_backtest_summary_fields(self):
        """BacktestSummary 必须包含稳定的字段名"""
        summary = BacktestSummary(
            horizon_days=20,
            sample_size=100,
            win_rate=65.0,
        )
        d = summary.to_dict()
        required_fields = [
            'horizon_days', 'sample_size', 'win_rate',
            'avg_return_pct', 'results',
        ]
        for f in required_fields:
            assert f in d, f"BacktestSummary 缺少必填字段: {f}"

    def test_enum_values_stable(self):
        """枚举值必须稳定，Web JS 依赖这些字符串"""
        assert TrendDirection.BULL.value == "bull"
        assert TrendDirection.BEAR.value == "bear"
        assert SignalType.BUY.value == "buy"
        assert SignalType.SELL.value == "sell"
        assert PatternStage.BREAKOUT_DAY.value == "breakout_day"
        assert ConfidenceLevel.S.value == "S"
        assert ConfidenceLevel.A.value == "A"


class TestInterfaceContractStability:
    """接口契约稳定性 — 方法签名必须稳定"""

    def test_pattern_detector_interface(self):
        """PatternDetector 必须有 pattern_id, pattern_name, detect 方法"""
        assert hasattr(PatternDetector, 'pattern_id')
        assert hasattr(PatternDetector, 'pattern_name')
        assert hasattr(PatternDetector, 'detect')

    def test_data_provider_interface(self):
        """DataProvider 必须有 get_stock_data, get_index_data, get_sector_constituents"""
        assert hasattr(DataProvider, 'get_stock_data')
        assert hasattr(DataProvider, 'get_index_data')
        assert hasattr(DataProvider, 'get_sector_constituents')

    def test_backtest_engine_return_types(self):
        """BacktestEngine 必须返回类型化结果，而不是 Dict[str, Any]"""
        import inspect
        sig = inspect.signature(BacktestEngine.backtest)
        return_annotation = sig.return_annotation
        # 返回类型应该是 BacktestResult
        assert return_annotation is BacktestResult, \
            f"backtest 返回类型应为 BacktestResult，实际: {return_annotation}"

        sig_batch = inspect.signature(BacktestEngine.batch_backtest)
        assert sig_batch.return_annotation is BacktestSummary, \
            f"batch_backtest 返回类型应为 BacktestSummary"

    def test_stock_analyzer_interface(self):
        """StockAnalyzer 必须有 analyze 和 analyze_with_data"""
        assert hasattr(StockAnalyzer, 'analyze')
        assert hasattr(StockAnalyzer, 'analyze_with_data')


class TestSerializationContract:
    """序列化契约 — 所有数据类必须可 JSON 序列化"""

    def test_all_dataclasses_json_serializable(self):
        """所有核心数据类必须可序列化为 JSON"""
        test_cases = [
            ("OHLCV", OHLCV(date="2026-01-01", open=10, high=11, low=9, close=10.5, volume=1000)),
            ("PivotPoint", PivotPoint(index=0, date="2026-01-01", price=10.5, pivot_type="swing_high")),
            ("PatternKeyPoint", PatternKeyPoint(name="左肩", index=0, date="2026-01-01", price=10.0)),
            ("PatternResult", PatternResult(
                pattern_id="test", pattern_name="测试",
                direction=TrendDirection.BULL, stage=PatternStage.FORMING,
                score=50, confidence=ConfidenceLevel.C, key_points=[],
            )),
            ("SignalResult", SignalResult(
                signal_type=SignalType.BUY, pattern_id="test", pattern_name="测试",
                score=50, confidence=ConfidenceLevel.C, trigger_price=10.0,
            )),
            ("MarketRegime", MarketRegime(
                regime="牛市", score=80, trend=TrendDirection.BULL,
            )),
            ("FakeSignalRisk", FakeSignalRisk(risk_score=30, risk_level="低")),
            ("BacktestResult", BacktestResult(
                code="001", signal_date="2026-01-01", horizon_days=20,
                entry_price=10.0, is_complete=True,
            )),
            ("BacktestSummary", BacktestSummary(horizon_days=20, sample_size=10)),
        ]

        for name, obj in test_cases:
            d = obj.to_dict()
            json_str = json.dumps(d)
            assert json.loads(json_str) is not None, f"{name} 无法 JSON 序列化"


class TestArchitectureModel:
    """架构模型验证 — Python Canonical Reference + Mandatory Parity"""

    def test_python_is_canonical_reference(self):
        """Python core 必须是可导入的规范参考实现"""
        import core
        assert hasattr(core, '__version__')
        assert core.__version__.startswith("5.")

    def test_core_has_complete_public_api(self):
        """core 必须导出完整的公共 API"""
        import core
        required_exports = [
            # 数据结构
            'OHLCV', 'StockData', 'PatternResult', 'SignalResult',
            'BacktestResult', 'BacktestSummary',
            # 接口
            'PatternDetector', 'DataProvider', 'StockAnalyzer', 'BacktestEngine',
            # 注册中心
            'AlgorithmRegistry', 'registry', 'get_registry',
            # 向后兼容
            'detect_swing_pivots',
        ]
        for name in required_exports:
            assert hasattr(core, name), f"core 缺少公共 API: {name}"
            assert name in core.__all__, f"{name} 不在 __all__ 中"
