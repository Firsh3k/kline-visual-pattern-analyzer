"""
V5 测试 — BacktestResult / BacktestSummary 类型化输出契约验证

确保回测输出使用类型化数据类，而不是自由格式 Dict[str, Any]。
"""

import pytest
import json
from core import BacktestResult, BacktestSummary


class TestBacktestResult:
    """单信号回测结果"""

    def test_create_complete(self):
        result = BacktestResult(
            code="002436",
            signal_date="2026-09-01",
            horizon_days=20,
            entry_price=10.0,
            return_pct=5.0,
            mfe_pct=8.0,
            mae_pct=-2.0,
            final_price=10.5,
            actual_days=20,
            is_complete=True,
        )
        assert result.code == "002436"
        assert result.is_complete is True
        assert result.return_pct == 5.0

    def test_create_incomplete(self):
        """数据不足时的回测结果"""
        result = BacktestResult(
            code="002436",
            signal_date="2026-09-25",
            horizon_days=20,
            entry_price=10.0,
            return_pct=None,
            mfe_pct=None,
            mae_pct=None,
            final_price=None,
            actual_days=5,
            is_complete=False,
            notes="future data不足",
        )
        assert result.is_complete is False
        assert result.return_pct is None
        assert result.actual_days == 5

    def test_to_dict(self):
        result = BacktestResult(
            code="002436",
            signal_date="2026-09-01",
            horizon_days=20,
            entry_price=10.0,
            return_pct=5.0,
            is_complete=True,
        )
        d = result.to_dict()
        assert d['code'] == "002436"
        assert d['horizon_days'] == 20
        assert d['return_pct'] == 5.0
        assert d['is_complete'] is True

    def test_from_dict(self):
        data = {
            'code': '002436',
            'signal_date': '2026-09-01',
            'horizon_days': 20,
            'entry_price': 10.0,
            'return_pct': 5.0,
            'is_complete': True,
            'extra_field': 'should be ignored',
        }
        result = BacktestResult.from_dict(data)
        assert result.code == "002436"
        assert result.return_pct == 5.0

    def test_roundtrip(self):
        """序列化→反序列化 往返测试"""
        original = BacktestResult(
            code="002436",
            signal_date="2026-09-01",
            horizon_days=20,
            entry_price=10.0,
            return_pct=5.0,
            mfe_pct=8.0,
            mae_pct=-2.0,
            is_complete=True,
        )
        restored = BacktestResult.from_dict(original.to_dict())
        assert restored.to_dict() == original.to_dict()

    def test_json_serializable(self):
        result = BacktestResult(
            code="002436",
            signal_date="2026-09-01",
            horizon_days=20,
            entry_price=10.0,
            is_complete=True,
        )
        d = result.to_dict()
        json_str = json.dumps(d)
        assert json.loads(json_str)['code'] == "002436"

    def test_required_fields(self):
        """验证必填字段"""
        with pytest.raises(TypeError):
            BacktestResult()  # 缺少必填字段


class TestBacktestSummary:
    """批量回测汇总"""

    def test_create(self):
        summary = BacktestSummary(
            horizon_days=20,
            sample_size=100,
            win_rate=65.0,
            avg_return_pct=3.5,
            median_return_pct=2.8,
            avg_mfe_pct=6.0,
            avg_mae_pct=-2.5,
            profit_factor=1.8,
        )
        assert summary.sample_size == 100
        assert summary.win_rate == 65.0
        assert summary.profit_factor == 1.8

    def test_create_with_results(self):
        r1 = BacktestResult(code="001", signal_date="2026-01-01", horizon_days=20,
                             entry_price=10.0, return_pct=5.0, is_complete=True)
        r2 = BacktestResult(code="002", signal_date="2026-01-02", horizon_days=20,
                             entry_price=20.0, return_pct=-3.0, is_complete=True)
        summary = BacktestSummary(
            horizon_days=20,
            sample_size=2,
            win_rate=50.0,
            avg_return_pct=1.0,
            results=[r1, r2],
        )
        assert len(summary.results) == 2
        assert summary.results[0].code == "001"

    def test_to_dict(self):
        summary = BacktestSummary(
            horizon_days=20,
            sample_size=50,
            win_rate=60.0,
        )
        d = summary.to_dict()
        assert d['horizon_days'] == 20
        assert d['sample_size'] == 50
        assert d['win_rate'] == 60.0
        assert 'results' in d

    def test_incomplete_count(self):
        summary = BacktestSummary(
            horizon_days=20,
            sample_size=10,
            incomplete_count=3,
        )
        assert summary.incomplete_count == 3

    def test_empty_samples(self):
        """无有效样本时的汇总"""
        summary = BacktestSummary(
            horizon_days=20,
            sample_size=0,
            win_rate=None,
            avg_return_pct=None,
        )
        assert summary.sample_size == 0
        assert summary.win_rate is None
