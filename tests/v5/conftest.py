"""
V5 测试框架 — pytest 配置和 fixtures

所有 V5 算法测试必须使用此配置，确保测试环境一致。
"""

import sys
import os
import pytest

# 将项目根目录加入 sys.path，确保可以 import core
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def sample_ohlcv():
    """单根K线数据 fixture"""
    from core import OHLCV
    return OHLCV(
        date="2026-09-30",
        open=10.50,
        high=11.20,
        low=10.30,
        close=11.00,
        volume=1500000,
        amount=16500000.0,
    )


@pytest.fixture
def sample_klines():
    """20根模拟K线数据 fixture（上升趋势）"""
    from core import OHLCV
    klines = []
    base_price = 10.0
    for i in range(20):
        date = f"2026-09-{i+1:02d}"
        open_p = base_price + i * 0.1
        close_p = base_price + i * 0.12
        high_p = max(open_p, close_p) + 0.2
        low_p = min(open_p, close_p) - 0.1
        volume = 1000000 + i * 50000
        klines.append(OHLCV(
            date=date,
            open=round(open_p, 2),
            high=round(high_p, 2),
            low=round(low_p, 2),
            close=round(close_p, 2),
            volume=volume,
        ))
    return klines


@pytest.fixture
def sample_stock_data(sample_klines):
    """完整股票数据 fixture"""
    from core import StockData
    return StockData(
        code="002436",
        name="兴森科技",
        klines=sample_klines,
        market="A股",
        source="test",
    )


@pytest.fixture
def clean_registry():
    """干净的注册中心 fixture（每个测试独立）"""
    from core import AlgorithmRegistry
    return AlgorithmRegistry()
