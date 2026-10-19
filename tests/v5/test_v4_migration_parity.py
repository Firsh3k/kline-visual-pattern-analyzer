"""
V4 → V5 迁移一致性测试

对照物：tests/v5/oracle/analyze_stock_v4.py（V4 scripts/analyze_stock.py 迁移时的冻结副本）。
被测物：core/indicators.py、core/market_regime.py、core/ma_platform_patterns.py。

覆盖方式：对每个数据集的每一个时间点（前缀长度 60..n）逐项比对，确保任何阶段分支、
任何一根K线上的数值都与 V4 完全相等（浮点 ==，不允许误差）。
最后断言 V4 的每一个阶段文字都至少被覆盖到一次，避免“没走到的分支也算通过”。
"""
import csv
import math
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent / "oracle"))

import analyze_stock_v4 as v4  # noqa: E402
from core import (  # noqa: E402
    OHLCV, ClassicIndicators, volume_status, atr_stop, ClassicMarketRegimeAnalyzer,
    MABullDetector, MABearDetector, PlatformBreakDetector, PlatformBreakdownDetector,
    PatternStage,
)
from core.ma_platform_patterns import _MA_STAGE, _PLAT_STAGE  # noqa: E402


# ------------------------------------------------------------------ 数据集

def _csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = r["date"].strip()
            if len(d) == 8:
                d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            rows.append((d, float(r["open"]), float(r["high"]), float(r["low"]),
                         float(r["close"]), float(r["volume"])))
    rows.sort()
    return rows


def _dates(n, start=(2024, 1, 1)):
    from datetime import date, timedelta
    d, out = date(*start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _synthetic():
    sets = {}
    rnd = random.Random(20261014)
    # 1) 随机游走（多种波动率）
    for vol in (0.008, 0.02, 0.04):
        p, rows = 20.0, []
        for d in _dates(260):
            o = p; c = max(0.5, o * (1 + rnd.gauss(0, vol)))
            h = max(o, c) * (1 + abs(rnd.gauss(0, vol / 2))); l = min(o, c) * (1 - abs(rnd.gauss(0, vol / 2)))
            rows.append((d, round(o, 2), round(h, 2), round(l, 2), round(c, 2), float(rnd.randint(50000, 200000))))
            p = c
        sets[f"walk_{vol}"] = rows
    # 2) 单边上涨 / 单边下跌（覆盖均线完美多头/空头、斜率阈值）
    for name, drift in (("trend_up", 0.012), ("trend_down", -0.012)):
        p, rows = 30.0, []
        for d in _dates(200):
            o = p; c = o * (1 + drift + rnd.gauss(0, 0.004))
            rows.append((d, round(o, 2), round(max(o, c) * 1.005, 2), round(min(o, c) * 0.995, 2), round(c, 2), 100000.0))
            p = c
        sets[name] = rows
    # 3) 平台整理后放量突破 / 缩量突破 / 放量破位 / 回踩 / 回抽（覆盖平台全部分支）
    def plat(name, tail, vol_tail):
        rows, p = [], 50.0
        ds = _dates(120 + len(tail))
        for i in range(120):
            c = 50.0 + math.sin(i / 3.0) * 1.5
            rows.append((ds[i], round(p, 2), round(max(p, c) + 0.3, 2), round(min(p, c) - 0.3, 2), round(c, 2), 100000.0))
            p = c
        for j, c in enumerate(tail):
            rows.append((ds[120 + j], round(p, 2), round(max(p, c) + 0.2, 2), round(min(p, c) - 0.2, 2), round(c, 2), vol_tail[j]))
            p = c
        sets[name] = rows
    plat("plat_up_vol", [51.5, 52.5, 53.5, 54.0, 54.5, 55.0], [100000.0] * 5 + [400000.0])
    plat("plat_up_novol", [51.5, 52.5, 53.5, 54.0, 54.5, 55.0], [100000.0] * 6)
    plat("plat_down_vol", [48.5, 47.5, 46.5, 46.0, 45.5, 45.0], [100000.0] * 5 + [400000.0])
    plat("plat_down_novol", [48.5, 47.5, 46.5, 46.0, 45.5, 45.0], [100000.0] * 6)
    plat("plat_near_top", [51.0, 51.3, 51.5, 51.4, 51.6, 51.7], [100000.0] * 6)
    plat("plat_near_bottom", [49.0, 48.7, 48.5, 48.6, 48.4, 48.3], [100000.0] * 6)
    plat("plat_retest_up", [53.5, 54.5, 55.0, 54.0, 53.0, 52.2], [100000.0] * 6)
    plat("plat_retest_down", [46.5, 45.5, 45.0, 46.0, 47.0, 47.8], [100000.0] * 6)
    plat("plat_run_up", [54.0, 55.0, 56.0, 57.0, 56.5, 56.8], [100000.0] * 6)
    plat("plat_run_down", [46.0, 45.0, 44.0, 43.0, 43.5, 43.2], [100000.0] * 6)
    # 4) 完全横盘（RSI avg_loss==0、KDJ h9==l9、BOLL std==0 等边界）
    sets["flat"] = [(d, 10.0, 10.0, 10.0, 10.0, 1000.0) for d in _dates(120)]
    return sets


def _datasets():
    sets = {}
    for p in sorted((ROOT / "data").glob("*.csv")) + sorted((ROOT / "data" / "v5_validation").glob("*.csv")):
        sets[f"repo:{p.parent.name}/{p.stem}"] = _csv(p)
    for p in sorted((Path(__file__).resolve().parent / "fixtures").glob("*_2026101*.csv")):
        sets[f"live:{p.stem}"] = _csv(p)
    sets.update({f"syn:{k}": v for k, v in _synthetic().items()})
    return sets


DATASETS = _datasets()


def _v4_data(rows):
    return [{"day": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in rows]


def _v5_klines(rows):
    return [OHLCV(date=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5]) for r in rows]


def _prefixes(rows, start=60):
    return range(min(start, len(rows)), len(rows) + 1)


SEEN_STAGES = set()
SEEN_RETEST = set()    # FIR-35：(方向, V4 当时给出的阶段)
SEEN_BREAKOUT = set()  # FIR-32 D3：平台“当日首次突破 / 突破后 / 未突破”三种情况都必须被覆盖


# ------------------------------------------------------------------ 指标

@pytest.mark.parametrize("name", sorted(DATASETS))
def test_indicators_identical_every_bar(name):
    rows = DATASETS[name]
    a = v4.calc_indicators(_v4_data(rows))
    b = ClassicIndicators().calculate(_v5_klines(rows))
    assert set(a) == set(b)
    for key in a:
        assert a[key] == b[key], f"{name} {key}"


@pytest.mark.parametrize("name", sorted(DATASETS))
def test_volume_and_atr_identical_every_prefix(name):
    rows = DATASETS[name]
    for n in _prefixes(rows, 20):
        d, k = _v4_data(rows[:n]), _v5_klines(rows[:n])
        ia, ib = v4.calc_indicators(d), ClassicIndicators().calculate(k)
        assert v4.analyze_volume(d, ia) == volume_status(k, ib), (name, n)
        assert v4.calc_atr_stop_loss(d, ia) == atr_stop(k, ib), (name, n)


@pytest.mark.parametrize("name", sorted(DATASETS))
def test_market_regime_identical_every_prefix(name):
    rows = DATASETS[name]
    an = ClassicMarketRegimeAnalyzer()
    for n in _prefixes(rows, 50):
        a = v4.calc_market_env_from_data(_v4_data(rows[:n]))
        b = an.analyze_dict(_v5_klines(rows[:n]))
        assert a == b, (name, n)
        r = an.analyze(_v5_klines(rows[:n]))
        if a is None:
            assert r is None
        else:
            assert (r.score, r.stage, r.trend.value, r.advice, r.impact_on_stock) == \
                   (a["score"], a["stage"], a["trend"], a["advice"], a["impact"])


# ------------------------------------------------------------------ 形态

def _check_ma(name, n, v4d, results, bull):
    exists = v4d["stage"] in _MA_STAGE
    SEEN_STAGES.add(v4d["stage"])
    if not exists:
        assert results == [], (name, n, v4d["stage"])
        return
    assert len(results) == 1, (name, n)
    r = results[0]
    assert r.score == v4d["score"], (name, n)
    assert r.notes == v4d["stage"], (name, n)
    assert r.stage == _MA_STAGE[v4d["stage"]]
    assert r.direction.value == ("bull" if bull else "bear")
    assert [(k.name, k.price) for k in r.key_points] == [(p["name"], p["price"]) for p in v4d["key_points"]]
    assert all(k.index == n - 1 for k in r.key_points)
    # FIR-32 D1 / D3
    assert r.category == "trend_state"
    ev = r.evidence
    assert ev["v4_stage"] == v4d["stage"]
    flags = (ev["aligned_ma5_ma10"], ev["aligned_ma10_ma20"], ev["aligned_ma20_ma60"],
             ev["close_beyond_ma5"], ev["ma5_slope_ok"])
    assert v4d["score"] == sum(w for w, f in zip((25, 25, 20, 15, 15), flags) if f), (name, n)
    assert ev["fully_aligned"] == (flags[0] and flags[1] and flags[2])


def _check_plat(name, n, v4d, results, up, klines):
    if not v4d["stage"]:
        assert results == []
        return
    SEEN_STAGES.add(v4d["stage"])
    if v4d["stage"] == "非平台整理：振幅过大":
        assert results == [], (name, n)
        return
    assert len(results) == 1, (name, n)
    r = results[0]
    kp = {p["name"]: p["price"] for p in v4d["key_points"]}
    mine = {k.name: k for k in r.key_points}
    assert r.score == v4d["score"], (name, n)
    # FIR-32 D3：独立计算“当日首次越过突破线”，据此确定期望阶段
    level = mine["平台上沿"].price if up else mine["平台下沿"].price
    line = level * 1.01 if up else level * 0.99
    c_now, c_prev = klines[-1].close, klines[-2].close
    beyond = (lambda c: c > line) if up else (lambda c: c < line)
    broke_today = beyond(c_now) and not beyond(c_prev)
    # FIR-35（有意偏离 V4）：独立计算“回踩/回抽”——此前 9 日曾收盘越过 2%、今日回到 ±3% 区间、今日非首次越过
    far = (lambda c: c > level * 1.02) if up else (lambda c: c < level * 0.98)
    retest = (any(far(k.close) for k in klines[-10:-1])
              and level * 0.97 < c_now < level * 1.03 and not broke_today)
    retest_text = "突破后回踩平台上沿确认中" if up else "破位后回抽平台下沿确认中"
    if retest:
        assert r.notes.split("｜")[0] == retest_text, (name, n)
        expected_stage = PatternStage.RETEST
        SEEN_RETEST.add(("up" if up else "down", v4d["stage"]))
    else:
        assert r.notes.split("｜")[0] == v4d["stage"], (name, n)
        expected_stage = PatternStage.BREAKOUT_DAY if broke_today else _PLAT_STAGE[v4d["stage"]]
    assert r.stage == expected_stage, (name, n)
    assert r.category == "price_pattern"
    ev = r.evidence
    assert ev["v4_stage"] == v4d["stage"]   # V4 原判定顺序的结论始终可追溯
    assert ev["retest"] == retest, (name, n)
    assert ev["broke"] == beyond(c_now) and ev["broke_today"] == broke_today
    if ev["broke"]:
        assert ev["volume_confirmed"] == ("放量" in v4d["stage"]), (name, n)
    SEEN_BREAKOUT.add("today" if broke_today else ("later" if ev["broke"] else "none"))
    assert round(mine["平台上沿"].price, 2) == kp["平台上沿"]
    assert round(mine["平台下沿"].price, 2) == kp["平台下沿"]
    assert round(mine["当前价"].price, 2) == kp["当前价"]
    assert f"振幅 {kp['平台振幅']}" in r.notes
    # 新增的日期信息必须指向真实K线，且价格就是那根K线的最高/最低价
    assert klines[mine["平台上沿"].index].high == mine["平台上沿"].price
    assert klines[mine["平台上沿"].index].date == mine["平台上沿"].date
    assert klines[mine["平台下沿"].index].low == mine["平台下沿"].price
    assert klines[mine["平台下沿"].index].date == mine["平台下沿"].date
    assert r.neckline_price == (mine["平台上沿"].price if up else mine["平台下沿"].price)


@pytest.mark.parametrize("name", sorted(DATASETS))
def test_patterns_identical_every_prefix(name):
    rows = DATASETS[name]
    dets = (MABullDetector(), MABearDetector(), PlatformBreakDetector(), PlatformBreakdownDetector())
    for n in _prefixes(rows):
        d, k = _v4_data(rows[:n]), _v5_klines(rows[:n])
        ind = v4.calc_indicators(d)
        bull, bear = v4.match_patterns(d, ind), v4.match_bear_patterns(d, ind)
        _check_ma(name, n, bull["ma_bull"], dets[0].detect(k), True)
        _check_ma(name, n, bear["ma_bear"], dets[1].detect(k), False)
        _check_plat(name, n, bull["platform_break"], dets[2].detect(k), True, k)
        _check_plat(name, n, bear["platform_break_down"], dets[3].detect(k), False, k)


# V4 中永远走不到的分支（V4 逻辑缺陷；FIR-35 已在 V5 中调整顺序使其可达，evidence.v4_stage 仍记录 V4 结论）：
# 平台突破：“回踩”要求 0.97×上沿 < 现价 < 1.03×上沿；但现价 > 1.01×上沿 时先命中“突破”，
#           现价 ≤ 1.01×上沿 时先命中“突破前夜”（0.97×上沿 < 现价 且未突破）。两者覆盖了整个回踩区间。
# 平台破位：对称地被“跌破”（< 0.99×下沿）与“破位前夜”（< 1.03×下沿 且未跌破）完全覆盖。
V4_UNREACHABLE = {"突破后回踩平台上沿确认中", "破位后回抽平台下沿确认中"}


def test_every_v4_stage_branch_was_exercised():
    """必须在其它测试之后运行（pytest 默认按定义顺序）：每个可达的 V4 阶段文字都至少出现过一次"""
    expected = set(_MA_STAGE) | set(_PLAT_STAGE) | {
        "均线未形成多头排列", "均线未形成空头排列", "非平台整理：振幅过大"}
    missing = expected - V4_UNREACHABLE - SEEN_STAGES
    assert not missing, f"以下 V4 分支没有被任何数据覆盖：{sorted(missing)}"
    assert SEEN_BREAKOUT == {"today", "later", "none"}, f"平台突破时点覆盖不全：{SEEN_BREAKOUT}"
    # V4 自身的顺序仍使其不可达（V4 冻结副本不改）
    assert not (V4_UNREACHABLE & SEEN_STAGES)
    # FIR-35：V5 中回踩 / 回抽必须真实出现，且覆盖 V4 原先吞掉它的两种分支（突破前夜 / 已突破）
    dirs = {d for d, _ in SEEN_RETEST}
    assert dirs == {"up", "down"}, f"回踩/回抽未被覆盖：{SEEN_RETEST}"
    v4_hidden = {s for _, s in SEEN_RETEST}
    assert any("前夜" in s for s in v4_hidden) and any("平台：" in s and "前夜" not in s for s in v4_hidden), \
        f"回踩覆盖不全（应同时覆盖 V4 判为“前夜”与“已突破/跌破”的情形）：{sorted(v4_hidden)}"


def test_unreachable_branches_are_truly_unreachable():
    """对平台判定做价格网格穷举：任何“回踩/回抽”条件成立时，前面的分支必定先命中"""
    for h in (10.0, 51.8, 123.45):
        for i in range(-500, 501):
            cur = h * (1 + i / 10000)
            broke_up, near_up = cur > h * 1.01, cur > h * 0.97 and not cur > h * 1.01
            if h * 0.97 < cur < h * 1.03:
                assert broke_up or near_up
            low = h
            broke_dn, near_dn = cur < low * 0.99, cur < low * 1.03 and not cur < low * 0.99
            if low * 0.97 < cur < low * 1.03:
                assert broke_dn or near_dn
