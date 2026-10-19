"""
FIR-40 / R67-1、R67-2：正式“信号层”——实例选择、确认判定、主导形态、可直接绘制的线段

这里是个股页、自选股列表、扫描列表唯一的择优 / 确认规则来源。Node BFF 与网页只透传、只展示，
不得再按阶段名自行推导“已确认买入 / 卖出”。

signals 放在 doc["signals"]，不进入 core（core.result_sha256 只覆盖检测器原始输出，不受本层影响）；
本层自带 signals_sha256 与 rule_version，规则改动必须升版本。

规则（rule_version = SIGNAL_RULE_VERSION）：
1. 每种形态取“最新一次”实例：关键点最大 index 降序 → 关键点最小 index 降序 → 检测器输出顺序。
2. 确认（confirmed）只给价格形态（category = price_pattern）：
   - 颈线类（w_bottom / m_top / hs_bottom / hs_top）：阶段 ∈ {breakout_day, post_break, retest}
     且 evidence.breakout_occurred 为 true 且 evidence.back_inside_neckline 为 false
     且 evidence.volume_confirmed 为 true。
   - 平台类（platform_break / platform_break_down）：阶段 ∈ {breakout_day, post_break, retest}
     且 evidence.broke 为 true（当日收盘仍在判定线外侧）且 evidence.volume_confirmed 为 true
     （即 V4 原文“放量突破平台：突破确认 / 放量跌破平台：破位确认”）。
   - 均线类（ma_bull / ma_bear，category = trend_state）只描述趋势状态，永远不是买卖确认。
   不满足时 confirmed=false，reasons 列出未满足的条件（机器可读 code + 中文说明）。
3. 主导形态（pick）：先在已确认形态中按 分数高 → 关键点更近 → PATTERN_ORDER 取第一；
   没有已确认形态时取最近的价格形态结构（关键点更近 → 分数高 → PATTERN_ORDER），confirmed=false；
   连价格形态都没有时取趋势状态（同样 confirmed=false）；全无为 None。
   - dominant.overall：两个方向一起 pick；
   - dominant.structure.bull / .bear：各方向单独 pick（个股页多 / 空两栏展示用）；
   - dominant.bull / dominant.bear：该方向 pick 结果已确认时为其 pattern_id，否则 None
     （扫描 / 自选股列表的“买入信号 / 卖出信号”列只看这两个字段）。
4. 线段（lines）：以 doc.bars 的 index 为横坐标，前端原样绘制，不再自行推断。
   - W 底 / M 顶：颈线点 → 最后一根，水平（neckline_price）。
   - 头肩底 / 头肩顶：左颈线点 → 最后一根（neckline_price），倾斜，必经右颈线点。
   - 平台：平台起点 → 最后一根，水平（平台上沿 / 下沿）。
   - 均线：无线段（均线本身由 core.indicators.series 绘制）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

SIGNAL_RULE_VERSION = "signals-1"

ACTIVE_STAGES = ("breakout_day", "post_break", "retest")
KNOWN_STAGES = ("forming", "pre_break", "breakout_day", "post_break", "retest", "failed_break", "complete")
NECKLINE_PATTERNS = ("w_bottom", "m_top", "hs_bottom", "hs_top")
PLATFORM_PATTERNS = ("platform_break", "platform_break_down")
TREND_PATTERNS = ("ma_bull", "ma_bear")
CATEGORY_PRICE = "price_pattern"
CATEGORY_TREND = "trend_state"


class SignalSchemaError(ValueError):
    """检测器输出不符合信号层的合同（fail-closed，不猜测）。"""


def _key_indices(inst: Mapping[str, Any]) -> List[int]:
    kps = inst.get("key_points") or []
    if not kps:
        raise SignalSchemaError(f"{inst.get('pattern_id')} 缺少关键点")
    return [int(kp["index"]) for kp in kps]


def latest_instance(results: Sequence[Mapping[str, Any]]) -> Optional[int]:
    """返回最新实例在 results 中的下标（规则 1）；无实例返回 None。"""
    best, best_key = None, None
    for i, r in enumerate(results):
        idx = _key_indices(r)
        key = (max(idx), min(idx), -i)
        if best_key is None or key > best_key:
            best, best_key = i, key
    return best


def _reason(code: str, text: str) -> Dict[str, str]:
    return {"code": code, "text": text}


def _confirm(pid: str, inst: Mapping[str, Any]) -> Dict[str, Any]:
    stage = inst["stage"]
    ev = inst.get("evidence") or {}
    bull = inst["direction"] == "bull"
    if inst["category"] == CATEGORY_TREND:
        return {"confirmed": False,
                "reasons": [_reason("trend_state_only", "均线排列只描述趋势状态，不构成买卖确认")]}
    reasons: List[Dict[str, str]] = []
    if stage not in ACTIVE_STAGES:
        reasons.append(_reason("stage_not_active", f"阶段为 {stage}，尚未突破 / 回踩" if bull else f"阶段为 {stage}，尚未跌破 / 回抽"))
    if pid in NECKLINE_PATTERNS:
        if ev.get("breakout_occurred") is not True:
            reasons.append(_reason("no_breakout", "尚未发生收盘越过颈线判定线"))
        if ev.get("back_inside_neckline") is not False:
            days = ev.get("days_back_inside")
            if ev.get("breakout_occurred") is True:
                reasons.append(_reason("back_inside_neckline",
                                       f"突破后已连续 {days} 日收盘回到颈线内侧" if days else "收盘已回到颈线内侧"))
        if ev.get("volume_confirmed") is not True:
            reasons.append(_reason("volume_not_confirmed", "量能未确认（5 日均量未超过 20 日均量 ×1.1）"))
    elif pid in PLATFORM_PATTERNS:
        if ev.get("broke") is not True:
            reasons.append(_reason("not_beyond_line", "当日收盘未越过平台判定线" if bull else "当日收盘未跌破平台判定线"))
        if ev.get("volume_confirmed") is not True:
            reasons.append(_reason("volume_not_confirmed", "量能未确认（当日量未超过 5 日均量 ×1.3）"))
    else:
        raise SignalSchemaError(f"未知形态：{pid}")
    return {"confirmed": not reasons, "reasons": reasons}


def _kp(inst: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    for kp in inst.get("key_points") or []:
        if kp.get("name") == name:
            return kp
    raise SignalSchemaError(f"{inst.get('pattern_id')} 缺少关键点“{name}”")


def _segment(kind: str, label: str, idx1: int, price1: float, idx2: int, price2: float) -> Dict[str, Any]:
    return {"kind": kind, "label": label, "idx1": int(idx1), "price1": round(float(price1), 4),
            "idx2": int(idx2), "price2": round(float(price2), 4)}


def _lines(pid: str, inst: Mapping[str, Any], bars: Sequence[Sequence[Any]]) -> List[Dict[str, Any]]:
    last = len(bars) - 1
    nl = inst.get("neckline_price")
    if pid in ("w_bottom", "m_top"):
        p = _kp(inst, "颈线点")
        return [_segment("neckline", "颈线", p["index"], nl, last, nl)]
    if pid in ("hs_bottom", "hs_top"):
        # 检测器以左右颈线点线性插值，故右颈线点必在该线段上（测试逐一断言）
        ln = _kp(inst, "左颈线点")
        _kp(inst, "右颈线点")
        return [_segment("neckline", "颈线", ln["index"], ln["price"], last, nl)]
    if pid in PLATFORM_PATTERNS:
        start = (inst.get("evidence") or {}).get("platform_start_date")
        idx = next((i for i, b in enumerate(bars) if b[0] == start), None)
        if idx is None:
            raise SignalSchemaError(f"{pid} 的平台起点 {start!r} 不在 K 线窗口内")
        label = "平台上沿" if pid == "platform_break" else "平台下沿"
        return [_segment("platform_edge", label, idx, nl, last, nl)]
    return []


def _order_confirmed(c: Mapping[str, Any]):
    return (-c["score"], -c["last_key_index"], c["order"])


def _order_latest(c: Mapping[str, Any]):
    return (-c["last_key_index"], -c["score"], c["order"])


def build_signals(core_patterns: Mapping[str, Sequence[Mapping[str, Any]]],
                  bars: Sequence[Sequence[Any]], pattern_order: Sequence[str]) -> Dict[str, Any]:
    last_idx = len(bars) - 1
    latest: Dict[str, Optional[Dict[str, Any]]] = {}
    for order, pid in enumerate(pattern_order):
        results = core_patterns.get(pid)
        if results is None:
            raise SignalSchemaError(f"core.patterns 缺少 {pid}")
        i = latest_instance(results)
        if i is None:
            latest[pid] = None
            continue
        inst = results[i]
        if inst.get("stage") not in KNOWN_STAGES:
            raise SignalSchemaError(f"{pid} 阶段未知：{inst.get('stage')!r}")
        if inst.get("direction") not in ("bull", "bear"):
            raise SignalSchemaError(f"{pid} 方向未知：{inst.get('direction')!r}")
        if inst.get("category") not in (CATEGORY_PRICE, CATEGORY_TREND):
            raise SignalSchemaError(f"{pid} 类别未知：{inst.get('category')!r}")
        idx = _key_indices(inst)
        latest[pid] = {
            "pattern_id": pid, "instance_index": i, "order": order,
            "direction": inst["direction"], "category": inst["category"],
            "stage": inst["stage"], "score": inst["score"],
            "first_key_index": min(idx), "last_key_index": max(idx),
            "last_key_date": bars[max(idx)][0], "bars_since": last_idx - max(idx),
            "active": inst["stage"] in ACTIVE_STAGES,
            **_confirm(pid, inst),
            "lines": _lines(pid, inst, bars),
        }
    cands = [c for c in latest.values() if c]

    def pick(direction: Optional[str]) -> Optional[Dict[str, Any]]:
        pool = [c for c in cands if direction is None or c["direction"] == direction]
        conf = sorted((c for c in pool if c["confirmed"]), key=_order_confirmed)
        if conf:
            c = conf[0]
        else:
            price = sorted((c for c in pool if c["category"] == CATEGORY_PRICE), key=_order_latest)
            trend = sorted((c for c in pool if c["category"] == CATEGORY_TREND), key=_order_latest)
            c = (price or trend or [None])[0]
        if c is None:
            return None
        return {"pattern_id": c["pattern_id"], "direction": c["direction"], "confirmed": c["confirmed"]}

    bull, bear = pick("bull"), pick("bear")
    dominant = {
        "bull": bull["pattern_id"] if bull and bull["confirmed"] else None,
        "bear": bear["pattern_id"] if bear and bear["confirmed"] else None,
        "overall": pick(None),
        # 各方向展示用的“当前结构”：有已确认取已确认，否则取最近结构（confirmed=false）
        "structure": {"bull": bull, "bear": bear},
    }
    body = {"rule_version": SIGNAL_RULE_VERSION, "latest": latest, "dominant": dominant}
    return {**body, "signals_sha256": hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
