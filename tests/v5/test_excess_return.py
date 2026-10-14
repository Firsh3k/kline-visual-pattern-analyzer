"""
形态超额收益验证 — 单元测试

覆盖：交易成本、价格重建（插值/冲突）、B1/B2 基准、样本外切分与 purge、聚类自助法。
"""

import pytest

from core.calibration.excess_return import (
    CostModel,
    PricePanel,
    TradeRecord,
    a_share_trading_days,
    build_trades,
    cluster_bootstrap_ci,
    same_day_pool_return,
    same_stock_random_day_return,
    split_period,
    summarize,
)

DAYS = [f"2025-01-{d:02d}" for d in range(1, 31)]


def _panel(series):
    """series: {code: [close per DAYS index]}"""
    closes = {c: {DAYS[i]: p for i, p in enumerate(ps)} for c, ps in series.items()}
    bars = {c: DAYS[:len(ps)] for c, ps in series.items()}
    return PricePanel(closes, bars, source="test")


class TestCostModel:
    def test_round_trip_default(self):
        assert CostModel().round_trip == pytest.approx(0.00302)

    def test_net_return_zero_cost(self):
        c = CostModel(0, 0, 0, 0)
        assert c.net_return(0.05) == pytest.approx(0.05)

    def test_net_return_below_gross(self):
        c = CostModel()
        assert c.net_return(0.0) == pytest.approx(-c.round_trip, abs=1e-5)
        assert c.net_return(0.10) < 0.10


class TestPricePanel:
    def test_forward_return_uses_bar_offset(self):
        p = _panel({"A": [10, 11, 12, 13]})
        assert p.forward_return("A", DAYS[0], 2) == pytest.approx(0.2)
        assert p.forward_return("A", DAYS[2], 2) is None  # 数据不足
        assert p.exit_date("A", DAYS[0], 3) == DAYS[3]

    def test_reconstruct_interpolates_short_gaps_and_drops_conflicts(self):
        ds = {
            "signals": [
                {"signal_id": "s1", "stock_code": "A", "signal_date": DAYS[0], "entry_price": 10.0},
                {"signal_id": "s2", "stock_code": "A", "signal_date": DAYS[3], "entry_price": 13.0},
                {"signal_id": "s3", "stock_code": "A", "signal_date": DAYS[10], "entry_price": 20.0},
            ],
            "backtests": [
                # s1 T+2 → DAYS[2] = 12；s2 T+7 → DAYS[10] = 20.5（与 s3 的 20 冲突 > 0.5%）
                {"signal_id": "s1", "horizon": 2, "sufficient_data": True, "final_price": 12.0},
                {"signal_id": "s2", "horizon": 7, "sufficient_data": True, "final_price": 20.5},
            ],
        }
        panel, stats = PricePanel.reconstruct(ds, DAYS, max_gap=3)
        c = panel.closes["A"]
        assert c[DAYS[0]] == 10.0 and c[DAYS[2]] == 12.0 and c[DAYS[3]] == 13.0
        assert DAYS[1] in c and 10.0 < c[DAYS[1]] < 12.0   # 1 天缺口被插值
        assert DAYS[1] in panel.interpolated["A"]
        assert DAYS[10] not in c                             # 冲突点丢弃
        assert stats["conflicts"] == 1

    def test_reconstruct_leaves_long_gaps_empty(self):
        ds = {"signals": [
            {"signal_id": "a", "stock_code": "A", "signal_date": DAYS[0], "entry_price": 10.0},
            {"signal_id": "b", "stock_code": "A", "signal_date": DAYS[8], "entry_price": 12.0},
        ], "backtests": []}
        panel, _ = PricePanel.reconstruct(ds, DAYS, max_gap=3)
        assert DAYS[4] not in panel.closes["A"]

    def test_trading_days_skip_weekend_and_holiday(self):
        days = a_share_trading_days("2025-09-29", "2025-10-10")
        assert "2025-10-01" not in days and "2025-10-04" not in days
        assert days[0] == "2025-09-29" and "2025-10-09" in days


class TestBaselines:
    def test_same_day_pool_excludes_signal_stock(self):
        series = {f"S{i}": [10, 11] for i in range(10)}  # 其余股票 +10%
        series["X"] = [10, 20]
        p = _panel(series)
        assert same_day_pool_return(p, DAYS[0], 1, exclude="X") == pytest.approx(0.1)

    def test_same_day_pool_requires_breadth(self):
        p = _panel({f"S{i}": [10, 11] for i in range(5)})
        assert same_day_pool_return(p, DAYS[0], 1) is None

    def test_same_stock_random_day_mean(self):
        p = _panel({"A": [10, 11, 11, 11, 11, 11, 11]})
        r = same_stock_random_day_return(p, "A", DAYS[:6], 1)
        assert r == pytest.approx(0.1 / 6)


class TestSplit:
    def test_split_period(self):
        assert split_period("2025-10-02", "2025-11-01", "2025-10-01") == "out_of_sample"
        assert split_period("2025-08-01", "2025-09-01", "2025-10-01") == "in_sample"
        assert split_period("2025-09-20", "2025-10-20", "2025-10-01") is None  # purge
        assert split_period("2025-09-20", None, None) == "all"

    def test_build_trades_merges_same_stock_same_day_and_purges(self):
        series = {f"S{i}": [10 + j for j in range(30)] for i in range(12)}
        panel = _panel(series)
        sigs, bts = [], []
        for sid, code, d, pt in [("a", "S0", DAYS[2], "w_bottom"), ("b", "S0", DAYS[2], "hs_bottom"),
                                 ("c", "S1", DAYS[18], "w_bottom"), ("d", "S2", DAYS[24], "w_bottom")]:
            sigs.append({"signal_id": sid, "stock_code": code, "signal_date": d,
                         "pattern_type": pt, "market_regime": "range"})
            bts.append({"signal_id": sid, "horizon": 3, "sufficient_data": True, "forward_return": 0.05})
        ds = {"signals": sigs, "backtests": bts}
        trades, st = build_trades(ds, panel, ("w_bottom", "hs_bottom"), "range", 3, DAYS[20])
        assert st["signals_merged"] == 3
        assert st["purged"] == 1                     # S1 在 DAYS[18] 持有到 DAYS[21]，跨过切分日
        periods = {t.stock_code: t.period for t in trades}
        assert periods == {"S0": "in_sample", "S2": "out_of_sample"}
        s0 = next(t for t in trades if t.stock_code == "S0")
        assert s0.pattern_types == ["hs_bottom", "w_bottom"]
        assert s0.b1_same_day_pool == pytest.approx(15 / 12 - 1)  # 其余股票 DAYS[2]→DAYS[5]: 12→15


class TestSummary:
    def test_cluster_bootstrap_ci_contains_mean_and_is_deterministic(self):
        vals = [0.01, 0.02, -0.01, 0.03, 0.0, 0.02]
        cl = ["2025-01", "2025-01", "2025-02", "2025-03", "2025-04", "2025-04"]
        ci1 = cluster_bootstrap_ci(vals, cl)
        ci2 = cluster_bootstrap_ci(vals, cl)
        assert ci1 == ci2
        assert ci1[0] <= sum(vals) / len(vals) <= ci1[1]

    def test_cluster_bootstrap_needs_clusters(self):
        assert cluster_bootstrap_ci([0.1, 0.2], ["m", "m"]) is None

    def test_summarize_excess(self):
        trades = [TradeRecord("A", f"2025-0{m}-01", ["w_bottom"], 20, "all", 0.05, 0.02, 0.01)
                  for m in range(1, 5)]
        st = summarize(trades, CostModel(0, 0, 0, 0))
        assert st.excess_b1_net == pytest.approx(0.03)
        assert st.excess_b2 == pytest.approx(0.04)
        assert st.beat_b1_rate == 1.0
        assert st.n_clusters == 4
