#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V5 FIR-12 Pattern Migration — A 股 Before/After 对比验证

用法:
    cd <repo_root>
    python3 scripts/v5_validation.py

输入:
    data/v5_validation/{code}.csv  (date,open,high,low,close,volume  date=YYYY-MM-DD)
输出:
    1) 终端打印对比摘要
    2) data/v5_validation/results.json  —— 供 docs/v5_pattern_migration_validation.md 使用

硬约束: 不修改 pattern_scanner.py / core/pivot_patterns.py 等任何 V4/V5 源码。
"""

import os
import re
import sys
import json
from collections import Counter, defaultdict

import pandas as pd

# ---------------------------------------------------------------------------
# 路径与导入
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# V4 (旧实现)
from pattern_scanner import calc_indicators, detect_double_bottom  # noqa: E402

# V5 (新实现) —— import 触发 @registry.pattern 注册
from core.types import OHLCV  # noqa: E402
import core.pivot_patterns  # noqa: E402,F401
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

# 与 V4 信号日匹配时允许的±交易日误差
MATCH_TOL_BARS = 5
# 最近 N 个交易日窗口：仅用此窗口内的 V4 历史信号与 V5 “当前形态”对比
RECENT_WINDOW = 60
# V5 “已突破/可交易” 的 stage（对应 V4 的突破信号语义）
CONFIRMED_STAGES = {"breakout_day", "post_break", "retest", "complete"}


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def load_v4_df(code: str) -> pd.DataFrame:
    """读取 CSV 为 V4 所需 DataFrame（date 为 datetime64）。

    注意: 不复用 pattern_scanner.load_data —— 它强制 format='%Y%m%d'，
    而本验证 CSV 使用 YYYY-MM-DD。这里自行解析日期，其余指标计算走原 calc_indicators。
    """
    path = os.path.join(DATA_DIR, f"{code}.csv")
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d")
    df = df.sort_values("date").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    return df


def to_ohlcv_list(df: pd.DataFrame):
    klines = []
    for _, r in df.iterrows():
        klines.append(OHLCV(
            date=r["date"].strftime("%Y-%m-%d"),
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r["volume"]),
        ))
    return klines


# ---------------------------------------------------------------------------
# V5 工具：提取 w_bottom 右底 anchor 索引
# ---------------------------------------------------------------------------
def kb_by_name(result, name: str):
    for kp in result.key_points:
        if kp.name == name:
            return kp
    return None


def v5_breakout_index(klines, right_bottom_idx: int, neckline: float,
                      breakout_mult: float = 1.01):
    """计算 V5 形态的“突破日”索引 = 右底之后首次收盘 > 颈线*mult 的K线。

    V4 信号日 = 突破日；为做日期上的同口径匹配，V5 形态也锚定到其突破日，
    而不是右底日（右底发生在突破之前，直接比会系统性错位）。
    未突破（FORMING/PRE_BREAK）返回 None。
    """
    if neckline is None:
        return None
    for i in range(right_bottom_idx + 1, len(klines)):
        if klines[i].close > neckline * breakout_mult:
            return i
    return None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run():
    detectors = {
        "w_bottom": WBottomDetector(),
        "m_top": MTopDetector(),
        "hs_bottom": HSBottomDetector(),
        "hs_top": HSTopDetector(),
    }

    per_stock = []
    # 汇总计数器
    v4_total_signals = 0
    v5_w_all = 0
    v5_w_confirmed = 0
    v5_per_pattern = Counter()
    stage_counter = Counter()
    conf_counter = Counter()
    score_buckets = []  # 全部 V5 形态的 score

    # W Bottom 召回对比明细
    recall_rows = []
    matched_pairs = []  # (code, v4_date, v4_idx, v5_rightbottom_date, v5_rightbottom_idx)

    for code, name, industry, setcode in STOCKS:
        csv_path = os.path.join(DATA_DIR, f"{code}.csv")
        if not os.path.exists(csv_path):
            print(f"[SKIP] {code} {name} 无 CSV，跳过")
            per_stock.append({"code": code, "name": name, "industry": industry,
                              "skipped": True})
            continue

        df = load_v4_df(code)
        n = len(df)
        last_idx = n - 1
        last_date = df.iloc[last_idx]["date"].strftime("%Y-%m-%d")
        first_date = df.iloc[0]["date"].strftime("%Y-%m-%d")

        # ---------- V4 (Before) ----------
        df_ind = calc_indicators(df.copy())
        v4_sigs = detect_double_bottom(df_ind)
        v4_sig_records = []
        for s in v4_sigs:
            v4_sig_records.append({
                "date": s["date"].strftime("%Y-%m-%d"),
                "index": int(s["index"]),
                "desc": s["desc"],
            })
        v4_total_signals += len(v4_sig_records)

        # ---------- V5 (After) ----------
        klines = to_ohlcv_list(df)
        v5_results = {}
        for pid, det in detectors.items():
            res = det.detect(klines)
            v5_results[pid] = res
            v5_per_pattern[pid] += len(res)
            for r in res:
                stage_counter[r.stage.value] += 1
                conf_counter[r.confidence.value] += 1
                score_buckets.append(r.score)

        w_all = v5_results["w_bottom"]
        v5_w_all += len(w_all)
        w_confirmed = [r for r in w_all if r.stage.value in CONFIRMED_STAGES]
        v5_w_confirmed += len(w_confirmed)

        # ---------- W Bottom 召回（最近窗口内 V4 信号 vs V5 当前 w_bottom） ----------
        recent_v4 = [s for s in v4_sig_records if s["index"] >= last_idx - RECENT_WINDOW]
        # V5 w_bottom 的 anchor：右底 key point（结构点）+ 推算出的突破日索引
        v5_w_anchors = []
        for r in w_all:
            rb = kb_by_name(r, "右底")
            rb_idx = rb.index if rb else None
            bo_idx = v5_breakout_index(klines, rb_idx, r.neckline_price) \
                if rb_idx is not None else None
            v5_w_anchors.append({
                "rb_index": rb_idx,
                "rb_date": rb.date if rb else None,
                "breakout_index": bo_idx,
                "breakout_date": klines[bo_idx].date if bo_idx is not None else None,
                "stage": r.stage.value,
                "score": r.score,
                "confidence": r.confidence.value,
                "neckline": r.neckline_price,
                "target": r.target_price,
            })

        matched = 0
        for s in recent_v4:
            hit = None
            for a in v5_w_anchors:
                if a["breakout_index"] is None:
                    continue
                if abs(a["breakout_index"] - s["index"]) <= MATCH_TOL_BARS:
                    hit = a
                    break
            if hit:
                matched += 1
                matched_pairs.append({
                    "code": code,
                    "v4_date": s["date"], "v4_index": s["index"],
                    "v5_breakout_date": hit["breakout_date"],
                    "v5_breakout_index": hit["breakout_index"],
                    "v5_rb_date": hit["rb_date"],
                    "v5_stage": hit["stage"], "v5_score": hit["score"],
                    "v5_neckline": hit["neckline"],
                })

        # ---- 结构匹配：按颈线价（±2%）判定 V4 信号与 V5 形态是否为同一个 W ----
        # V4 desc 形如 "第一低点45.9，第二低点47.5...颈线位49.2，当前收盘53.0突破颈线"
        def v4_neckline(desc):
            m = re.search(r"颈线位([0-9.]+)", desc)
            return float(m.group(1)) if m else None

        struct_matched = 0
        v4_necklines = []
        for s in recent_v4:
            nl = v4_neckline(s["desc"])
            v4_necklines.append(nl)
            hit = None
            if nl is not None:
                for a in v5_w_anchors:
                    if a["neckline"] and abs(a["neckline"] - nl) / nl <= 0.02:
                        hit = a
                        break
            if hit:
                struct_matched += 1
        # V4 去重后的“独立 W 形态”数（颈线价聚簇）
        distinct_v4_patterns = len({round(nl, 1) for nl in v4_necklines if nl is not None})
        distinct_struct_matched = 0
        used = set()
        for nl in v4_necklines:
            if nl is None:
                continue
            for a in v5_w_anchors:
                key = (round(a["neckline"], 1) if a["neckline"] else None)
                if a["neckline"] and abs(a["neckline"] - nl) / nl <= 0.02 and key not in used:
                    used.add(key)
                    distinct_struct_matched += 1
                    break

        n_recent = len(recent_v4)
        recall = (matched / n_recent) if n_recent > 0 else None

        recall_rows.append({
            "code": code, "name": name,
            "v4_total": len(v4_sig_records),
            "v4_recent60": n_recent,
            "v5_w_all": len(w_all),
            "v5_w_confirmed": len(w_confirmed),
            "matched": matched,
            "recall": recall,
            "struct_matched": struct_matched,
            "distinct_v4_patterns": distinct_v4_patterns,
            "distinct_struct_matched": distinct_struct_matched,
            "v4_sigs": v4_sig_records,
            "v5_w_anchors": v5_w_anchors,
        })

        per_stock.append({
            "code": code, "name": name, "industry": industry,
            "setcode": setcode, "bars": n,
            "first_date": first_date, "last_date": last_date,
            "v4_w_signals": v4_sig_records,
            "v5_counts": {pid: len(v5_results[pid]) for pid in detectors},
            "skipped": False,
        })

    # ------------------------------------------------------------------
    # 汇总指标
    # ------------------------------------------------------------------
    # Recall（总体）：仅对有 recent V4 信号的股票汇总
    total_recent = sum(r["v4_recent60"] for r in recall_rows)
    total_matched = sum(r["matched"] for r in recall_rows)
    overall_recall = (total_matched / total_recent) if total_recent > 0 else None

    # 结构（颈线价）口径汇总
    total_struct_sig = sum(r["struct_matched"] for r in recall_rows)
    total_distinct_v4 = sum(r["distinct_v4_patterns"] for r in recall_rows)
    total_distinct_struct = sum(r["distinct_struct_matched"] for r in recall_rows)
    # 信号级结构召回：被 V5 形态结构上覆盖的 V4 信号占比
    struct_signal_recall = (total_struct_sig / total_recent) if total_recent > 0 else None
    # 形态级结构召回：V4 独立 W 形态中 V5 也识别出的比例
    struct_pattern_recall = (total_distinct_struct / total_distinct_v4) if total_distinct_v4 > 0 else None

    # FP reduction: 以 V5 “已突破可交易” 的 w_bottom 数 对比 V4 历史信号数
    # (V5 当前形态 vs V4 历史触发，口径不同，见报告说明)
    fp_ratio = (v5_w_confirmed / v4_total_signals) if v4_total_signals > 0 else None
    fp_reduction_pct = ((v4_total_signals - v5_w_confirmed) / v4_total_signals * 100.0) \
        if v4_total_signals > 0 else None
    fp_ratio_all = (v5_w_all / v4_total_signals) if v4_total_signals > 0 else None

    score_counter = Counter(score_buckets)

    summary = {
        "stocks": per_stock,
        "recall_rows": recall_rows,
        "totals": {
            "v4_total_w_signals": v4_total_signals,
            "v5_w_bottom_all": v5_w_all,
            "v5_w_bottom_confirmed": v5_w_confirmed,
            "v5_per_pattern": dict(v5_per_pattern),
            "stage_dist": dict(stage_counter),
            "confidence_dist": dict(conf_counter),
            "score_dist": dict(sorted(score_counter.items())),
            "overall_recent_v4_signals": total_recent,
            "overall_matched": total_matched,
            "overall_recall": overall_recall,
            "struct_signal_recall": struct_signal_recall,
            "struct_pattern_recall": struct_pattern_recall,
            "total_distinct_v4_patterns": total_distinct_v4,
            "total_distinct_struct_matched": total_distinct_struct,
            "total_struct_signal_matched": total_struct_sig,
            "fp_ratio_confirmed_over_v4": fp_ratio,
            "fp_reduction_pct_confirmed": fp_reduction_pct,
            "fp_ratio_all_over_v4": fp_ratio_all,
            "match_tol_bars": MATCH_TOL_BARS,
            "recent_window": RECENT_WINDOW,
            "confirmed_stages": sorted(CONFIRMED_STAGES),
        },
        "matched_pairs": matched_pairs,
    }

    out_path = os.path.join(DATA_DIR, "results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # ---------------- 终端打印 ----------------
    print("=" * 78)
    print("V5 FIR-12 Pattern Migration — Before/After 验证结果")
    print("=" * 78)
    print(f"\n{'代码':<8}{'名称':<10}{'K线':>5}{'V4 W信号':>9}{'近60日':>7}{'V5 W全部':>9}{'V5 W已破':>9}{'匹配':>5}{'Recall':>9}")
    for r in recall_rows:
        rec = "N/A" if r["recall"] is None else f"{r['recall']*100:.0f}%"
        print(f"{r['code']:<8}{r['name']:<10}{'':>5}{r['v4_total']:>9}{r['v4_recent60']:>7}"
              f"{r['v5_w_all']:>9}{r['v5_w_confirmed']:>9}{r['matched']:>5}{rec:>9}")

    t = summary["totals"]
    print("\n--- 总体 ---")
    print(f"V4 历史 W 信号总数:        {t['v4_total_w_signals']}")
    print(f"V5 当前 w_bottom 形态总数: {t['v5_w_bottom_all']}")
    print(f"V5 w_bottom 已突破数:       {t['v5_w_bottom_confirmed']}")
    print(f"各形态识别数: {t['v5_per_pattern']}")
    print(f"Stage 分布:   {t['stage_dist']}")
    print(f"Confidence:   {t['confidence_dist']}")
    print(f"Score 分布:   {t['score_dist']}")
    print(f"总体 Recall (近{RECENT_WINDOW}日窗口, ±{MATCH_TOL_BARS}交易日): "
          f"{total_matched}/{total_recent} = "
          f"{'N/A' if overall_recall is None else f'{overall_recall*100:.1f}%'}")
    print(f"结构召回(颈线±2%, 信号级): {total_struct_sig}/{total_recent} = "
          f"{'N/A' if struct_signal_recall is None else f'{struct_signal_recall*100:.1f}%'}")
    print(f"结构召回(颈线±2%, 形态级): {total_distinct_struct}/{total_distinct_v4} = "
          f"{'N/A' if struct_pattern_recall is None else f'{struct_pattern_recall*100:.1f}%'}")
    print(f"FP reduction (已突破口径): V5/V4 = {fp_ratio}, 减少 {fp_reduction_pct}%")
    print(f"详细结果已写入: {out_path}")


if __name__ == "__main__":
    run()
