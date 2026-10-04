#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V5 FIR-12 Pattern Migration — Apples-to-Apples Validation (V4-JS snapshot vs V5).

用法:
    cd <repo_root>
    python3 scripts/v5_validation.py

输入:
    data/v5_validation/{code}.csv  (date,open,high,low,close,volume  date=YYYY-MM-DD)

输出:
    1) 终端打印对比摘要
    2) data/v5_validation/results.json —— 供 docs/v5_pattern_migration_validation.md 使用

设计要点（针对 PR #36 Blocker 4 的修正）:
- V4 端不再用 pattern_scanner.detect_double_bottom 的"逐日历史触发"口径（与 V5 末端快照
  不可比），而是忠实移植 kline_analyzer.html 中的 matchPatterns()/matchBearPatterns()
  四个形态检测函数（W底/M头/头肩底/头肩顶）。两端都是对**同一根末端K线**做快照分析，
  语义一致，可直接逐股逐形态对比。
- 不修改 kline_analyzer.html / pattern_scanner.py（仅只读移植其 JS 逻辑到本脚本）。
- 指标命名诚实：没有人工标注的 false-positive 真值，因此不报告 "false positive reduction"，
  而报告 structural recall / coverage / neckline agreement / breakout-state agreement。
"""

import os
import sys
import json
from collections import Counter, defaultdict

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# V5 (新实现) —— import 触发 @registry.pattern 注册
from core.types import OHLCV  # noqa: E402
import core.pivot_patterns  # noqa: E401,F401
from core.pivot_patterns import (  # noqa: E402
    WBottomDetector,
    MTopDetector,
    HSBottomDetector,
    HSTopDetector,
)

DATA_DIR = os.path.join(ROOT, "data", "v5_validation")

# 验证标的: (code, name, industry, setcode)
STOCKS = [
    ("600519", "贵州茅台", "白酒/消费", 1),
    ("002594", "比亚迪", "新能源汽车", 0),
    ("300750", "宁德时代", "新能源电池", 0),
    ("601318", "中国平安", "金融保险", 1),
    ("000858", "五粮液", "白酒/消费", 0),
    ("002415", "海康威视", "安防科技", 0),
]

PATTERN_IDS = ["w_bottom", "m_top", "hs_bottom", "hs_top"]
NECKLINE_TOL = 0.02  # ±2% 颈线价匹配容差


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def load_klines(code: str):
    path = os.path.join(DATA_DIR, f"{code}.csv")
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d")
    df = df.sort_values("date").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    klines = [OHLCV(
        date=r["date"].strftime("%Y-%m-%d"),
        open=float(r["open"]), high=float(r["high"]),
        low=float(r["low"]), close=float(r["close"]),
        volume=float(r["volume"]),
    ) for _, r in df.iterrows()]
    return klines, df


# ---------------------------------------------------------------------------
# V4 JS 忠实移植 —— 与 kline_analyzer.html matchPatterns()/matchBearPatterns() 等价
# ---------------------------------------------------------------------------
def _sma(vals, i, w):
    if i + 1 < w:
        return None
    return sum(vals[i - w + 1:i + 1]) / w


def v4js_detect(klines):
    """返回 {pattern_id: {present, neckline, broken, stage, score, ...}}"""
    n = len(klines)
    last = n - 1
    lookback = min(80, n - 1)
    off = n - lookback  # seg 局部索引 -> 全局索引偏移

    seg = klines[off:]
    highs = [k.high for k in seg]
    lows = [k.low for k in seg]
    closes = [k.close for k in klines]  # 全局
    vols = [k.volume for k in klines]   # 全局
    cur_price = closes[last]

    vma5 = _sma(vols, last, 5)
    vma20 = _sma(vols, last, 20)
    vol_confirm = bool(vma5 and vma20 and vma5 > vma20 * 1.1)

    out = {pid: {"present": False, "neckline": None, "broken": None,
                 "stage": "absent", "score": 0} for pid in PATTERN_IDS}

    # ===== W底（matchPatterns #2）=====
    min_low = min(lows)
    min_idx = lows.index(min_low)
    if min_idx > 5 and min_idx < lookback - 8:
        left_lows_arr = lows[0:min_idx - 2]
        left_low = min(left_lows_arr) if left_lows_arr else min_low
        right_lows = lows[min_idx + 3:]
        right_low = min(right_lows) if right_lows else min_low
        neckline = max(highs[0:min_idx])
        diff_pct = abs(right_low - min_low) / min_low * 100
        second_higher = right_low >= min_low * 0.97 and diff_pct <= 5.0
        break_neck = cur_price > neckline
        score = (40 if second_higher else 10) + (35 if break_neck else 15) + (25 if vol_confirm else 10)
        out["w_bottom"] = {
            "present": second_higher,
            "neckline": neckline, "broken": break_neck,
            "stage": ("invalid" if not second_higher else
                      ("broken" if break_neck else "forming")),
            "score": score, "left_low": left_low, "min_low": min_low, "right_low": right_low,
        }

    # ===== 头肩底（matchPatterns #3）=====
    if lookback > 30:
        head_idx = lows.index(min(lows))
        if head_idx > 8 and head_idx < lookback - 8:
            left_part = lows[0:head_idx - 3]
            right_part = lows[head_idx + 3:]
            left_shoulder = min(left_part) if left_part else None
            left_shoulder_idx = left_part.index(left_shoulder) if left_part else -1
            right_shoulder = min(right_part) if right_part else None
            right_shoulder_idx = right_part.index(right_shoulder) if right_part else -1
            head = lows[head_idx]

            ls_idx = left_shoulder_idx if left_shoulder_idx >= 0 else 0
            nl_left_idx = ls_idx + highs[ls_idx:head_idx].index(max(highs[ls_idx:head_idx]))
            nl_left_price = highs[nl_left_idx]
            right_shoulder_local = head_idx + 3 + right_shoulder_idx
            nl_right_idx = head_idx + 3
            if right_shoulder_local > head_idx + 3:
                seg_h = highs[head_idx + 3:right_shoulder_local + 1]
                nl_right_idx = head_idx + 3 + seg_h.index(max(seg_h))
            nl_right_price = highs[min(nl_right_idx, lookback - 1)]

            nl_left_global = off + nl_left_idx
            nl_right_global = off + nl_right_idx
            neck_slope = (nl_right_price - nl_left_price) / (nl_right_global - nl_left_global) \
                if nl_right_global > nl_left_global else 0.0
            neckline_now = nl_left_price + neck_slope * (last - nl_left_global)

            left_higher = bool(left_shoulder and left_shoulder > head)
            right_higher = bool(right_shoulder and right_shoulder > head)
            symmetry = bool(left_shoulder and right_shoulder and
                            abs(left_shoulder - right_shoulder) / left_shoulder < 0.2)
            broken = cur_price > neckline_now * 0.98

            bo_event = False
            for i in range(max(1, last - 10), last + 1):
                prev_nl = nl_left_price + neck_slope * (i - 1 - nl_left_global)
                cur_nl = nl_left_price + neck_slope * (i - nl_left_global)
                if closes[i - 1] <= prev_nl and closes[i] > cur_nl:
                    bo_event = True
                    break

            present = left_higher and right_higher and symmetry
            score = (20 if left_higher else 5) + (20 if right_higher else 5) + \
                    (20 if symmetry else 5) + (40 if broken else 10)
            stage = "broken" if (present and broken and bo_event) else ("forming" if present else "invalid")
            out["hs_bottom"] = {
                "present": present, "neckline": neckline_now, "broken": broken,
                "stage": stage, "score": score,
                "head": head, "left_shoulder": left_shoulder, "right_shoulder": right_shoulder,
            }

    # ===== M头（matchBearPatterns #2）=====
    max_high = max(highs)
    max_idx = highs.index(max_high)
    if max_idx > 5 and max_idx < lookback - 8:
        left_highs = highs[0:max_idx - 2]
        left_high = max(left_highs) if left_highs else max_high
        right_highs = highs[max_idx + 3:]
        right_high = max(right_highs) if right_highs else max_high
        nl_lo = max(0, max_idx - 10)
        neckline = min(lows[nl_lo:max_idx + 3])
        second_lower = right_high <= max_high * 1.03
        break_neck = cur_price < neckline
        score = (40 if second_lower else 10) + (35 if break_neck else 15) + (25 if vol_confirm else 10)
        out["m_top"] = {
            "present": second_lower,
            "neckline": neckline, "broken": break_neck,
            "stage": ("invalid" if not second_lower else
                      ("broken" if break_neck else "forming")),
            "score": score, "left_high": left_high, "max_high": max_high, "right_high": right_high,
        }

    # ===== 头肩顶（matchBearPatterns #3）=====
    if lookback > 30:
        head_idx = highs.index(max(highs))
        if head_idx > 8 and head_idx < lookback - 8:
            left_part = highs[0:head_idx - 3]
            right_part = highs[head_idx + 3:]
            left_shoulder = max(left_part) if left_part else None
            left_shoulder_idx = left_part.index(left_shoulder) if left_part else -1
            right_shoulder = max(right_part) if right_part else None
            right_shoulder_idx = right_part.index(right_shoulder) if right_part else -1
            head = highs[head_idx]

            ls_idx = left_shoulder_idx if left_shoulder_idx >= 0 else 0
            nl_left_idx = ls_idx + lows[ls_idx:head_idx].index(min(lows[ls_idx:head_idx]))
            nl_left_price = lows[nl_left_idx]
            right_shoulder_local = head_idx + 3 + right_shoulder_idx
            nl_right_idx = head_idx + 3
            if right_shoulder_local > head_idx + 3:
                seg_l = lows[head_idx + 3:right_shoulder_local + 1]
                nl_right_idx = head_idx + 3 + seg_l.index(min(seg_l))
            nl_right_price = lows[min(nl_right_idx, lookback - 1)]

            nl_left_global = off + nl_left_idx
            nl_right_global = off + nl_right_idx
            neck_slope = (nl_right_price - nl_left_price) / (nl_right_global - nl_left_global) \
                if nl_right_global > nl_left_global else 0.0
            neckline_now = nl_left_price + neck_slope * (last - nl_left_global)

            left_lower = bool(left_shoulder and left_shoulder < head)
            right_lower = bool(right_shoulder and right_shoulder < head)
            symmetry = bool(left_shoulder and right_shoulder and
                            abs(left_shoulder - right_shoulder) / left_shoulder < 0.2)
            broken = cur_price < neckline_now * 1.02

            bd_event = False
            for i in range(max(1, last - 10), last + 1):
                prev_nl = nl_left_price + neck_slope * (i - 1 - nl_left_global)
                cur_nl = nl_left_price + neck_slope * (i - nl_left_global)
                if closes[i - 1] >= prev_nl and closes[i] < cur_nl:
                    bd_event = True
                    break

            present = left_lower and right_lower and symmetry
            score = (20 if left_lower else 5) + (20 if right_lower else 5) + \
                    (20 if symmetry else 5) + (40 if broken else 10)
            stage = "broken" if (present and broken and bd_event) else ("forming" if present else "invalid")
            out["hs_top"] = {
                "present": present, "neckline": neckline_now, "broken": broken,
                "stage": stage, "score": score,
                "head": head, "left_shoulder": left_shoulder, "right_shoulder": right_shoulder,
            }

    return out


# ---------------------------------------------------------------------------
# V5 结果辅助
# ---------------------------------------------------------------------------
V5_DETECTORS = {
    "w_bottom": WBottomDetector(),
    "m_top": MTopDetector(),
    "hs_bottom": HSBottomDetector(),
    "hs_top": HSTopDetector(),
}
BROKEN_STAGES = {"breakout_day", "post_break", "retest", "complete"}


def v5_detect(klines):
    return {pid: det.detect(klines) for pid, det in V5_DETECTORS.items()}


def best_v5_neckline(v5_results, target_neckline):
    """在 V5 形态列表中找颈line最接近 target 的那个（用于两端颈线价对比）。"""
    if not v5_results or target_neckline is None:
        return None
    return min(v5_results, key=lambda r: abs((r.neckline_price or 0) - target_neckline))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run():
    per_stock = []
    outcome = defaultdict(Counter)
    neckline_agree = Counter()
    neckline_total = Counter()
    breakout_agree = Counter()
    breakout_total = Counter()
    v5_counts = Counter()
    v4_present_counts = Counter()

    for code, name, industry, setcode in STOCKS:
        csv_path = os.path.join(DATA_DIR, f"{code}.csv")
        if not os.path.exists(csv_path):
            per_stock.append({"code": code, "name": name, "skipped": True})
            continue

        klines, df = load_klines(code)
        n = len(klines)

        v4 = v4js_detect(klines)
        v5 = v5_detect(klines)

        stock_row = {"code": code, "name": name, "industry": industry,
                     "bars": n, "first_date": klines[0].date, "last_date": klines[-1].date,
                     "patterns": {}}

        for pid in PATTERN_IDS:
            v4p = v4[pid]["present"]
            v5list = v5[pid]
            v5p = len(v5list) > 0
            v5_counts[pid] += len(v5list)
            if v4p:
                v4_present_counts[pid] += 1

            if v4p and v5p:
                oc = "both"
            elif v4p and not v5p:
                oc = "v4_only"
            elif (not v4p) and v5p:
                oc = "v5_only"
            else:
                oc = "neither"
            outcome[pid][oc] += 1

            nl_agree = None
            bo_agree = None
            matched_v5 = best_v5_neckline(v5list, v4[pid]["neckline"])
            if oc == "both" and matched_v5 is not None and v4[pid]["neckline"]:
                neckline_total[pid] += 1
                v5_nl = matched_v5.neckline_price
                if v5_nl and abs(v5_nl - v4[pid]["neckline"]) / v4[pid]["neckline"] <= NECKLINE_TOL:
                    neckline_agree[pid] += 1
                    nl_agree = True
                else:
                    nl_agree = False
                breakout_total[pid] += 1
                v5_broken = matched_v5.stage.value in BROKEN_STAGES
                if v5_broken == bool(v4[pid]["broken"]):
                    breakout_agree[pid] += 1
                    bo_agree = True
                else:
                    bo_agree = False

            stock_row["patterns"][pid] = {
                "v4_present": v4p, "v4_stage": v4[pid]["stage"],
                "v4_neckline": v4[pid]["neckline"], "v4_broken": v4[pid]["broken"],
                "v4_score": v4[pid]["score"],
                "v5_count": len(v5list),
                "v5_stages": [r.stage.value for r in v5list],
                "v5_necklines": [r.neckline_price for r in v5list],
                "outcome": oc,
                "neckline_agree": nl_agree,
                "breakout_agree": bo_agree,
            }

        per_stock.append(stock_row)

    totals = {}
    agg = Counter()
    for pid in PATTERN_IDS:
        o = outcome[pid]
        both = o["both"]
        v4_only = o["v4_only"]
        v5_only = o["v5_only"]
        v4_detected = both + v4_only
        v5_detected = both + v5_only
        recall = (both / v4_detected) if v4_detected else None
        coverage = (both / v5_detected) if v5_detected else None
        nl_n = neckline_total[pid]
        nl_a = neckline_agree[pid]
        bo_n = breakout_total[pid]
        bo_a = breakout_agree[pid]
        totals[pid] = {
            "n_stocks": len(per_stock),
            "v4_detected": v4_detected, "v5_detected": v5_detected,
            "both": both, "v4_only": v4_only, "v5_only": v5_only,
            "structural_recall": recall, "structural_recall_n": both,
            "structural_recall_denom": v4_detected,
            "v5_coverage": coverage, "v5_coverage_n": both,
            "v5_coverage_denom": v5_detected,
            "neckline_agree": nl_a, "neckline_total": nl_n,
            "neckline_agree_rate": (nl_a / nl_n) if nl_n else None,
            "breakout_agree": bo_a, "breakout_total": bo_n,
            "breakout_agree_rate": (bo_a / bo_n) if bo_n else None,
        }
        agg["both"] += both; agg["v4_only"] += v4_only; agg["v5_only"] += v5_only

    total_recall_denom = agg["both"] + agg["v4_only"]
    total_cov_denom = agg["both"] + agg["v5_only"]
    summary = {
        "per_stock": per_stock,
        "per_pattern": totals,
        "aggregate": {
            "both": agg["both"], "v4_only": agg["v4_only"], "v5_only": agg["v5_only"],
            "structural_recall": (agg["both"] / total_recall_denom) if total_recall_denom else None,
            "structural_recall_n": agg["both"], "structural_recall_denom": total_recall_denom,
            "v5_coverage": (agg["both"] / total_cov_denom) if total_cov_denom else None,
            "v5_coverage_n": agg["both"], "v5_coverage_denom": total_cov_denom,
            "n_stocks": len(per_stock),
            "n_pattern_types": len(PATTERN_IDS),
        },
        "note": (
            "无人工标注 false-positive 真值，故不报告 false-positive reduction。"
            "structural_recall = V4-JS检出样本中V5也检出的比例；"
            "v5_coverage = V5检出样本中V4-JS也检出的比例（V5为pivot结构严格检测，可能更保守）。"
        ),
    }

    out_path = os.path.join(DATA_DIR, "results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("=" * 88)
    print("V5 FIR-12 Apples-to-Apples: V4-JS snapshot vs V5-pivot detectors")
    print("=" * 88)
    hdr = f"{'code':<8}{'name':<9}{'bars':>5}"
    for pid in PATTERN_IDS:
        hdr += f" {pid:>14}"
    print(hdr)
    for s in per_stock:
        if s.get("skipped"):
            continue
        row = f"{s['code']:<8}{s['name']:<9}{s['bars']:>5}"
        for pid in PATTERN_IDS:
            oc = s["patterns"][pid]["outcome"]
            mark = {"both": "BOTH", "v4_only": "V4 ", "v5_only": "V5 ", "neither": "-- "}[oc]
            row += f" {mark:>14}"
        print(row)

    print("\n--- Per-pattern summary ---")
    print(f"{'pattern':<12}{'V4det':>6}{'V5det':>6}{'both':>6}{'v4only':>7}{'v5only':>7}"
          f"{'recall':>9}{'cov':>9}{'nlAgree':>9}{'boAgree':>9}")
    for pid in PATTERN_IDS:
        t = totals[pid]
        rec = "N/A" if t["structural_recall"] is None else f"{t['structural_recall']*100:.0f}%"
        cov = "N/A" if t["v5_coverage"] is None else f"{t['v5_coverage']*100:.0f}%"
        nl = "N/A" if not t["neckline_total"] else f"{t['neckline_agree']}/{t['neckline_total']}"
        bo = "N/A" if not t["breakout_total"] else f"{t['breakout_agree']}/{t['breakout_total']}"
        print(f"{pid:<12}{t['v4_detected']:>6}{t['v5_detected']:>6}{t['both']:>6}"
              f"{t['v4_only']:>7}{t['v5_only']:>7}{rec:>9}{cov:>9}{nl:>9}{bo:>9}")

    a = summary["aggregate"]
    recall_str = "N/A" if a["structural_recall"] is None else f"{a['structural_recall']*100:.1f}%"
    cov_str = "N/A" if a["v5_coverage"] is None else f"{a['v5_coverage']*100:.1f}%"
    print("\n--- Aggregate ---")
    print(f"stocks x pattern-cells: {a['n_stocks']} stocks x {a['n_pattern_types']} types "
          f"= {a['n_stocks']*a['n_pattern_types']}")
    print(f"both={a['both']}  v4_only={a['v4_only']}  v5_only={a['v5_only']}")
    print(f"structural recall (V4->V5): {a['structural_recall_n']}/{a['structural_recall_denom']}"
          f" = {recall_str}")
    print(f"V5 coverage (V5->V4):       {a['v5_coverage_n']}/{a['v5_coverage_denom']}"
          f" = {cov_str}")
    print(f"\nDetailed results -> {out_path}")


if __name__ == "__main__":
    run()
