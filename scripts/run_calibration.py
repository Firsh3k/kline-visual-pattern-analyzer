#!/usr/bin/env python3
"""
FIR-13 Empirical Calibration — 命令行运行工具

用法：
  python scripts/run_calibration.py --stocks config/calibration_stocks.json
  python scripts/run_calibration.py --codes 600519,000858 --start-date 2023-01-01
  python scripts/run_calibration.py --regen --output data/calibration/output

输出文件（--output 目录下）：
  1. calibration_dataset.json — 完整数据集（signals + backtests）
  2. calibration_summary.json — 汇总统计
  3. calibration_report.md    — 人类可读报告
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

# 允许直接以脚本方式运行：把项目根加入 sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.calibration.types import (
    PatternType,
    ConfirmationState,
    MarketRegime,
    ConfidenceLabel,
    CalibrationDataset,
    CalibrationSummary,
    CalibrationGroup,
    CalibrationMetric,
    DEFAULT_HORIZONS,
)
from core.calibration.calibrator import EmpiricalCalibrator

# ---------------------------------------------------------------------------
# 可选依赖（由其他子模块实现）：import 失败时降级，保证 --help 可用
# ---------------------------------------------------------------------------

try:
    from core.calibration.data_collector import CalibrationDataCollector  # noqa: F401
    _HAS_COLLECTOR = True
except Exception:  # pragma: no cover - 依赖就绪后才有意义
    CalibrationDataCollector = None
    _HAS_COLLECTOR = False

try:
    from core.calibration.signal_generator import CalibrationSignalGenerator  # noqa: F401
    _HAS_SIGNAL_GEN = True
except Exception:  # pragma: no cover
    CalibrationSignalGenerator = None
    _HAS_SIGNAL_GEN = False

try:
    from core.calibration.backtest_engine import FixedHorizonBacktester  # noqa: F401
    _HAS_BACKTESTER = True
except Exception:  # pragma: no cover
    FixedHorizonBacktester = None
    _HAS_BACKTESTER = False


# ===========================================================================
# 报告渲染
# ===========================================================================

def _fmt_pct(v: Optional[float]) -> str:
    """收益率显示为百分比（内部存储小数）"""
    if v is None:
        return "N/A"
    return f"{v * 100:.1f}%"


def _fmt_num(v: Optional[float], digits: int = 2) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{digits}f}"


def _confidence_display(metric: CalibrationMetric) -> str:
    if metric.confidence_label == ConfidenceLabel.INSUFFICIENT:
        return "insufficient"
    return metric.confidence_label.value


def render_report_md(
    summary: CalibrationSummary,
    dataset: Optional[CalibrationDataset] = None,
) -> str:
    """将 CalibrationSummary 渲染为人类可读 Markdown 报告"""
    lines: List[str] = []
    lines.append("# FIR-13 Empirical Calibration Report")
    lines.append("")

    # ---- 概览 ----
    horizons = sorted(summary.groups[0].metrics_by_horizon.keys()) if summary.groups else list(DEFAULT_HORIZONS)
    lines.append("## 概览")
    lines.append(f"- 股票数量: {summary.stock_count}")
    lines.append(f"- 信号总数: {summary.total_signals:,}")
    if summary.date_range:
        lines.append(f"- 日期范围: {summary.date_range}")
    elif dataset and dataset.start_date:
        lines.append(f"- 日期范围: {dataset.start_date} ~ {dataset.end_date}")
    lines.append(f"- Horizons: {', '.join(f'T+{h}' for h in horizons)}")
    lines.append("")

    # ---- 方向语义说明 ----
    lines.append("## Outcome 方向定义")
    lines.append("- **看涨形态** (ma_bull, w_bottom, hs_bottom, platform_break)：价格上涨 = 赢，`directional_profit = forward_return`")
    lines.append("- **看跌形态** (ma_bear, m_top, hs_top, platform_break_down)：价格下跌 = 赢，`directional_profit = -forward_return`")
    lines.append("- `avg_return` = 原始前向收益率（价格实际涨跌方向，看涨为正/看跌为负均可能）")
    lines.append("- `avg_directional_profit` = 按形态方向调整后的收益（正数表示该形态方向盈利）")
    lines.append("- `win_rate` 基于方向感知的 win（看涨=涨，看跌=跌）")
    lines.append("- `profit_factor` 基于 directional_profit 计算")
    lines.append("")

    # ---- 总体胜率表（按 horizon）----
    overall = summary.find_group("all", "all", "all")
    if overall is not None:
        lines.append("## 总体胜率")
        lines.append("| Horizon | Sample Size | Win Rate | Avg Return | Avg Dir Profit | Avg MFE | Avg MAE | Confidence |")
        lines.append("|---------|-------------|----------|------------|----------------|---------|---------|------------|")
        for h in horizons:
            m = overall.metrics_by_horizon.get(h)
            if m is None:
                continue
            if m.confidence_label == ConfidenceLabel.INSUFFICIENT:
                lines.append(
                    f"| T+{h} | {m.sample_size:,} | N/A (insufficient sample) | N/A | N/A | N/A | N/A | insufficient |"
                )
            else:
                lines.append(
                    f"| T+{h} | {m.sample_size:,} | {_fmt_pct(m.win_rate)} | "
                    f"{_fmt_pct(m.avg_return)} | {_fmt_pct(m.avg_directional_profit)} | "
                    f"{_fmt_pct(m.avg_mfe)} | {_fmt_pct(m.avg_mae)} | {_confidence_display(m)} |"
                )
        lines.append("")

    # 参考 horizon（用于分组表）：优先 T+20，否则取中间值
    ref_h = 20 if 20 in horizons else (horizons[len(horizons) // 2] if horizons else DEFAULT_HORIZONS[3])

    # ---- 按形态分组 ----
    lines.append(f"## 按形态分组（T+{ref_h}）")
    lines.append("| Pattern | Direction | Sample | Win Rate | Avg Return | Avg Dir Profit | Profit Factor | Confidence |")
    lines.append("|---------|-----------|--------|----------|------------|----------------|---------------|------------|")
    for pt in PatternType:
        g = summary.find_group(pt.value, "all", "all")
        if g is None:
            continue
        m = g.metrics_by_horizon.get(ref_h)
        if m is None:
            continue
        direction = "看跌" if pt.is_bearish else "看涨"
        if m.confidence_label == ConfidenceLabel.INSUFFICIENT:
            lines.append(f"| {pt.value} | {direction} | {m.sample_size:,} | N/A (insufficient sample) | N/A | N/A | N/A | insufficient |")
        else:
            lines.append(
                f"| {pt.value} | {direction} | {m.sample_size:,} | {_fmt_pct(m.win_rate)} | "
                f"{_fmt_pct(m.avg_return)} | {_fmt_pct(m.avg_directional_profit)} | "
                f"{_fmt_num(m.profit_factor)} | {_confidence_display(m)} |"
            )
    lines.append("")

    # ---- 按大盘环境分组 ----
    lines.append(f"## 按大盘环境分组（T+{ref_h}）")
    lines.append("| Regime | Sample | Win Rate | Avg Return | Profit Factor | Confidence |")
    lines.append("|--------|--------|----------|------------|---------------|------------|")
    for mr in MarketRegime:
        g = summary.find_group("all", mr.value, "all")
        if g is None:
            continue
        m = g.metrics_by_horizon.get(ref_h)
        if m is None:
            continue
        if m.confidence_label == ConfidenceLabel.INSUFFICIENT:
            lines.append(f"| {mr.value} | {m.sample_size:,} | N/A (insufficient sample) | N/A | N/A | insufficient |")
        else:
            lines.append(
                f"| {mr.value} | {m.sample_size:,} | {_fmt_pct(m.win_rate)} | "
                f"{_fmt_pct(m.avg_return)} | {_fmt_num(m.profit_factor)} | {_confidence_display(m)} |"
            )
    lines.append("")

    # ---- 交叉分组 pattern × regime ----
    lines.append(f"## 交叉分组：形态 × 大盘环境（T+{ref_h}）")
    lines.append("| Pattern | Regime | Sample | Win Rate | Avg Return | Profit Factor | Confidence |")
    lines.append("|---------|--------|--------|----------|------------|---------------|------------|")
    for pt in PatternType:
        for mr in MarketRegime:
            g = summary.find_group(pt.value, mr.value, "all")
            if g is None:
                continue
            m = g.metrics_by_horizon.get(ref_h)
            if m is None:
                continue
            if m.confidence_label == ConfidenceLabel.INSUFFICIENT:
                lines.append(
                    f"| {pt.value} | {mr.value} | {m.sample_size:,} | "
                    f"N/A (insufficient sample) | N/A | N/A | insufficient |"
                )
            else:
                lines.append(
                    f"| {pt.value} | {mr.value} | {m.sample_size:,} | {_fmt_pct(m.win_rate)} | "
                    f"{_fmt_pct(m.avg_return)} | {_fmt_num(m.profit_factor)} | {_confidence_display(m)} |"
                )
    lines.append("")

    # ---- 脚注 ----
    lines.append("---")
    lines.append("")
    lines.append("置信度说明：")
    lines.append("- `insufficient`: sample_size < 30，不报告概率（N/A）")
    lines.append("- `low_confidence`: 30 ≤ sample_size < 100")
    lines.append("- `empirical`: sample_size ≥ 100")
    lines.append("")
    lines.append("收益率说明：")
    lines.append("- `avg_return` = 原始前向收益率 (close_horizon - entry) / entry，反映价格实际涨跌")
    lines.append("- `avg_directional_profit` = 按形态方向调整后的收益：看涨=forward_return，看跌=-forward_return；正数表示该形态方向盈利")
    lines.append("- `win_rate` 基于方向感知的 win：看涨=价格上涨，看跌=价格下跌")
    lines.append("- `profit_factor` = sum(winning directional_profits) / |sum(losing directional_profits)|")
    lines.append("- 所有收益率内部存储为小数（0.05 = 5%），显示为百分比")

    return "\n".join(lines)


# ===========================================================================
# 股票池加载
# ===========================================================================

def load_stock_codes(stocks_path: Optional[str], codes_arg: Optional[str]) -> List[str]:
    """从 --codes 或配置文件加载股票代码列表"""
    if codes_arg:
        return [c.strip() for c in codes_arg.split(",") if c.strip()]

    if not stocks_path or not os.path.exists(stocks_path):
        return []

    with open(stocks_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    def _extract_code(item):
        """从列表项中提取股票代码（支持 dict 含 code 字段，或纯字符串）"""
        if isinstance(item, dict):
            return str(item.get("code", item.get("symbol", "")))
        return str(item)

    # 兼容多种配置格式
    for key in ("stocks", "watchlist", "codes"):
        if key in cfg and isinstance(cfg[key], list):
            return [c for c in (_extract_code(item) for item in cfg[key]) if c]

    # 顶层纯列表
    if isinstance(cfg, list):
        return [c for c in (_extract_code(item) for item in cfg) if c]

    return []


# ===========================================================================
# 主流程
# ===========================================================================

def run_pipeline(args) -> CalibrationSummary:
    """执行采集→信号→回测→校准完整流程，返回 CalibrationSummary"""
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]

    codes = load_stock_codes(args.stocks, args.codes)
    print(f"[1/5] 股票池: {len(codes)} 只 -> {', '.join(codes[:10])}{'...' if len(codes) > 10 else ''}")

    signals = []
    backtests = []
    dataset = CalibrationDataset(
        stock_pool=codes,
        start_date=args.start_date,
        end_date=args.end_date,
        horizons=horizons,
    )

    if not (_HAS_COLLECTOR and _HAS_SIGNAL_GEN and _HAS_BACKTESTER):
        missing = []
        if not _HAS_COLLECTOR:
            missing.append("data_collector.CalibrationDataCollector")
        if not _HAS_SIGNAL_GEN:
            missing.append("signal_generator.CalibrationSignalGenerator")
        if not _HAS_BACKTESTER:
            missing.append("backtest_engine.FixedHorizonBacktester")
        print("[warn] 以下校准依赖模块尚未就绪，跳过实跑，输出空校准结果：")
        for m in missing:
            print(f"       - {m}")
    else:
        import time

        # 2. 采集数据
        print("[2/5] 采集行情数据 ...")
        use_cache = args.cache and not args.regen
        collector = CalibrationDataCollector(cache_dir="data/calibration", use_cache=use_cache)

        # 获取指数数据（用于 market regime 判断）
        index_klines = collector.fetch_index_data("sh000001", args.start_date, args.end_date)
        print(f"  上证指数: {len(index_klines)} bars")

        # 批量获取股票数据（P0-2：携带真实 provenance，不再硬编码 multi_source/qfq）
        klines_by_code: dict = {}
        provenance_by_code: dict = {}
        for idx, code in enumerate(codes):
            try:
                klines, prov = collector.fetch_stock_data_with_provenance(
                    code, args.start_date, args.end_date)
                klines_by_code[code] = klines
                provenance_by_code[code] = prov
                if (idx + 1) % 10 == 0 or idx == len(codes) - 1:
                    print(f"  已采集 {idx + 1}/{len(codes)} 只")
            except Exception as e:
                print(f"  [warn] {code} 数据获取失败: {e}")
            time.sleep(0.3)  # 防限流

        print(f"  成功采集 {len(klines_by_code)}/{len(codes)} 只股票")

        # 3. 生成信号
        print("[3/5] 生成形态信号 ...")
        gen = CalibrationSignalGenerator()
        signals = []
        for code, klines in klines_by_code.items():
            # 构建该股票每个交易日的 market regime
            regime_by_date = {}
            for k in klines:
                regime_by_date[k.date] = collector.determine_market_regime(index_klines, k.date)
            # 将真实 provenance 透传给信号生成器（不再硬编码 data_source/adjustment）
            prov = provenance_by_code.get(code)
            stock_signals = gen.generate_signals(
                klines, code, regime_by_date, provenance=prov,
            )
            signals.extend(stock_signals)
        print(f"  共生成 {len(signals)} 个信号")

        # 4. 固定 horizon 回测
        print("[4/5] 固定 horizon 回测 ...")
        bt = FixedHorizonBacktester()
        backtests = []
        for sig in signals:
            klines = klines_by_code.get(sig.stock_code, [])
            if klines:
                results = bt.backtest_all_horizons(sig, klines, horizons)
                backtests.extend(results)
        complete = sum(1 for b in backtests if b.sufficient_data)
        print(f"  回测记录: {len(backtests)} (完整: {complete})")

        dataset.signals = signals
        dataset.backtests = backtests
        # P0-2 / FIR-23：汇总每只股票的真实 provenance 分布（不再硬编码 data_source）
        from collections import Counter
        src_dist = Counter(
            p.get("data_source", "unknown") for p in provenance_by_code.values())
        # data_quality 现为详细 dict，分布按 status 字符串聚合
        def _dq_status(p):
            dq = p.get("data_quality", "unknown")
            if isinstance(dq, dict):
                return dq.get("status", "unknown")
            return dq
        qual_dist = Counter(_dq_status(p) for p in provenance_by_code.values())
        adj_dist = Counter(
            p.get("adjustment", "unknown") for p in provenance_by_code.values())
        dataset.metadata = {
            "data_source": "FIR-23 MultiSourceDataProvider (Sina/Tencent)",
            "index": "sh000001",
            "stocks_collected": len(klines_by_code),
            "stocks_total": len(codes),
            "provenance_summary": {
                "data_source_distribution": dict(src_dist),
                "adjustment_distribution": dict(adj_dist),
                "data_quality_distribution": dict(qual_dist),
            },
            "provenance_manifest": {
                code: prov for code, prov in provenance_by_code.items()
            },
            "detector_versions": {
                "w_bottom": "canonical_v5.2.0",
                "m_top": "canonical_v5.2.0",
                "hs_bottom": "canonical_v5.2.0",
                "hs_top": "canonical_v5.2.0",
                "ma_bull": "builtin_v1",
                "ma_bear": "builtin_v1",
                "platform_break": "builtin_v1",
                "platform_break_down": "builtin_v1",
            },
            "guardrails": {
                "MIN_HS_HEAD_PROMINENCE": 0.03,
                "MAX_NECKLINE_EXTRAPOLATION_RATIO": 1.5,
                "DEDUP_WINDOW_DAYS": 20,
            },
        }

    # 5. 统计校准
    print("[5/5] 统计校准 ...")
    calibrator = EmpiricalCalibrator()
    summary = calibrator.calibrate(signals or dataset.signals, backtests or dataset.backtests, horizons=horizons)

    # 写出文件
    os.makedirs(args.output, exist_ok=True)

    dataset.created_at = datetime.now(timezone.utc).isoformat()
    dataset.horizons = horizons
    ds_path = os.path.join(args.output, "calibration_dataset.json")
    with open(ds_path, "w", encoding="utf-8") as f:
        json.dump(dataset.to_dict(), f, ensure_ascii=False, indent=2)
    print(f"  -> {ds_path}")

    summary.created_at = datetime.now(timezone.utc).isoformat()
    sum_path = os.path.join(args.output, "calibration_summary.json")
    with open(sum_path, "w", encoding="utf-8") as f:
        json.dump(summary.to_dict(), f, ensure_ascii=False, indent=2)
    print(f"  -> {sum_path}")

    report_md = render_report_md(summary, dataset)
    rep_path = os.path.join(args.output, "calibration_report.md")
    with open(rep_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    print(f"  -> {rep_path}")

    # 打印摘要
    print("\n" + "=" * 60)
    print(report_md)
    return summary


def main():
    parser = argparse.ArgumentParser(description="FIR-13 Empirical A-share Calibration")
    parser.add_argument("--stocks", type=str, default="config/calibration_stocks.json",
                        help="股票池配置文件路径")
    parser.add_argument("--codes", type=str, default=None,
                        help="直接指定股票代码，逗号分隔（覆盖 --stocks）")
    parser.add_argument("--start-date", type=str, default="2023-01-01")
    parser.add_argument("--end-date", type=str, default="2026-10-01")
    parser.add_argument("--horizons", type=str, default="3,5,10,20,40",
                        help="回测 horizons，逗号分隔")
    parser.add_argument("--output", type=str, default="data/calibration/output")
    parser.add_argument("--cache", action="store_true", default=True,
                        help="使用缓存数据")
    parser.add_argument("--no-cache", action="store_true", help="不使用缓存")
    parser.add_argument("--regen", action="store_true",
                        help="重新生成信号和回测结果（忽略已有的 dataset）")
    args = parser.parse_args()

    if args.no_cache:
        args.cache = False

    run_pipeline(args)


if __name__ == "__main__":
    main()
