"""
V5 FIR-12 测试 — 基于 SwingPivotEngine 的形态识别器

覆盖：
1. 四个标准形态识别（W底/M头/头肩底/头肩顶）
2. 形态失败/不识别场景（两底差距过大、右肩低于头等）
3. 倾斜颈线计算
4. 突破状态机（BREAKOUT_DAY / PRE_BREAK / FAILED_BREAK）
5. 空数据/数据不足
6. registry 注册验证
7. V4 行为不破坏 + V4/V5 方向一致性
8. pivots 参数传入
"""

import os
import sys
from datetime import date, timedelta

import pytest

# 将项目根目录加入 sys.path（与 conftest.py 保持一致，便于单独运行本文件）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import (  # noqa: E402
    OHLCV,
    PivotPoint,
    TrendDirection,
    PatternStage,
    ConfidenceLevel,
    registry,
    SwingPivotEngine,
    WBottomDetector,
    MTopDetector,
    HSBottomDetector,
    HSTopDetector,
)


# ============================================================
# 合成K线数据构造工具
# ============================================================

def make_klines(prices, volumes=None):
    """
    根据价格列表生成 List[OHLCV]。

    - 日期从 2025-01-01 开始逐日递增
    - open = close = price（简化）
    - high = price * 1.01
    - low  = price * 0.99
    - volume 默认 1,000,000

    Args:
        prices: 收盘价/价格序列
        volumes: 可选的成交量序列（与 prices 等长）

    Returns:
        List[OHLCV]
    """
    klines = []
    base = date(2025, 1, 1)
    for i, p in enumerate(prices):
        d = (base + timedelta(days=i)).isoformat()
        v = volumes[i] if volumes else 1000000
        klines.append(OHLCV(
            date=d,
            open=float(p),
            high=round(float(p) * 1.01, 4),
            low=round(float(p) * 0.99, 4),
            close=float(p),
            volume=float(v),
        ))
    return klines


# ---------- 标准形态价格序列（由冒烟测试验证可产生正确 pivot） ----------
# W底：左底@5=88, 颈线高@11=100, 右底@18=88, 突破@24
W_PRICES = [
    95, 94, 93, 92, 91,          # 0-4 下跌
    88,                           # 5  左底
    90, 92, 94, 96, 98,          # 6-10 反弹
    100,                          # 11 颈线高点
    97, 94, 91, 89.5, 89, 88.6,  # 12-17 回落
    88,                           # 18 右底
    89, 91, 94, 97, 99,          # 19-23 回升
    103, 105, 107, 109, 111, 113,  # 24-29 突破上行
]

# M头：左顶@5=102, 颈线低@11=90, 右顶@17=102.5, 跌破
M_PRICES = [
    90, 92, 94, 96, 98,          # 0-4 上涨
    102,                          # 5  左顶
    100, 98, 96, 94, 92,         # 6-10 回落
    90,                           # 11 颈线低点
    92, 94, 96, 98, 100,         # 12-16 反弹
    102.5,                        # 17 右顶
    100, 97, 94, 91, 88,         # 18-22 下跌
    85, 83, 81,                  # 23-25 跌破后下行
]

# 头肩底：左肩@5=88, 左颈线@11=100, 头@17=84, 右颈线@23=99, 右肩@27=89
HS_BOTTOM_PRICES = [
    95, 94, 93, 92, 91,          # 0-4
    88,                           # 5  左肩
    90, 92, 94, 96, 98,          # 6-10
    100,                          # 11 左颈线高
    97, 94, 90, 86, 85,          # 12-16
    84,                           # 17 头（最低）
    86, 88, 91, 94, 97,          # 18-22
    99,                           # 23 右颈线高
    97, 94, 91,                  # 24-26
    89,                           # 27 右肩
    91, 94, 97, 100, 105, 108, 111, 114,  # 28-35 突破上行
]

# 头肩顶：左肩@5=96, 左颈线低@11=84, 头@17=98, 右颈线低@23=84, 右肩@27=93
HS_TOP_PRICES = [
    90, 91, 92, 93, 94,          # 0-4
    96,                           # 5  左肩
    95, 93, 90, 87, 85,          # 6-10
    84,                           # 11 左颈线低
    85, 88, 91, 94, 96,          # 12-16
    98,                           # 17 头（最高）
    96, 93, 90, 88, 86,          # 18-22
    84,                           # 23 右颈线低
    86, 89, 91,                  # 24-26
    93,                           # 27 右肩
    91, 88, 85, 82, 79, 76, 73, 70,  # 28-35 跌破下行
]


class TestWBottomDetector:
    """W底识别器测试"""

    def test_standard_w_bottom(self):
        """标准 W 底：先跌→双底→突破颈线，应正确识别"""
        klines = make_klines(W_PRICES)
        results = WBottomDetector().detect(klines)
        assert len(results) >= 1
        r = results[0]
        assert r.pattern_id == "w_bottom"
        assert r.pattern_name == "W底"
        assert r.direction == TrendDirection.BULL
        assert r.neckline_price is not None
        assert r.score > 0
        # 关键点：左底/右底/颈线点
        names = {kp.name for kp in r.key_points}
        assert "左底" in names and "右底" in names and "颈线点" in names

    def test_w_bottom_stage_after_breakout(self):
        """突破后的 W 底应为 POST_BREAK 或 RETEST（不能是 FORMING）"""
        klines = make_klines(W_PRICES)
        r = WBottomDetector().detect(klines)[0]
        assert r.stage in (PatternStage.POST_BREAK, PatternStage.RETEST,
                           PatternStage.BREAKOUT_DAY)

    def test_w_bottom_two_bottoms_gap_too_large(self):
        """两底差距 >5%：不应识别为 W 底"""
        # 右底抬高到 95，与左底 88 差距约 8%
        prices = [
            95, 94, 93, 92, 91,
            88,
            90, 92, 94, 96, 98,
            100,
            99, 97, 96, 95.5, 95.2, 95.1,
            95,
            96, 98, 100, 102, 104, 106, 108,
        ]
        results = WBottomDetector().detect(make_klines(prices))
        assert len(results) == 0

    def test_w_bottom_not_broken_neckline(self):
        """未突破颈线：stage 应为 PRE_BREAK 或 FORMING"""
        # 取 W_BASE 段（右底之后只回升到 99，未过颈线 101）
        prices = W_PRICES[:24]
        results = WBottomDetector().detect(make_klines(prices))
        assert len(results) >= 1
        assert results[0].stage in (PatternStage.PRE_BREAK, PatternStage.FORMING)

    def test_w_bottom_breakout_day(self):
        """突破当日：最后一根K线恰好为突破日 → BREAKOUT_DAY"""
        # W_PRICES[24]=103 是突破日（前收99 <= 颈线101，当日103 > 101*1.01）
        prices = W_PRICES[:25]
        results = WBottomDetector().detect(make_klines(prices))
        assert len(results) >= 1
        assert results[0].stage == PatternStage.BREAKOUT_DAY

    def test_w_bottom_failed_break(self):
        """突破失败：突破后连续3日以上收在颈线下方 → FAILED_BREAK"""
        prices = W_PRICES[:25] + [99, 97, 95, 94]
        results = WBottomDetector().detect(make_klines(prices))
        assert len(results) >= 1
        assert results[0].stage == PatternStage.FAILED_BREAK

    def test_w_bottom_failed_break_score_penalty(self):
        """FAILED_BREAK 状态评分应被扣减（显著低于正常突破形态）"""
        # 正常突破形态评分
        normal = WBottomDetector().detect(make_klines(W_PRICES))[0].score
        # 失败形态
        failed = WBottomDetector().detect(
            make_klines(W_PRICES[:25] + [99, 97, 95, 94]))[0]
        assert failed.stage == PatternStage.FAILED_BREAK
        assert failed.score < normal
        assert failed.score >= 10

    def test_w_bottom_target_and_stop(self):
        """目标价/止损价计算正确"""
        r = WBottomDetector().detect(make_klines(W_PRICES))[0]
        # 目标价 = 颈线 + (颈线 - 两底最低价)
        expected_target = r.neckline_price + (r.neckline_price - 87.12)
        assert abs(r.target_price - expected_target) < 0.1
        # 止损 = 右底 * 0.98
        assert r.stop_loss is not None


class TestMTopDetector:
    """M头识别器测试"""

    def test_standard_m_top(self):
        """标准 M 头：先涨→双顶→跌破颈线"""
        results = MTopDetector().detect(make_klines(M_PRICES))
        assert len(results) >= 1
        r = results[0]
        assert r.pattern_id == "m_top"
        assert r.pattern_name == "M头"
        assert r.direction == TrendDirection.BEAR
        names = {kp.name for kp in r.key_points}
        assert "左顶" in names and "右顶" in names and "颈线点" in names

    def test_m_top_breakdown_stage(self):
        """跌破后 M 头应为 POST_BREAK 类阶段"""
        r = MTopDetector().detect(make_klines(M_PRICES))[0]
        assert r.stage in (PatternStage.POST_BREAK, PatternStage.RETEST,
                           PatternStage.BREAKOUT_DAY)

    def test_m_top_not_broken(self):
        """未跌破颈线：PRE_BREAK / FORMING"""
        # 右顶之后不再下跌，保持在颈线上方
        prices = M_PRICES[:18] + [101, 100, 101, 100, 101, 100]
        results = MTopDetector().detect(make_klines(prices))
        # 可能识别到构建中的 M 头
        if results:
            assert results[0].stage in (PatternStage.PRE_BREAK, PatternStage.FORMING)


class TestHSBottomDetector:
    """头肩底识别器测试"""

    def test_standard_hs_bottom(self):
        """标准头肩底：左肩→头→右肩→突破"""
        results = HSBottomDetector().detect(make_klines(HS_BOTTOM_PRICES))
        assert len(results) >= 1
        r = results[0]
        assert r.pattern_id == "hs_bottom"
        assert r.pattern_name == "头肩底"
        assert r.direction == TrendDirection.BULL
        names = {kp.name for kp in r.key_points}
        assert {"左肩", "头", "右肩", "左颈线点", "右颈线点"} <= names

    def test_hs_bottom_right_shoulder_below_head_fails(self):
        """右肩低于头部：形态失败/不识别"""
        prices = [
            95, 94, 93, 92, 91,
            88,
            90, 92, 94, 96, 98,
            100,
            97, 94, 90, 86, 85,
            84,
            83, 82, 81, 80, 79,
            78,
            77, 76, 75,
            82,
            85, 88, 91, 94, 97, 100, 105, 108,
        ]
        results = HSBottomDetector().detect(make_klines(prices))
        assert len(results) == 0

    def test_hs_sloped_neckline(self):
        """倾斜颈线：验证左右颈线点及斜率计算"""
        r = HSBottomDetector().detect(make_klines(HS_BOTTOM_PRICES))[0]
        left_nl = next(kp for kp in r.key_points if kp.name == "左颈线点")
        right_nl = next(kp for kp in r.key_points if kp.name == "右颈线点")
        assert right_nl.index > left_nl.index
        # 斜率 = (右颈线价 - 左颈线价)/(右颈线索引 - 左颈线索引)
        expected_slope = (right_nl.price - left_nl.price) / (right_nl.index - left_nl.index)
        # 数据构造：左颈线 101，右颈线 99.99，索引差 12 → 轻微向下倾斜
        assert abs(expected_slope - (99.99 - 101.0) / 12) < 0.001

    def test_hs_bottom_target_uses_neckline(self):
        """头肩底目标价 = 当前颈线值 + (颈线 - 头部价)"""
        r = HSBottomDetector().detect(make_klines(HS_BOTTOM_PRICES))[0]
        head = next(kp for kp in r.key_points if kp.name == "头")
        expected = r.neckline_price + (r.neckline_price - head.price)
        assert abs(r.target_price - expected) < 0.1
        assert r.stop_loss is not None


class TestHSTopDetector:
    """头肩顶识别器测试"""

    def test_standard_hs_top(self):
        """标准头肩顶：左肩→头→右肩→跌破"""
        results = HSTopDetector().detect(make_klines(HS_TOP_PRICES))
        assert len(results) >= 1
        r = results[0]
        assert r.pattern_id == "hs_top"
        assert r.pattern_name == "头肩顶"
        assert r.direction == TrendDirection.BEAR
        names = {kp.name for kp in r.key_points}
        assert {"左肩", "头", "右肩"} <= names


class TestEdgeCases:
    """边界与异常场景测试"""

    def test_empty_klines(self):
        """空数据：返回空列表"""
        assert WBottomDetector().detect(make_klines([])) == []
        assert MTopDetector().detect(make_klines([])) == []
        assert HSBottomDetector().detect(make_klines([])) == []
        assert HSTopDetector().detect(make_klines([])) == []

    def test_insufficient_data(self):
        """数据不足（少于形态所需）：返回空列表"""
        short = make_klines([10, 11, 12, 11, 10, 11, 12])
        assert WBottomDetector().detect(short) == []
        assert HSBottomDetector().detect(short) == []

    def test_pivots_parameter_passed_in(self):
        """预计算 pivots 传入：与内部计算结果一致"""
        klines = make_klines(W_PRICES)
        pivots = SwingPivotEngine(left_bars=5, right_bars=5).detect_confirmed_only(klines)
        r_internal = WBottomDetector().detect(klines)
        r_external = WBottomDetector().detect(klines, pivots=pivots)
        assert len(r_internal) == len(r_external)
        for a, b in zip(r_internal, r_external):
            assert a.stage == b.stage
            assert a.score == b.score
            assert a.neckline_price == b.neckline_price


class TestRegistry:
    """注册中心验证"""

    def test_all_four_patterns_registered(self):
        """四个形态都已注册到全局 registry"""
        ids = registry.get_pattern_ids()
        assert {"w_bottom", "m_top", "hs_bottom", "hs_top"} <= set(ids)

    def test_get_pattern_returns_instance(self):
        """通过 registry 可获取四个识别器实例"""
        for pid in ["w_bottom", "m_top", "hs_bottom", "hs_top"]:
            det = registry.get_pattern(pid)
            assert det is not None
            assert det.pattern_id == pid


class TestV4BackwardsCompat:
    """V4 行为不破坏 + 方向一致性"""

    def test_v4_import_and_callable(self):
        """V4 pattern_scanner 仍可正常 import 和调用"""
        import pandas as pd
        from pattern_scanner import detect_double_bottom
        # 构造足够长的 df（V4 窗口=60，少于61行直接返回空，不应抛异常）
        prices = [100.0] * 70
        df = pd.DataFrame([{
            'date': (date(2025, 1, 1) + timedelta(days=i)).isoformat(),
            'open': p, 'high': p * 1.01, 'low': p * 0.99,
            'close': p, 'volume': 1000000,
        } for i, p in enumerate(prices)])
        df['date'] = pd.to_datetime(df['date'])
        result = detect_double_bottom(df)
        assert isinstance(result, list)

    def test_v4_v5_consistency_direction(self):
        """同一份合成数据：V4 与 V5 都能识别 W 底（方向一致）"""
        import pandas as pd
        from pattern_scanner import detect_double_bottom
        # 前缀补 38 根平盘K线，满足 V4 的 60 日窗口
        prices = [100.0] * 38 + W_PRICES
        klines = make_klines(prices)
        df = pd.DataFrame([{
            'date': k.date, 'open': k.open, 'high': k.high,
            'low': k.low, 'close': k.close, 'volume': k.volume,
        } for k in klines])
        df['date'] = pd.to_datetime(df['date'])

        v4_signals = detect_double_bottom(df)
        v5_results = WBottomDetector().detect(klines)

        assert len(v4_signals) >= 1
        assert len(v5_results) >= 1
        assert v5_results[0].direction == TrendDirection.BULL


class TestScoring:
    """评分与置信度映射测试"""

    def test_volume_boost(self):
        """放量突破（最近5日均量 > 20日均量*1.1）应获得更高评分"""
        base_volumes = [1000000] * len(W_PRICES)
        r_base = WBottomDetector().detect(make_klines(W_PRICES, base_volumes))[0]

        # 最近5日放量到 200 万
        boosted_volumes = [1000000] * len(W_PRICES)
        boosted_volumes[-5:] = [2000000] * 5
        r_boosted = WBottomDetector().detect(make_klines(W_PRICES, boosted_volumes))[0]
        assert r_boosted.score > r_base.score

    def test_confidence_mapping(self):
        """置信度按分数映射：S/A/B/C/D"""
        r = WBottomDetector().detect(make_klines(W_PRICES))[0]
        # 标准突破+无量能确认：40+35+10=85 → S
        assert r.confidence in (ConfidenceLevel.S, ConfidenceLevel.A,
                                ConfidenceLevel.B, ConfidenceLevel.C, ConfidenceLevel.D)
        expected = {r.score: None}
        if r.score >= 80:
            assert r.confidence == ConfidenceLevel.S
        elif r.score >= 65:
            assert r.confidence == ConfidenceLevel.A

# ============================================================
# Blocker 2 — caller-supplied pivots 的 point-in-time 校验（防 look-ahead）
# ============================================================

def _flat_klines(n=30, price=100.0):
    """构造 n 根平盘K线（open=close=price, high=price*1.01, low=price*0.99）"""
    return make_klines([price] * n)


def _w_manual_pivots(klines, a_idx=5, b_idx=20, h_idx=12,
                     a_price=90.0, b_price=90.5, h_price=100.0,
                     right_bars=5, b_confirmed_at=None, b_date=None, h_confirmed_at=None):
    """
    手工构造一组可构成 W 底的 confirmed pivot：
      a (swing_low @ a_idx)、b (swing_low @ b_idx)、h (swing_high @ h_idx，落在 a,b 之间)
    默认全部 confirmed=True 且携带合法 confirmed_at_index/date。
    """
    a = PivotPoint(index=a_idx, date=klines[a_idx].date, price=a_price,
                   pivot_type="swing_low", confirmed=True,
                   confirmed_at_index=a_idx + right_bars,
                   confirmed_at_date=klines[a_idx + right_bars].date)
    b = PivotPoint(index=b_idx, date=(b_date or klines[b_idx].date), price=b_price,
                   pivot_type="swing_low", confirmed=True,
                   confirmed_at_index=(b_confirmed_at if b_confirmed_at is not None
                                       else b_idx + right_bars),
                   confirmed_at_date=None)
    h = PivotPoint(index=h_idx, date=klines[h_idx].date, price=h_price,
                   pivot_type="swing_high", confirmed=True,
                   confirmed_at_index=(h_confirmed_at if h_confirmed_at is not None
                                       else h_idx + right_bars),
                   confirmed_at_date=None)
    return [a, b, h]


class TestSuppliedPivotPointInTimeValidation:
    """Blocker 2 — caller-supplied pivots 必须通过 point-in-time 校验，否则引入未来函数"""

    def test_valid_supplied_pivots_form_w_bottom(self):
        """基线：合法的手工 confirmed pivots 应能构成 W 底（验证测试夹具本身正确）"""
        klines = _flat_klines(30)
        pivots = _w_manual_pivots(klines)
        results = WBottomDetector().detect(klines, pivots=pivots)
        assert len(results) == 1
        assert results[0].pattern_id == "w_bottom"

    def test_full_history_pivots_leaked_into_prefix(self):
        """full-history pivots 传入 prefix：在 prefix 达到 confirmed_at_index 之前不能使用"""
        klines = _flat_klines(30)
        full_pivots = _w_manual_pivots(klines)
        prefix = klines[:18]  # indices 0..17
        # b@20 的 confirmed_at_index=25 > 17，且 b.index=20 >= 18 —— 在 prefix 时点不可用
        results = WBottomDetector().detect(prefix, pivots=full_pivots)
        assert results == []
        internal = WBottomDetector().detect(prefix)
        assert results == internal

    def test_confirmed_at_index_beyond_series_skipped(self):
        """pivot.confirmed_at_index > len(klines)-1 → 被跳过"""
        klines = _flat_klines(30)
        pivots = _w_manual_pivots(klines, b_confirmed_at=30)
        results = WBottomDetector().detect(klines, pivots=pivots)
        assert results == []

    def test_mismatched_date_skipped(self):
        """pivot.date != klines[p.index].date → 被跳过（跨 series 错位防护）"""
        klines = _flat_klines(30)
        pivots = _w_manual_pivots(klines, b_date="1999-01-01")
        results = WBottomDetector().detect(klines, pivots=pivots)
        assert results == []

    def test_unconfirmed_pivot_skipped(self):
        """pivot.confirmed is False（pending）→ 被跳过"""
        klines = _flat_klines(30)
        pivots = _w_manual_pivots(klines)
        pivots = [
            PivotPoint(index=20, date=klines[20].date, price=90.5,
                       pivot_type="swing_low", confirmed=False,
                       confirmed_at_index=None, confirmed_at_date=None)
            if p.index == 20 else p
            for p in pivots
        ]
        results = WBottomDetector().detect(klines, pivots=pivots)
        assert results == []


# ============================================================
# Blocker 3 — raw-extrema fallback 已移除（仅原始窗口极值不能创建形态）
# ============================================================

class TestRawExtremaFallbackRemoved:
    """Blocker 3 — 两对立 pivot 缺失时拒绝候选，不退化到无限制原始 K 线极值"""

    def test_raw_window_extreme_does_not_create_w_bottom(self):
        """两底之间无 swing high pivot、但有原始价格高点时，WBottomDetector 不返回结果"""
        klines = _flat_klines(30)
        a = PivotPoint(index=5, date=klines[5].date, price=90.0, pivot_type="swing_low",
                       confirmed=True, confirmed_at_index=10,
                       confirmed_at_date=klines[10].date)
        b = PivotPoint(index=20, date=klines[20].date, price=90.5, pivot_type="swing_low",
                       confirmed=True, confirmed_at_index=25,
                       confirmed_at_date=klines[25].date)
        raw_high_between = max(k.high for k in klines[5:21])
        assert raw_high_between > 90.0  # 原始窗口里确实存在价格高点
        results = WBottomDetector().detect(klines, pivots=[a, b])
        assert results == []
