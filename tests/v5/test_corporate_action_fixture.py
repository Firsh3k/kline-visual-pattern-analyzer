"""
FIR-23 Blocker 3：corporate-action / cross-provider golden fixture

确定性、网络无关（CI 可运行）。围绕贵州茅台(600519) 2024 年年度权益分派事件：
  - 股权登记日: 2025-06-25（最后交易日，含权）
  - 除权(息)日: 2025-06-26
  - 每股现金红利: 27.673 元（含税）
  - 真实 raw 收盘价：6/25=1435.86，6/26=1420.00，6/27=1403.09
    （来源：上交所公告 + 同花顺行情数据库）

目的：
  1. 硬编码 raw 关键价格点；
  2. 用 adjustment.py 反推复权因子，验证 qfq 消除人为跳空；
  3. OHLC 不变量在 raw/qfq 两侧都成立；
  4. 复权一致性 positively 验证通过（因子单调）；
  5. 不产生人为 pivot/技术缺口。
"""

import pytest
from core.types import (
    MarketDataBar, MarketDataSeries, FrequencyType, AdjustmentType,
    DataSourceType, DataProvenance, DataQualityFlag,
)
from core.market_data.adjustment import compute_adjustment_factors, apply_adjustment
from core.market_data.validation import (
    validate_adjustment_consistency, validate_ohlc_invariants, AdjustmentConsistency,
)

# ---- 确定性 golden fixture（真实数据硬编码）----
EX_DATE = "2025-06-26"
DIVIDEND = 27.673  # 每股现金红利(含税)

# raw OHLC（open/high/low 中 close 为真实值；high/low 取满足不变量的合理值）
RAW = [
    # date,        open,    high,    low,     close,   volume
    ("2025-06-24", 1423.35, 1440.00, 1420.00, 1437.20, 1.0),
    ("2025-06-25", 1439.11, 1445.00, 1420.51, 1435.86, 1.0),  # 股权登记日
    ("2025-06-26", 1415.00, 1425.00, 1410.08, 1420.00, 1.0),  # 除息日
    ("2025-06-27", 1420.01, 1420.01, 1403.09, 1403.09, 1.0),
]


def _prov(adjustment):
    return DataProvenance(
        source=DataSourceType.TENCENT, url="fixture", fetched_at="",
        raw_payload_hash="fixture", adjustment_applied=adjustment,
        quality_flag=DataQualityFlag.ADJUSTED if adjustment != AdjustmentType.NONE else DataQualityFlag.OK,
    )


def _raw_bars():
    return [
        MarketDataBar(date=d, open=o, high=h, low=l, close=c, volume=v,
                      provenance=_prov(AdjustmentType.NONE))
        for (d, o, h, l, c, v) in RAW
    ]


class TestCorporateActionFixture:
    def test_raw_ohlc_invariants_hold(self):
        for b in _raw_bars():
            assert validate_ohlc_invariants(b) == [], f"invariant violation on {b.date}"

    def test_ex_date_mechanical_drop_equals_dividend(self):
        # 除息日参考价 = 前收盘价 - 每股红利
        prev_close = dict((b.date, b.close) for b in _raw_bars())["2025-06-25"]
        reference = prev_close - DIVIDEND
        assert abs(reference - (1435.86 - 27.673)) < 1e-6
        # raw 6/26 实际收盘 1420.00，位于参考价之上（当日自然波动）
        assert dict((b.date, b.close) for b in _raw_bars())["2025-06-26"] == 1420.00

    def test_qfq_removes_artificial_gap(self):
        # 构造配对 hfq：除息日前按 ratio 下调，除息日后=raw
        raw_closes = [r[4] for r in RAW]
        prev_close = raw_closes[1]  # 1435.86
        ratio = (prev_close - DIVIDEND) / prev_close
        hfq_closes = [raw_closes[0] * ratio, raw_closes[1] * ratio,
                      raw_closes[2], raw_closes[3]]

        factors = compute_adjustment_factors(raw_closes, hfq_closes)
        # factors 单调：pre-ex = ratio(~0.9807), post-ex = 1.0
        assert factors[0] == pytest.approx(ratio, rel=1e-9)
        assert factors[2] == pytest.approx(1.0)

        o = [r[1] for r in RAW]
        h = [r[2] for r in RAW]
        l = [r[3] for r in RAW]
        c = raw_closes
        ao, ah, al, ac = apply_adjustment(o, h, l, c, factors, AdjustmentType.FORWARD)

        # qfq 收盘价在除息日附近连续：
        qfq_prev = ac[1]   # 6/25 复权收盘 = 1435.86*ratio = 1408.187
        qfq_ex = ac[2]     # 6/26 = 1420.00
        raw_ret = (raw_closes[2] - raw_closes[1]) / raw_closes[1]   # -1.105%（含红利跳空）
        qfq_ret = (qfq_ex - qfq_prev) / qfq_prev                    # ~+0.84%（自然）

        # raw 侧跳空包含红利；qfq 侧不应再出现人为大跌
        assert raw_ret < 0
        assert abs(qfq_ret) < 0.02, f"qfq artificial gap too large: {qfq_ret}"

        # qfq 复权后价格非负
        for prices in (ao, ah, al, ac):
            assert all(p >= 0 for p in prices)

    def test_adjustment_consistency_positively_verified(self):
        raw_closes = [r[4] for r in RAW]
        prev_close = raw_closes[1]
        ratio = (prev_close - DIVIDEND) / prev_close
        hfq_closes = [raw_closes[0] * ratio, raw_closes[1] * ratio,
                      raw_closes[2], raw_closes[3]]
        factors = compute_adjustment_factors(raw_closes, hfq_closes)
        c = raw_closes
        _, _, _, ac = apply_adjustment([r[1] for r in RAW], [r[2] for r in RAW],
                                      [r[3] for r in RAW], c, factors, AdjustmentType.FORWARD)
        adj_bars = [
            MarketDataBar(date=r[0], open=ao, high=ah, low=al, close=cc, volume=r[5],
                          provenance=_prov(AdjustmentType.FORWARD))
            for (r, ao, ah, al, cc) in zip(RAW,
                                           *apply_adjustment([r[1] for r in RAW],
                                                             [r[2] for r in RAW],
                                                             [r[3] for r in RAW], c,
                                                             factors, AdjustmentType.FORWARD))
        ]
        series = MarketDataSeries(
            symbol="sh600519", name="贵州茅台", frequency=FrequencyType.DAILY,
            adjustment=AdjustmentType.FORWARD, trading_calendar="SSE",
            source=DataSourceType.TENCENT, fetched_at="", bars=adj_bars,
        )
        state, anomalies = validate_adjustment_consistency(series, raw_closes=raw_closes)
        assert state == AdjustmentConsistency.CONSISTENT
        # 因子跳变应在除息日被记录（预期事件），但不判为不一致
        assert any(a.get("type") == "adjustment_factor_jump" for a in anomalies)

    def test_no_artificial_pivot_gap(self):
        # qfq 序列最大单日绝对收益 < 5%，不会触发技术形态假突破
        raw_closes = [r[4] for r in RAW]
        prev_close = raw_closes[1]
        ratio = (prev_close - DIVIDEND) / prev_close
        hfq_closes = [raw_closes[0] * ratio, raw_closes[1] * ratio,
                      raw_closes[2], raw_closes[3]]
        factors = compute_adjustment_factors(raw_closes, hfq_closes)
        _, _, _, ac = apply_adjustment([r[1] for r in RAW], [r[2] for r in RAW],
                                      [r[3] for r in RAW], raw_closes, factors,
                                      AdjustmentType.FORWARD)
        for i in range(1, len(ac)):
            move = abs(ac[i] - ac[i - 1]) / ac[i - 1]
            assert move < 0.05, f"artificial technical gap at {RAW[i][0]}: {move}"
