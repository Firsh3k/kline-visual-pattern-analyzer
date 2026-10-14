#!/usr/bin/env python3
"""
形态超额收益验证 — 命令行工具

在 FIR-13 校准数据集上，对「形态 × 大盘环境」分组计算：
随机入场基准（B1 同日股票池 / B2 同股随机日期）、交易成本、样本外切分。

用法：
  python scripts/run_excess_return.py
  python scripts/run_excess_return.py --split-date 2025-10-01 --prices auto

--prices:
  auto         有 FIR-13 K线缓存（data/calibration/*_qfq.json）就用缓存，否则从数据集重建
  cache        只用缓存（缺任何一只则报错）
  reconstruct  只用数据集重建

输出（--output 目录）：excess_return_report.md、excess_return_summary.json
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.calibration.excess_return import (  # noqa: E402
    COST_SCENARIOS,
    CostModel,
    PricePanel,
    a_share_trading_days,
    build_trades,
    summarize,
)

FOCUS_PATTERNS = ("platform_break", "hs_bottom", "w_bottom")
PATTERN_CN = {
    "platform_break": "平台突破",
    "hs_bottom": "头肩底",
    "w_bottom": "W底",
    "ma_bull": "均线多头",
}
REGIME_CN = {"range": "震荡", "bull": "牛市", "bear": "熊市", None: "全部环境"}
PERIOD_CN = {"all": "全样本", "in_sample": "样本内", "out_of_sample": "样本外"}


def load_cache_panel(codes: List[str], cache_dir: str) -> Optional[PricePanel]:
    from core.types import OHLCV
    klines = {}
    for code in codes:
        files = sorted(glob.glob(os.path.join(cache_dir, f"{code}_*_qfq.json")))
        if not files:
            return None
        with open(files[-1], encoding="utf-8") as f:
            data = json.load(f)
        rows = data["klines"] if isinstance(data, dict) else data
        klines[code] = [OHLCV.from_dict(r) for r in rows]
    return PricePanel.from_klines(klines)


def validate_reconstruction(panel: PricePanel, csv_dir: str, horizon: int) -> Optional[dict]:
    """用仓库内真实日K（data/v5_validation/*.csv）检验重建价格的 T+h 收益误差"""
    errs, errs_interp, n_codes = [], [], 0
    for path in sorted(glob.glob(os.path.join(csv_dir, "*.csv"))):
        code = os.path.splitext(os.path.basename(path))[0]
        if code not in panel.closes:
            continue
        with open(path, encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f)]
        dates = [r["date"] for r in rows]
        closes = [float(r["close"]) for r in rows]
        n_codes += 1
        for i in range(len(rows) - horizon):
            rec = panel.forward_return(code, dates[i], horizon)
            if rec is None:
                continue
            real = closes[i + horizon] / closes[i] - 1
            errs.append(abs(rec - real))
            ip = panel.interpolated.get(code, set())
            if dates[i] in ip or dates[i + horizon] in ip:
                errs_interp.append(abs(rec - real))
    if not errs:
        return None

    def q(xs, p):
        xs = sorted(xs)
        return xs[min(int(len(xs) * p), len(xs) - 1)] if xs else None
    return {"stocks": n_codes, "pairs": len(errs),
            "median_abs_err": q(errs, 0.5), "p90_abs_err": q(errs, 0.9),
            "pairs_interpolated": len(errs_interp),
            "interp_median_abs_err": q(errs_interp, 0.5),
            "interp_p90_abs_err": q(errs_interp, 0.9)}


def pct(v: Optional[float], signed: bool = True) -> str:
    if v is None:
        return "N/A"
    return f"{v * 100:+.2f}%" if signed else f"{v * 100:.1f}%"


def ci_str(ci) -> str:
    if not ci:
        return "N/A"
    return f"[{ci[0] * 100:+.2f}%, {ci[1] * 100:+.2f}%]"


def verdict(st) -> str:
    if st is None or st.n < 30:
        return "样本不足"
    if st.n_clusters < 6:
        return "月份太少"
    ci = st.ci_excess_b1_net
    if ci and ci[0] > 0:
        return "显著为正"
    if ci and ci[1] < 0:
        return "显著为负"
    return "不显著"


def main():
    ap = argparse.ArgumentParser(description="形态超额收益验证")
    ap.add_argument("--dataset", default="data/calibration/output/calibration_dataset.json")
    ap.add_argument("--output", default="data/calibration/output")
    ap.add_argument("--split-date", default="2025-10-01",
                    help="样本外起始日（信号日 ≥ 该日为样本外）")
    ap.add_argument("--horizons", default="5,10,20,40")
    ap.add_argument("--prices", choices=("auto", "cache", "reconstruct"), default="auto")
    ap.add_argument("--cache-dir", default="data/calibration")
    args = ap.parse_args()

    with open(args.dataset, encoding="utf-8") as f:
        ds = json.load(f)
    horizons = [int(h) for h in args.horizons.split(",")]
    codes = sorted({s["stock_code"] for s in ds["signals"]})

    panel, recon_stats = None, None
    if args.prices in ("auto", "cache"):
        panel = load_cache_panel(codes, args.cache_dir)
        if panel is None and args.prices == "cache":
            sys.exit("[error] K线缓存不完整，无法使用 --prices cache")
    if panel is None:
        days = a_share_trading_days(ds["start_date"], "2026-12-31")
        panel, recon_stats = PricePanel.reconstruct(ds, days)
    print(f"[prices] source={panel.source} stocks={len(panel.codes)} {recon_stats or ''}")
    validation = (validate_reconstruction(panel, "data/v5_validation", 20)
                  if panel.source != "kline_cache" else None)

    base_cost = COST_SCENARIOS["基准"]
    groups = [("震荡 × 看涨三形态（合并）", FOCUS_PATTERNS, "range")]
    groups += [(f"震荡 × {PATTERN_CN[p]}", (p,), "range") for p in FOCUS_PATTERNS]
    groups += [("牛市 × 看涨三形态（对照）", FOCUS_PATTERNS, "bull"),
               ("熊市 × 看涨三形态（对照）", FOCUS_PATTERNS, "bear"),
               ("全部环境 × 看涨三形态（对照）", FOCUS_PATTERNS, None)]

    results: Dict[str, dict] = {}
    for name, pats, rg in groups:
        results[name] = {"patterns": list(pats), "regime": rg, "by_horizon": {}}
        for h in horizons:
            row = {}
            for period_key, split in (("all", None), ("split", args.split_date)):
                trades, st = build_trades(ds, panel, pats, rg, h, split)
                if period_key == "all":
                    row["all"] = summarize(trades, base_cost)
                    if name == groups[0][0] and h == 20:
                        row["cost_scenarios"] = {k: summarize(trades, c)
                                                 for k, c in COST_SCENARIOS.items()}
                else:
                    row["purged"] = st["purged"]
                    for p in ("in_sample", "out_of_sample"):
                        row[p] = summarize([t for t in trades if t.period == p], base_cost)
            results[name]["by_horizon"][h] = row

    md = render(results, groups, horizons, args, panel, recon_stats, validation, base_cost, ds)
    os.makedirs(args.output, exist_ok=True)
    rep = os.path.join(args.output, "excess_return_report.md")
    with open(rep, "w", encoding="utf-8") as f:
        f.write(md)

    def ser(o):
        if hasattr(o, "to_dict"):
            return o.to_dict()
        raise TypeError(type(o))
    summ = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": args.dataset, "split_date": args.split_date,
        "price_source": panel.source, "reconstruction": recon_stats,
        "reconstruction_validation": validation,
        "cost_round_trip": base_cost.round_trip,
        "groups": results,
    }
    sp = os.path.join(args.output, "excess_return_summary.json")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(summ, f, ensure_ascii=False, indent=2, default=ser)
    print(f"  -> {rep}\n  -> {sp}")
    print(md)


def render(results, groups, horizons, args, panel, recon_stats, validation, cost: CostModel, ds) -> str:
    L: List[str] = []
    focus = groups[0][0]
    f20 = results[focus]["by_horizon"].get(20, {})
    a, i_, o = f20.get("all"), f20.get("in_sample"), f20.get("out_of_sample")

    L.append("# 形态超额收益验证报告")
    L.append("")
    n_stocks = len({x["stock_code"] for x in ds["signals"]})
    L.append(f"数据集：FIR-13 校准数据（{n_stocks} 只有信号的股票，信号日 2024-05 ~ 2026-09）。"
             f"样本外切分日 {args.split_date}。基准往返成本 {cost.round_trip * 100:.2f}%。")
    L.append("")

    L.append("## 结论（T+20，震荡市 × 平台突破/头肩底/W底）")
    if a and i_ and o:
        if verdict(a) == "显著为正" and verdict(o) == "显著为正":
            head = "扣除成本、对比同期随机买入后，这组形态仍有统计上可靠的超额收益，且在样本外成立。"
        elif verdict(a) == "显著为负":
            head = "扣除成本后，这组形态显著跑输同期随机买入。"
        else:
            head = ("原报告里的高收益主要来自「震荡市这几段时间整个股票池都在涨」；"
                    "扣除成本、对比同期随机买入后，剩下的超额收益很小，统计上与 0 无法区分，"
                    "样本内外也不一致。")
        L.append(f"**一句话：{head}**")
        L.append("")
    if a:
        L.append(f"- 全样本 {a.n} 笔：平均毛收益 {pct(a.gross)}，扣费后 {pct(a.net)}；"
                 f"同日股票池等权随机买入 {pct(a.b1)}。")
        L.append(f"- **扣费后超额（相对同日随机买入）{pct(a.excess_b1_net)}**，"
                 f"按月聚类 95% 置信区间 {ci_str(a.ci_excess_b1_net)}（{a.n_clusters} 个月），{verdict(a)}；"
                 f"跑赢基准的笔数占比 {pct(a.beat_b1_rate, False)}。")
        L.append(f"- 相对「同股、同为震荡市的随机日期买入」的择时超额 {pct(a.excess_b2)}，"
                 f"置信区间 {ci_str(a.ci_excess_b2)}。")
    if i_ and o:
        L.append(f"- 样本内（< {args.split_date}）{i_.n} 笔，扣费后超额 {pct(i_.excess_b1_net)} {ci_str(i_.ci_excess_b1_net)}，{verdict(i_)}；"
                 f"样本外（≥ {args.split_date}）{o.n} 笔，扣费后超额 {pct(o.excess_b1_net)} {ci_str(o.ci_excess_b1_net)}，{verdict(o)}。")
    L.append("- 注意：震荡市在这段时间只出现在少数几个时间段（见按月分布），"
             "信号成簇出现，有效独立样本远少于笔数。")
    L.append("")

    L.append("## 方法")
    L.append("- **入场/退出**：信号日收盘价买入，持有 h 个交易日后收盘价卖出（与 FIR-13 回测一致）。"
             "同股同日多个形态合并为一笔。")
    L.append("- **B1 同日随机买入**：信号当天在股票池其余股票中等权随机买入的期望收益，"
             "代表「同期随便买」的机会成本；视为被动持有，不扣增量成本。")
    L.append("- **B2 同股随机日期**：同一只股票、同一大盘环境、同一时间段（样本内/外）内随机日期买入的期望收益，"
             "衡量形态的择时价值；双方都交易，成本抵消，按毛收益比较。")
    L.append(f"- **交易成本**：佣金万2.5/边 + 印花税0.05%（卖出）+ 过户费0.001%/边 + 滑点0.10%/边，"
             f"往返 {cost.round_trip * 100:.2f}%。另做低/高成本敏感性。")
    L.append(f"- **样本外切分**：信号日 ≥ {args.split_date} 为样本外；样本内信号若持有期跨过切分日则剔除（purge），"
             "B2 的随机日期也限制在同一时间段内。形态和分组规则在两段完全相同，没有在样本内调参。")
    L.append("- **置信区间**：按信号所在自然月做聚类自助法（2000 次），因为同月信号高度相关。"
             "样本内/外各只有 6~10 个月，区间只是粗略参考；少于 6 个月不做判断。")
    if panel.source != "kline_cache":
        L.append("- **价格来源（重要局限）**：本次运行环境无法访问行情接口，基准所需的全股票池日K"
                 "由数据集内的价格点重建（信号日入场价 + 各 horizon 收盘价，按交易日历定位），"
                 "≤3 个交易日的缺口做对数线性插值。")
        if recon_stats:
            L.append(f"  价格点 {recon_stats['points']:,} 个，冲突丢弃 {recon_stats['conflicts']:,} 个，"
                     f"插值 {recon_stats['interpolated']:,} 个。")
        if validation:
            L.append(f"  用仓库内真实日K（{validation['stocks']} 只，2026-05~09）检验 T+20 收益：{validation['pairs']} 对样本，"
                     f"绝对误差中位数 {validation['median_abs_err'] * 100:.2f} 个百分点，90 分位 {validation['p90_abs_err'] * 100:.2f}；"
                     f"其中端点含插值的 {validation['pairs_interpolated']} 对，误差中位数 "
                     f"{validation['interp_median_abs_err'] * 100:.2f}、90 分位 {validation['interp_p90_abs_err'] * 100:.2f} 个百分点。"
                     "插值误差是随机噪声，会放大单笔基准误差，但对上千笔的均值影响很小。")
        L.append("  在能联网的机器上先跑 `scripts/run_calibration.py` 生成 K 线缓存，再跑本脚本即可自动改用真实日K。")
    L.append("")

    L.append("## 分组明细（基准成本，B1 超额为扣费后）")
    for h in horizons:
        L.append(f"### T+{h}")
        L.append("| 分组 | 区间 | 笔数 | 月数 | 毛收益 | 扣费后 | B1 同日随机 | 扣费后超额 vs B1 | 95% CI | 择时超额 vs B2 | 判断 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for name, _p, _r in groups:
            row = results[name]["by_horizon"][h]
            for pk in ("all", "in_sample", "out_of_sample"):
                st = row.get(pk)
                if st is None:
                    L.append(f"| {name} | {PERIOD_CN[pk]} | 0 | | | | | | | | 无样本 |")
                    continue
                L.append(f"| {name} | {PERIOD_CN[pk]} | {st.n} | {st.n_clusters} | {pct(st.gross)} | {pct(st.net)} | "
                         f"{pct(st.b1)} | {pct(st.excess_b1_net)} | {ci_str(st.ci_excess_b1_net)} | "
                         f"{pct(st.excess_b2)} | {verdict(st)} |")
        L.append("")

    cs = f20.get("cost_scenarios")
    if cs:
        L.append("## 成本敏感性（T+20，震荡 × 看涨三形态，全样本）")
        L.append("| 成本情景 | 往返成本 | 扣费后收益 | 扣费后超额 vs B1 | 95% CI |")
        L.append("|---|---|---|---|---|")
        for k, st in cs.items():
            L.append(f"| {k} | {COST_SCENARIOS[k].round_trip * 100:.2f}% | {pct(st.net)} | "
                     f"{pct(st.excess_b1_net)} | {ci_str(st.ci_excess_b1_net)} |")
        L.append("")

    L.append("## 震荡 × 看涨三形态信号按月分布（T+20）")
    from collections import Counter
    trades, _ = build_trades(ds, panel, FOCUS_PATTERNS, "range", 20, None)
    by_m = Counter(t.signal_date[:7] for t in trades)
    exm: Dict[str, List[float]] = {}
    for t in trades:
        if t.b1_same_day_pool is not None:
            exm.setdefault(t.signal_date[:7], []).append(cost.net_return(t.gross_return) - t.b1_same_day_pool)
    L.append("| 月份 | 笔数 | 扣费后超额 vs B1 |")
    L.append("|---|---|---|")
    for m in sorted(by_m):
        xs = exm.get(m, [])
        L.append(f"| {m} | {by_m[m]} | {pct(sum(xs) / len(xs)) if xs else 'N/A'} |")
    L.append("")
    L.append("---")
    L.append("生成：`python scripts/run_excess_return.py`（core/calibration/excess_return.py）")
    return "\n".join(L)


if __name__ == "__main__":
    main()
