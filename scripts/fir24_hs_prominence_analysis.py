"""
FIR-24: H&S head-prominence 分布分析（证据驱动阈值选择）

用 canonical HSBottomDetector / HSTopDetector 在全部缓存 qfq 数据上运行，
收集所有通过现有闸门（未加突出度规则）的 H&S 候选的 head_prominence：

    head_prominence = |head_price - avg_shoulder_price| / avg_shoulder_price
    avg_shoulder_price = (ls.price + rs.price) / 2

统计 P10/P25/P50/P75/P90/mean/std，据此选择 MIN_HS_HEAD_PROMINENCE。

运行：python scripts/fir24_hs_prominence_analysis.py
输出：控制台统计 + docs/fir24_hs_prominence_analysis.md 引用的数字
"""

import glob
import json
import os
import statistics
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.types import OHLCV  # noqa: E402
import core.pivot_patterns as pp  # noqa: E402
from core.pivot_patterns import HSBottomDetector, HSTopDetector  # noqa: E402

# 统计「加 guardrail 之前」的全集分布：临时中和阈值，
# 让 detect() 收齐所有通过既有结构闸门的候选（不被新 guardrail 过滤）。
pp.MIN_HS_HEAD_PROMINENCE = 0.0
pp.MAX_NECKLINE_EXTRAPOLATION_RATIO = 1e9

DATA_GLOB = os.path.join(PROJECT_ROOT, "data", "calibration", "*_qfq.json")


def _kp(result, name):
    for kp in result.key_points:
        if kp.name == name:
            return kp
    return None


def collect_prominences():
    rows = []  # (stock, detector, stage, head, ls, rs, avg_sh, prominence, nl_now, slope)
    files = sorted(glob.glob(DATA_GLOB))
    for fp in files:
        stock = os.path.basename(fp).split("_")[0]
        try:
            d = json.load(open(fp))
            klines = [OHLCV.from_dict(b) for b in d]
        except Exception as e:  # noqa: BLE001
            print(f"[skip] {stock}: {e}")
            continue
        for det_cls in (HSBottomDetector, HSTopDetector):
            det = det_cls()
            try:
                results = det.detect(klines)
            except Exception:  # noqa: BLE001
                continue
            for r in results:
                ls = _kp(r, "左肩")
                head = _kp(r, "头")
                rs = _kp(r, "右肩")
                if ls is None or head is None or rs is None:
                    continue
                avg_sh = (ls.price + rs.price) / 2.0
                if avg_sh <= 0:
                    continue
                prom = abs(head.price - avg_sh) / avg_sh
                rows.append({
                    "stock": stock,
                    "detector": det_cls.__name__,
                    "stage": r.stage.value,
                    "head": head.price,
                    "ls": ls.price,
                    "rs": rs.price,
                    "avg_sh": avg_sh,
                    "prominence": prom,
                    "nl_now": r.neckline_price,
                })
    return rows


def pct(sorted_vals, q):
    """线性分位数"""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def summarize(values, label):
    s = sorted(values)
    n = len(s)
    out = {
        "label": label,
        "n": n,
        "p10": pct(s, 0.10),
        "p25": pct(s, 0.25),
        "p50": pct(s, 0.50),
        "p75": pct(s, 0.75),
        "p90": pct(s, 0.90),
        "mean": statistics.mean(values) if values else float("nan"),
        "std": statistics.pstdev(values) if values else float("nan"),
        "min": s[0] if s else float("nan"),
        "max": s[-1] if s else float("nan"),
    }
    return out


def main():
    rows = collect_prominences()
    print(f"total H&S candidates collected: {len(rows)}")

    bot = [r["prominence"] for r in rows if r["detector"] == "HSBottomDetector"]
    top = [r["prominence"] for r in rows if r["detector"] == "HSTopDetector"]
    allv = [r["prominence"] for r in rows]

    for vals, label in [(bot, "hs_bottom"), (top, "hs_top"), (allv, "all_hs")]:
        s = summarize(vals, label)
        print(f"\n=== {label} (n={s['n']}) ===")
        for k in ("min", "p10", "p25", "p50", "mean", "p75", "p90", "max", "std"):
            print(f"  {k:>6}: {s[k]*100:.2f}%")

    # 阈值建议：P25 与 中位数一半 取较大者，下限 3%
    p25 = pct(sorted(allv), 0.25)
    p50 = pct(sorted(allv), 0.50)
    cand = max(p25, p50 / 2.0, 0.03)
    print(f"\n=== 阈值建议 ===")
    print(f"  P25            = {p25*100:.2f}%")
    print(f"  median/2       = {p50/2*100:.2f}%")
    print(f"  floor 3%       = 3.00%")
    print(f"  -> MIN_HS_HEAD_PROMINENCE = {cand*100:.2f}%")

    # 002415 弱案例对照
    weak = [r for r in rows if r["stock"] == "002415" and r["detector"] == "HSBottomDetector"]
    print(f"\n=== 002415 hs_bottom candidates (n={len(weak)}) ===")
    for r in weak:
        print(f"  head={r['head']:.2f} ls={r['ls']:.2f} rs={r['rs']:.2f} "
              f"prom={r['prominence']*100:.2f}% nl_now={r['nl_now']:.2f} stage={r['stage']}")

    # --- 颈线外推比率分布（决定 MAX_NECKLINE_EXTRAPOLATION_RATIO） ---
    # HSBottom: ratio = nl_now / head_price（颈线应在头上，过远=外推失控）
    # HSTop:    ratio = head_price / nl_now（颈线应在头下，过远=外推失控）
    ratios = []
    for r in rows:
        if r["head"] <= 0 or not r["nl_now"] or r["nl_now"] <= 0:
            continue
        if r["detector"] == "HSBottomDetector":
            ratios.append(r["nl_now"] / r["head"])
        else:
            ratios.append(r["head"] / r["nl_now"])
    s = sorted(ratios)
    print(f"\n=== 颈线外推比率 nl_now/head (bottom) | head/nl_now (top), n={len(s)} ===")
    for q, lab in [(0.10, "p10"), (0.25, "p25"), (0.50, "p50"),
                   (0.75, "p75"), (0.90, "p90"), (0.95, "p95"), (0.99, "p99")]:
        print(f"  {lab}: {pct(s, q):.3f}")
    print(f"  max: {s[-1]:.3f}")
    # 002415 弱案例的比率
    print("\n=== 002415 弱案例外推比率 ===")
    for r in weak:
        if r["nl_now"] and r["nl_now"] > 0 and r["head"] > 0:
            print(f"  nl_now/head = {r['nl_now']/r['head']:.3f}  (prom={r['prominence']*100:.2f}%)")


if __name__ == "__main__":
    main()
