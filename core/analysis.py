"""
单一分析入口（FIR-38 / FIR-31）：个股分析、自选股扫描、市场扫描、每日管线全部调用这里

同一份输入（规范化行情 + 复权方式 + 250 根窗口 + asOf）→ 同一份输出。Node BFF 与网页只展示，
不做任何形态 / 指标 / 大盘判断。

数据合同（FIR-23 / FIR-25 / FIR-27）：
- 输入 K 线严格校验，任何问题都拒绝整只股票（不修补、不去重、不猜测）：
  日期格式、严格递增（无重复 / 乱序）、OHLC 为有限正数且满足高低不变量、成交量为有限非负数。
- input.data_hash：窗口内规范化 K 线的 sha256，相同输入必得相同哈希。
- session_state：close_confirmed（已收盘确认）/ intraday（盘中，最后一根未收盘）/ unknown（日历未核实）。
- calendar.status：只有 asOf 所在年份在权威交易日历（VERIFIED_YEARS）内才为 verified，
  否则 unverified；不再默认 verified。
"""
from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import re
import subprocess
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import __version__ as CORE_VERSION
from .indicators import (ClassicIndicators, ReferenceIndicators, INDICATOR_CONVENTIONS,
                         volume_status, atr_stop)
from .market_data import trading_calendar as tc
from .market_regime import ClassicMarketRegimeAnalyzer
from .pivot_patterns import WBottomDetector
from .registry import registry
from .signals import build_signals
from .swing_pivot import SwingPivotEngine
from .types import OHLCV

ANALYSIS_SCHEMA_VERSION = "3.1"   # 3.1：新增 signals 层（R67-1 / R67-2）
WINDOW = 250
MIN_BARS = 60
CN_TZ = timezone(timedelta(hours=8))
MARKET_CLOSE = time(15, 0)
PATTERN_ORDER = ["w_bottom", "m_top", "hs_bottom", "hs_top",
                 "ma_bull", "ma_bear", "platform_break", "platform_break_down"]
# R67-1：个股页展示的全部经典指标序列都由 core 输出（前端不再自算），与 ClassicIndicators 键一致
INDICATOR_SERIES = ("ma5", "ma10", "ma20", "ma60", "vma5", "vma20", "dif", "dea", "macd", "rsi",
                    "kdj_k", "kdj_d", "kdj_j", "boll_mid", "boll_up", "boll_low", "boll_width", "atr")
ADJUSTMENTS = ("qfq", "none", "unknown")   # unknown 仅用于来源未声明复权方式的离线 CSV；API 只接受 qfq / none

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CODE_RE = re.compile(r"^\d{6}$")
_ROOT = Path(__file__).resolve().parent.parent


class InvalidInputError(ValueError):
    """输入不合法（整只股票拒绝）。issues 为机器可读的问题清单。"""

    def __init__(self, message: str, issues: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.issues = issues or []


# ------------------------------------------------------------------ 代码 / 交易所

def exchange_symbol(code: str) -> str:
    """6 位 A 股代码 → 带交易所前缀的代码（sh/sz/bj）；无法识别时报错（fail-closed）"""
    if not isinstance(code, str) or not _CODE_RE.match(code):
        raise InvalidInputError(f"股票代码必须是 6 位数字：{code!r}")
    from .market_data.sina_provider import _market_prefix
    prefix = _market_prefix(code)
    if prefix is None:
        raise InvalidInputError(f"无法识别交易所：{code}")
    return f"{prefix}{code}"


# ------------------------------------------------------------------ 输入校验

def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_bars(bars: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """严格校验 K 线；返回问题清单（空 = 通过）。bars 元素需含 day/open/high/low/close/volume。

    R67-3：日期落在已核实交易日历年份（VERIFIED_YEARS）内时，必须是交易日；休市日出现 K 线即拒绝。
    未核实年份不做该项判断（结果的 calendar.status 会是 unverified）。
    """
    issues: List[Dict[str, Any]] = []

    def bad(i, field, reason, value=None):
        issues.append({"index": i, "date": bars[i].get("day") if i is not None else None,
                       "field": field, "reason": reason, "value": value})

    prev = None
    for i, b in enumerate(bars):
        d = b.get("day")
        if not isinstance(d, str) or not _DATE_RE.match(d):
            bad(i, "day", "日期格式必须为 YYYY-MM-DD", d)
        else:
            try:
                datetime.strptime(d, "%Y-%m-%d")
            except ValueError:
                bad(i, "day", "日期不存在", d)
            else:
                if tc.is_calendar_verified(d) and not tc.is_trading_day(d):
                    bad(i, "day", "休市日（非交易日）出现 K 线", d)
            if prev is not None:
                if d == prev:
                    bad(i, "day", "重复日期", d)
                elif d < prev:
                    bad(i, "day", "日期乱序（必须严格递增）", d)
            prev = d
        vals = {}
        for f in ("open", "high", "low", "close"):
            v = b.get(f)
            if not _finite(v):
                bad(i, f, "价格必须是有限数（不允许 NaN / Inf / 空）", repr(v))
            elif v <= 0:
                bad(i, f, "价格必须为正数", v)
            else:
                vals[f] = float(v)
        vol = b.get("volume")
        if not _finite(vol):
            bad(i, "volume", "成交量必须是有限数（不允许 NaN / Inf / 空）", repr(vol))
        elif vol < 0:
            bad(i, "volume", "成交量不能为负", vol)
        if len(vals) == 4:
            if vals["high"] < max(vals["open"], vals["close"], vals["low"]):
                bad(i, "high", "最高价低于开盘/收盘/最低价", vals["high"])
            if vals["low"] > min(vals["open"], vals["close"], vals["high"]):
                bad(i, "low", "最低价高于开盘/收盘/最高价", vals["low"])
    return issues


def normalize_bars(raw: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """统一字段名（date→day）与数值类型；不排序、不去重（校验会拒绝乱序 / 重复）"""
    out = []
    for r in raw:
        out.append({
            "day": r.get("day", r.get("date")),
            "open": r.get("open"), "high": r.get("high"), "low": r.get("low"),
            "close": r.get("close"), "volume": r.get("volume"),
        })
    return out


def data_hash(bars: Sequence[Mapping[str, Any]]) -> str:
    """规范化 K 线（窗口内）的 sha256。数值统一为 float 后 JSON 序列化，键顺序固定。"""
    canon = [[b["day"], float(b["open"]), float(b["high"]), float(b["low"]),
              float(b["close"]), float(b["volume"])] for b in bars]
    return hashlib.sha256(json.dumps(canon, separators=(",", ":")).encode()).hexdigest()


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------ 日历 / 收盘状态

def calendar_info(symbol: str, last_date: str, now: datetime) -> Dict[str, Any]:
    """交易日历证据。只有能用权威日历证明时才给 verified。"""
    cal_id = tc.derive_calendar_id(symbol)
    today = now.astimezone(CN_TZ).date().isoformat()
    info: Dict[str, Any] = {"calendar_id": cal_id, "status": "unverified", "source": None,
                            "as_of_is_trading_day": None, "expected_trade_date": None,
                            "stale": None, "session_state": "unknown"}
    if cal_id == tc.CALENDAR_UNKNOWN:
        info["reason"] = "无法识别交易所"
        return info
    if not (tc.is_calendar_verified(last_date) and tc.is_calendar_verified(today)):
        info["reason"] = f"交易日历未覆盖 {last_date[:4]} / {today[:4]} 年（已核实年份：{sorted(tc.VERIFIED_YEARS)}）"
        return info
    info["status"] = "verified"
    info["source"] = "core.market_data.trading_calendar（国务院办公厅节假日安排 + 交易所休市通知）"
    info["as_of_is_trading_day"] = tc.is_trading_day(last_date)
    today_trading = tc.is_trading_day(today)
    after_close = now.astimezone(CN_TZ).time() >= MARKET_CLOSE
    expected = today if (today_trading and after_close) else tc.prev_trading_day(today)
    info["expected_trade_date"] = expected
    if last_date == today and today_trading and not after_close:
        info["session_state"] = "intraday"
        info["stale"] = False
    else:
        info["session_state"] = "close_confirmed"
        info["stale"] = last_date < expected
    return info


# ------------------------------------------------------------------ 计算

def to_klines(bars: Sequence[Mapping[str, Any]]) -> List[OHLCV]:
    return [OHLCV(date=b["day"], open=float(b["open"]), high=float(b["high"]), low=float(b["low"]),
                  close=float(b["close"]), volume=float(b["volume"])) for b in bars]


def run_core(klines: List[OHLCV]) -> Dict[str, Any]:
    """8 种形态 + 摆动点 + 指标（含口径与对照值）。所有入口共用。"""
    left, right = WBottomDetector.left_bars, WBottomDetector.right_bars
    pivots = SwingPivotEngine(left_bars=left, right_bars=right).detect(klines)
    patterns = {pid: [r.to_dict() for r in registry.get_pattern(pid).detect(klines)]
                for pid in PATTERN_ORDER}
    ind = ClassicIndicators().calculate(klines)
    ref = ReferenceIndicators().calculate(klines)
    last = len(klines) - 1
    return {
        "core_version": CORE_VERSION,
        "pivot_params": {"left_bars": left, "right_bars": right},
        "pivots": [p.to_dict() for p in pivots],
        "patterns": patterns,
        "indicators": {
            "latest": {k: ind[k][last] for k in ind},
            "series": {k: ind[k] for k in INDICATOR_SERIES},
            "volume": volume_status(klines, ind),
            "atr_stop": atr_stop(klines, ind),
            "conventions": INDICATOR_CONVENTIONS,
            "reference": {"latest": {k: v[last] for k, v in ref.items()}, "series": ref},
        },
    }


def _roundtrip(obj: Any) -> Any:
    return json.loads(json.dumps(obj, ensure_ascii=False, allow_nan=False))


@functools.lru_cache(maxsize=1)
def core_content_sha256() -> str:
    """
    线上版本自证：core 包内全部 .py 文件内容的 sha256（按相对路径排序，路径与内容一起参与哈希）。
    与 git / 部署平台无关，同一份代码在任何地方算出的值都相同；
    scripts/v6_fingerprint.py 可按任意 git 版本算出期望值做对比。
    """
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for f in sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts):
        rel = f.relative_to(root).as_posix()
        h.update(rel.encode() + b"\0" + hashlib.sha256(f.read_bytes()).hexdigest().encode() + b"\n")
    return h.hexdigest()


def engine_commit() -> str:
    for k in ("ENGINE_COMMIT", "GITHUB_SHA"):
        if os.environ.get(k):
            return os.environ[k]
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def today_cn(now: Optional[datetime] = None) -> str:
    """Asia/Shanghai 当前日期（所有入口统一口径）"""
    return (now or datetime.now(CN_TZ)).astimezone(CN_TZ).date().isoformat()


def parse_as_of(as_of: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """
    R67-3：asOf 统一校验（在取行情之前调用）。None = 最新。
    必须是 YYYY-MM-DD、真实存在的日期，且不晚于 Asia/Shanghai 今天；否则拒绝，
    不允许“未来日期 + 截取到旧 K 线”被当作合法请求。
    """
    if as_of is None:
        return None
    if not isinstance(as_of, str) or not _DATE_RE.match(as_of):
        raise InvalidInputError(f"asOf 必须为 YYYY-MM-DD：{as_of!r}")
    try:
        datetime.strptime(as_of, "%Y-%m-%d")
    except ValueError:
        raise InvalidInputError(f"asOf 日期不存在：{as_of}")
    today = today_cn(now)
    if as_of > today:
        raise InvalidInputError(f"asOf {as_of} 晚于今天（{today}，北京时间），不能分析未来日期")
    return as_of


def prepare_window(raw_bars: Iterable[Mapping[str, Any]], as_of: Optional[str] = None,
                   window: int = WINDOW, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """asOf 校验 → 规范化 → 全量校验 → 按 asOf 截断（point-in-time）→ 取最近 window 根"""
    as_of = parse_as_of(as_of, now)
    bars = normalize_bars(raw_bars)
    issues = validate_bars(bars)
    if issues:
        raise InvalidInputError(f"行情数据不合法：{len(issues)} 处问题（首个：第 {issues[0]['index']} 根 "
                                f"{issues[0]['date']} {issues[0]['field']} {issues[0]['reason']}）", issues)
    if as_of is not None:
        bars = [b for b in bars if b["day"] <= as_of]
    bars = bars[-window:]
    if len(bars) < MIN_BARS:
        raise InvalidInputError(f"K线不足 {MIN_BARS} 根（实际 {len(bars)} 根），均线形态与指标无法计算")
    return bars


def analyze(code: str, raw_bars: Iterable[Mapping[str, Any]], *, adjustment: str,
            provenance: Mapping[str, Any], as_of: Optional[str] = None, name: Optional[str] = None,
            now: Optional[datetime] = None, window: int = WINDOW) -> Dict[str, Any]:
    """
    个股分析（所有入口共用）。

    provenance 至少含 source；adjustment 为 qfq / none。返回 JSON 安全的 dict，
    其中 core 与直接调用各检测器的 to_dict() 完全一致（JSON 往返后）。
    """
    if adjustment not in ADJUSTMENTS:
        raise InvalidInputError(f"adjustment 只能是 {ADJUSTMENTS}：{adjustment!r}")
    if window != WINDOW:
        raise InvalidInputError(f"window 固定为 {WINDOW}（所有入口必须一致）")
    symbol = exchange_symbol(code)
    now = now or datetime.now(CN_TZ)
    bars = prepare_window(raw_bars, as_of, window, now=now)
    last_date = bars[-1]["day"]
    if last_date > today_cn(now):
        raise InvalidInputError(f"最后一根 K 线日期 {last_date} 晚于当前日期")
    cal = calendar_info(symbol, last_date, now)
    result = _roundtrip(run_core(to_klines(bars)))
    bar_rows = [[b["day"], float(b["open"]), float(b["high"]), float(b["low"]),
                 float(b["close"]), float(b["volume"])] for b in bars]
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "code": code,
        "symbol": symbol,
        "name": name or code,
        "request": {"as_of": as_of, "adjustment": adjustment, "window": window},
        "as_of": last_date,
        "session_state": cal["session_state"],
        "calendar": cal,
        "input": {
            "data_hash": data_hash(bars),
            "bar_count": len(bars),
            "first_date": bars[0]["day"],
            "last_date": last_date,
            "adjustment": adjustment,
            "provenance": _roundtrip(dict(provenance)),
        },
        "engine": {"core_version": CORE_VERSION, "engine_commit": engine_commit(),
                   "core_sha256": core_content_sha256(),
                   "pattern_order": PATTERN_ORDER, "window": window},
        "bars": bar_rows,
        "core": {**result, "result_sha256": sha256_json(result)},
        # 信号层（实例选择 / 确认 / 主导 / 线段）——不进入 core.result_sha256，自带 signals_sha256
        "signals": _roundtrip(build_signals(result["patterns"], bar_rows, PATTERN_ORDER)),
    }


def analyze_market(index_bars: Iterable[Mapping[str, Any]], as_of: Optional[str] = None,
                   now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """大盘环境（上证指数）。输入同样严格校验（含 asOf 与休市日）；数据不足返回 None。"""
    as_of = parse_as_of(as_of, now)
    bars = normalize_bars(index_bars)
    issues = validate_bars(bars)
    if issues:
        raise InvalidInputError(f"大盘行情数据不合法：{len(issues)} 处问题", issues)
    if as_of is not None:
        bars = [b for b in bars if b["day"] <= as_of]
    bars = bars[-WINDOW:]
    if not bars:
        return None
    env = ClassicMarketRegimeAnalyzer().analyze_dict(to_klines(bars))
    if env is None:
        return None
    return {**_roundtrip(env), "as_of": bars[-1]["day"], "data_hash": data_hash(bars), "bar_count": len(bars)}


def pattern_summary(doc: Mapping[str, Any]) -> Dict[str, Any]:
    """
    每种形态最近一次结果的摘要（扫描列表 / 自选股列表共用，保证与个股页同源）。
    实例选择、confirmed、dominant 全部取自 doc["signals"]（与个股页同一份），不在这里另立规则。
    """
    bars = doc["bars"]
    sig = doc["signals"]
    latest = {}
    for pid, s in sig["latest"].items():
        if s is None:
            continue
        r = doc["core"]["patterns"][pid][s["instance_index"]]
        latest[pid] = {"pattern_name": r["pattern_name"], "direction": s["direction"],
                       "category": s["category"], "stage": s["stage"], "score": s["score"],
                       "confidence": r["confidence"], "last_key_date": s["last_key_date"],
                       "bars_since": s["bars_since"], "active": s["active"],
                       "confirmed": s["confirmed"], "reasons": s["reasons"]}
    close, prev = bars[-1][4], bars[-2][4]
    return {
        "code": doc["code"], "name": doc["name"], "as_of": doc["as_of"],
        "close": close, "change": round(close - prev, 2), "change_pct": round((close - prev) / prev * 100, 2),
        "patterns_latest": latest,
        "dominant": sig["dominant"],
        "signal_rule_version": sig["rule_version"],
        "session_state": doc["session_state"],
        "stale": doc["calendar"]["stale"],
        "data_hash": doc["input"]["data_hash"],
        "result_sha256": doc["core"]["result_sha256"],
        "data_source": doc["input"]["provenance"].get("source"),
        "adjustment": doc["input"]["adjustment"],
    }
