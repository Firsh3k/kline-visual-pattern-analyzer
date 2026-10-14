"""
V4 → V5 迁移测试的变异测试（FIR-32 QA Q4：要求在 CI 中给出可复现的 red/green 证据）

做法：把被测源码（core/indicators.py、core/ma_platform_patterns.py、core/market_regime.py）
读成文本，对单个常量做一次替换（变异），在内存中编译成独立模块，再用与
test_v4_migration_parity.py 相同的数据集和冻结 V4 对照物比对。

- 对照组（不变异）：必须与 V4 完全一致 —— 证明本测试的加载方式本身不引入差异（green）。
- 每个变异体：必须至少出现一处与 V4 不一致 —— 证明一致性测试能抓到这类错误（red）。

源码文件本身不会被修改。
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "oracle"))

import analyze_stock_v4 as v4  # noqa: E402
import core  # noqa: E402
from core.registry import registry  # noqa: E402
import test_v4_migration_parity as P  # noqa: E402  复用同一组数据集与转换函数


def _load_mutated(module: str, old: str = None, new: str = None, deps: dict = None) -> types.ModuleType:
    """以 core 子模块身份在内存中编译源码（可选做一次替换），不注册到全局 registry。"""
    src = (ROOT / "core" / f"{module}.py").read_text(encoding="utf-8")
    if old is not None:
        assert src.count(old) == 1, f"变异锚点在 {module}.py 中必须恰好出现一次：{old!r}"
        src = src.replace(old, new)
    mod = types.ModuleType(f"core._mutant_{module}")
    mod.__package__ = "core"
    mod.__file__ = str(ROOT / "core" / f"{module}.py")
    saved = {k: sys.modules.get(f"core.{k}") for k in (deps or {})}
    orig_ind, orig_pat = registry.indicator, registry.pattern
    registry.indicator = lambda name, force=False: (lambda cls: cls)
    registry.pattern = lambda cls: cls
    try:
        for k, m in (deps or {}).items():  # 让 `from .indicators import ...` 拿到变异后的依赖
            sys.modules[f"core.{k}"] = m
        exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    finally:
        registry.indicator, registry.pattern = orig_ind, orig_pat
        for k, m in saved.items():
            sys.modules[f"core.{k}"] = m
    return mod


def _indicator_mismatch(ind_mod) -> bool:
    for rows in P.DATASETS.values():
        a = v4.calc_indicators(P._v4_data(rows))
        b = ind_mod.ClassicIndicators().calculate(P._v5_klines(rows))
        if a != b:
            return True
    return False


def _pattern_mismatch(pat_mod) -> bool:
    dets = (pat_mod.MABullDetector(), pat_mod.MABearDetector(),
            pat_mod.PlatformBreakDetector(), pat_mod.PlatformBreakdownDetector())
    for rows in P.DATASETS.values():
        for n in P._prefixes(rows):
            d, k = P._v4_data(rows[:n]), P._v5_klines(rows[:n])
            ind = v4.calc_indicators(d)
            bull, bear = v4.match_patterns(d, ind), v4.match_bear_patterns(d, ind)
            pairs = ((bull["ma_bull"], dets[0]), (bear["ma_bear"], dets[1]),
                     (bull["platform_break"], dets[2]), (bear["platform_break_down"], dets[3]))
            for v4d, det in pairs:
                res = det.detect(k)
                if res:
                    if res[0].score != v4d["score"] or res[0].notes.split("｜")[0] != v4d["stage"]:
                        return True
                elif v4d["stage"] in pat_mod._MA_STAGE or (
                        v4d["stage"] in pat_mod._PLAT_STAGE):
                    return True  # V4 认为结构存在，变异体却不报告
    return False


def _regime_mismatch(reg_mod) -> bool:
    an = reg_mod.ClassicMarketRegimeAnalyzer()
    for rows in P.DATASETS.values():
        for n in P._prefixes(rows, 50):
            if v4.calc_market_env_from_data(P._v4_data(rows[:n])) != an.analyze_dict(P._v5_klines(rows[:n])):
                return True
    return False


# ------------------------------------------------------------------ 对照组（green）

def test_control_unmutated_modules_match_v4():
    ind = _load_mutated("indicators")
    assert not _indicator_mismatch(ind)
    pat = _load_mutated("ma_platform_patterns", deps={"indicators": ind})
    assert not _pattern_mismatch(pat)
    reg = _load_mutated("market_regime", deps={"indicators": ind})
    assert not _regime_mismatch(reg)


# ------------------------------------------------------------------ 变异体（red）

INDICATOR_MUTANTS = [
    ("KDJ 平滑权重", "k = prev_k * 2/3 + rsv * 1/3", "k = prev_k * 0.66 + rsv * 0.34"),
    ("RSI 周期", "avg_gain = gains / 14", "avg_gain = gains / 13"),
    ("MACD 慢线", "e26 = e26 * 25/27 + closes[i] * 2/27", "e26 = e26 * 24/26 + closes[i] * 2/26"),
    ("BOLL 倍数", "boll_up[i] = mid + 2 * std", "boll_up[i] = mid + 2.1 * std"),
    ("ATR 平滑", "atr[i] = (atr[i-1] * 13 + tr) / 14", "atr[i] = (atr[i-1] * 12 + tr) / 13"),
    ("MA60 周期", "ma60 = _ma(closes, 60)", "ma60 = _ma(closes, 59)"),
]

PATTERN_MUTANTS = [
    ("平台突破阈值", "broke = cur_price > plat_high * 1.01", "broke = cur_price > plat_high * 1.02"),
    ("平台跌破阈值", "broke = cur_price < plat_low * 0.99", "broke = cur_price < plat_low * 0.98"),
    ("平台振幅上限", "tight = range_pct < 0.18", "tight = range_pct < 0.20"),
    ("平台放量倍数", "vol_boost = klines[last].volume > (ind['vma5'][last] or 0) * 1.3",
     "vol_boost = klines[last].volume > (ind['vma5'][last] or 0) * 1.2"),
    ("均线排列分值", "score = (25 if c1 else 0) + (25 if c2 else 0)", "score = (25 if c1 else 0) + (20 if c2 else 0)"),
    ("均线斜率阈值", "if aligned and c4 and slope > 0.02 * cur_price:", "if aligned and c4 and slope > 0.03 * cur_price:"),
]

REGIME_MUTANTS = [
    ("均线多头加分", "if ma_bull: score += 25;", "if ma_bull: score += 20;"),
    ("MA20 斜率回看", "ma20_up = ma20 > (ind['ma20'][max(0, last - 5)] or ma20)",
     "ma20_up = ma20 > (ind['ma20'][max(0, last - 4)] or ma20)"),
    ("阶段阈值", "elif score >= 30:", "elif score >= 31:"),
]


@pytest.mark.parametrize("label,old,new", INDICATOR_MUTANTS, ids=[m[0] for m in INDICATOR_MUTANTS])
def test_indicator_mutant_is_detected(label, old, new):
    assert _indicator_mismatch(_load_mutated("indicators", old, new)), f"变异未被发现：{label}"


@pytest.mark.parametrize("label,old,new", PATTERN_MUTANTS, ids=[m[0] for m in PATTERN_MUTANTS])
def test_pattern_mutant_is_detected(label, old, new):
    ind = _load_mutated("indicators")
    assert _pattern_mismatch(_load_mutated("ma_platform_patterns", old, new, deps={"indicators": ind})), \
        f"变异未被发现：{label}"


@pytest.mark.parametrize("label,old,new", REGIME_MUTANTS, ids=[m[0] for m in REGIME_MUTANTS])
def test_regime_mutant_is_detected(label, old, new):
    ind = _load_mutated("indicators")
    assert _regime_mismatch(_load_mutated("market_regime", old, new, deps={"indicators": ind})), \
        f"变异未被发现：{label}"
