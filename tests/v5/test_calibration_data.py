"""
FIR-13: Empirical Calibration — 数据采集层 & 信号生成层测试

重点验证：
1. market regime 三分法（BULL/BEAR/RANGE）
2. **防 look-ahead**：修改信号日之后的未来数据，已生成信号必须不变
3. 形态检测正确性（ma_bull / platform_break）
4. entry_price = 信号日收盘价
5. 数据不足不产生信号
"""

import os
import sys
from datetime import date, timedelta

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.types import OHLCV
from core.calibration.types import PatternType, MarketRegime, ConfirmationState
from core.calibration.data_collector import CalibrationDataCollector
from core.calibration.signal_generator import CalibrationSignalGenerator


# ---------------------------------------------------------------------------
# fixture 构造工具
# ---------------------------------------------------------------------------

def _gen_dates(n: int, start: str = "2024-01-02"):
    """生成 n 个连续日历日日期字符串（YYYY-MM-DD）。"""
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def _make_klines(
    closes,
    opens=None,
    highs=None,
    lows=None,
    volumes=None,
    start: str = "2024-01-02",
):
    """从价格序列构造 List[OHLCV]。缺省的 open/high/low/volume 给确定性默认值。"""
    n = len(closes)
    dates = _gen_dates(n, start)
    opens = opens if opens is not None else [c - 0.1 for c in closes]
    highs = highs if highs is not None else [c + 0.2 for c in closes]
    lows = lows if lows is not None else [c - 0.2 for c in closes]
    volumes = volumes if volumes is not None else [1000.0] * n
    return [
        OHLCV(
            date=dates[i],
            open=round(opens[i], 4),
            high=round(highs[i], 4),
            low=round(lows[i], 4),
            close=round(closes[i], 4),
            volume=float(volumes[i]),
        )
        for i in range(n)
    ]


def _rising_klines(n: int = 90, start_price: float = 100.0, step: float = 0.3):
    """单调上升序列：用于稳定触发 ma_bull。"""
    closes = [start_price + i * step for i in range(n)]
    return _make_klines(closes)


@pytest.fixture
def collector(tmp_path):
    return CalibrationDataCollector(cache_dir=str(tmp_path / "cal"), use_cache=False)


@pytest.fixture
def generator():
    return CalibrationSignalGenerator()


# ---------------------------------------------------------------------------
# 1. market regime
# ---------------------------------------------------------------------------

def test_market_regime_bull(collector):
    """上升趋势指数 → BULL：收盘>MA60 且 MA60 向上。"""
    closes = [100.0 + i * 0.5 for i in range(120)]
    idx = _make_klines(closes)
    regime = collector.determine_market_regime(idx, idx[-1].date)
    assert regime == MarketRegime.BULL


def test_market_regime_bear(collector):
    """下降趋势指数 → BEAR：收盘<MA60 且 MA60 向下。"""
    closes = [200.0 - i * 0.5 for i in range(120)]
    idx = _make_klines(closes)
    regime = collector.determine_market_regime(idx, idx[-1].date)
    assert regime == MarketRegime.BEAR


def test_market_regime_range(collector):
    """横盘指数 → RANGE：收盘≈MA60，方向不明。"""
    closes = [100.0 for _ in range(120)]
    idx = _make_klines(closes)
    regime = collector.determine_market_regime(idx, idx[-1].date)
    assert regime == MarketRegime.RANGE


# ---------------------------------------------------------------------------
# 2. 防 look-ahead（核心）
# ---------------------------------------------------------------------------

def test_signal_lookahead_safety(generator):
    """修改信号日之后的未来数据，重新生成，信号日及之前的信号必须完全不变。"""
    base = _rising_klines(n=90)
    signals_a = generator.generate_signals(base, "999999", {})

    cut_idx = 80  # 只动 cut_idx 之后的"未来"K线
    cutoff_date = base[cut_idx].date

    # 篡改未来数据：把 cut_idx 之后的价格全部砸到 1.0，量放到极端
    future = [
        OHLCV(
            date=k.date,
            open=1.0, high=1.1, low=0.9, close=1.0, volume=9e9,
        )
        for k in base[cut_idx + 1:]
    ]
    tampered = base[:cut_idx + 1] + future
    signals_b = generator.generate_signals(tampered, "999999", {})

    # 只比较 cutoff 日期及之前的信号
    before_a = sorted(
        [(s.signal_id, s.entry_price) for s in signals_a if s.signal_date <= cutoff_date]
    )
    before_b = sorted(
        [(s.signal_id, s.entry_price) for s in signals_b if s.signal_date <= cutoff_date]
    )
    assert before_a == before_b, (
        f"look-ahead 泄漏！未来数据修改影响了已生成信号: "
        f"{set(before_a) ^ set(before_b)}"
    )


# ---------------------------------------------------------------------------
# 3. 形态检测
# ---------------------------------------------------------------------------

def test_ma_bull_detection(generator):
    """构造均线多头+突破数据，应检测到 ma_bull 信号。"""
    klines = _rising_klines(n=90)
    signals = generator.generate_signals(klines, "600519", {})
    ma_bull = [s for s in signals if s.pattern_type == PatternType.MA_BULL]
    assert len(ma_bull) >= 1, "单调上升趋势应至少产生一个 ma_bull 信号"
    # 所有 ma_bull 信号都应是突破日状态
    assert all(s.confirmation_state == ConfirmationState.BREAKOUT_DAY for s in ma_bull)
    # 评分：均线类 = 70
    assert all(s.pattern_score == 70 for s in ma_bull)


def test_platform_break_detection(generator):
    """长期横盘后放量大涨突破 → platform_break。"""
    n = 66
    closes = [10.0] * n
    opens = [10.0] * n
    highs = [10.2] * n
    lows = [9.8] * n
    volumes = [1000.0] * n

    # 第 65 根：+6% 大阳线、放量 5 倍
    closes[65] = 10.6
    opens[65] = 10.0
    highs[65] = 10.7
    lows[65] = 10.0
    volumes[65] = 5000.0

    klines = _make_klines(closes, opens, highs, lows, volumes)
    signals = generator.generate_signals(klines, "000001", {})
    pb = [s for s in signals if s.pattern_type == PatternType.PLATFORM_BREAK]
    assert len(pb) >= 1, "横盘后放量大涨应触发 platform_break"
    sig = pb[0]
    assert sig.signal_date == klines[65].date
    assert sig.entry_price == 10.6
    assert sig.pattern_score == 80


# ---------------------------------------------------------------------------
# 4. entry_price 正确性
# ---------------------------------------------------------------------------

def test_signal_entry_price(generator):
    """entry_price 必须等于信号日收盘价。"""
    klines = _rising_klines(n=90)
    close_by_date = {k.date: k.close for k in klines}
    signals = generator.generate_signals(klines, "600519", {})
    assert signals, "上升趋势应有信号"
    for s in signals:
        assert s.entry_price == close_by_date[s.signal_date]


# ---------------------------------------------------------------------------
# 5. 数据不足不产生信号
# ---------------------------------------------------------------------------

def test_no_signals_insufficient_data(generator):
    """K线数 < MIN_START(60) 时不产生任何信号。"""
    klines = _rising_klines(n=30)
    signals = generator.generate_signals(klines, "600519", {})
    assert signals == []

    # 数据刚到 60 根、但仍是横盘无突破 → 也不应有突破类信号
    flat = _make_klines([10.0] * 65)
    signals_flat = generator.generate_signals(flat, "600519", {})
    assert all(
        s.pattern_type not in (PatternType.PLATFORM_BREAK, PatternType.PLATFORM_BREAK_DOWN)
        for s in signals_flat
    )
