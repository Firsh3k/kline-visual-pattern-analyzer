"""
校准统计（FIR-13）：只读展示，保留样本量与证据口径；证据不足或口径不适用 → 数值为 null（前端显示 N/A）。

注意：data/calibration/output/calibration_summary.json 中
- W底 / M头 / 头肩底 / 头肩顶 由 canonical 检测器（canonical_v5.2.0）生成 → 口径适用；
- 均线多头/空头、平台突破/破位 由校准脚本内置的简化规则（builtin_v1）生成，与当前 core 检测器
  （FIR-32 迁移的 V4 规则 + FIR-35）不是同一规则 → 不适用，数值一律置空；
- pattern_type = all 混合了两类 → 不适用。
启发式形态分数（score）不是历史胜率，任何地方都不得把它当胜率展示。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

APPLICABLE = {"w_bottom", "m_top", "hs_bottom", "hs_top"}
VALUE_KEYS = ("win_rate", "avg_return", "median_return", "avg_mfe", "avg_mae", "profit_factor", "std_return",
              "max_return", "min_return", "avg_directional_profit")


def load(path: Path) -> Dict[str, Any]:
    if not Path(path).exists():
        return {"available": False, "reason": "校准文件不存在", "groups": []}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    groups = []
    for g in raw["groups"]:
        applicable = g["pattern_type"] in APPLICABLE
        metrics = {}
        for h, m in g["metrics_by_horizon"].items():
            ok = applicable and m["confidence_label"] != "insufficient"
            metrics[h] = {"horizon": m["horizon"], "sample_size": m["sample_size"], "total_signals": m["total_signals"],
                          "confidence_label": m["confidence_label"],
                          **{k: (m.get(k) if ok else None) for k in VALUE_KEYS}}
        groups.append({"pattern_type": g["pattern_type"], "market_regime": g["market_regime"],
                       "confirmation_state": g["confirmation_state"], "applicable": applicable,
                       "not_applicable_reason": None if applicable else (
                           "校准信号由简化规则（builtin_v1）生成，与当前检测器规则不同" if g["pattern_type"] != "all"
                           else "混合了不适用的形态"),
                       "metrics_by_horizon": metrics})
    return {"available": True, "version": raw.get("version"), "created_at": raw.get("created_at"),
            "total_signals": raw.get("total_signals"), "stock_count": raw.get("stock_count"),
            "date_range": raw.get("date_range"), "metadata": raw.get("metadata"),
            "thresholds": {"insufficient_below": raw.get("metadata", {}).get("min_sample_size", 30),
                           "empirical_from": raw.get("metadata", {}).get("empirical_threshold", 100)},
            "disclaimer": "形态分数是算法信号强度，不是历史胜率。下表为历史回测统计；样本不足或口径不适用时显示 N/A。",
            "groups": groups}
