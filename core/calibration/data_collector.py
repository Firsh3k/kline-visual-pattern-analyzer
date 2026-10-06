"""
FIR-13: Empirical Calibration — 校准数据采集层

批量获取历史日线数据并缓存，为经验校准提供 point-in-time 的数据基础。

设计要点：
- 使用 FIR-23 MultiSourceDataProvider 获取 qfq 前复权数据（技术分析需复权价）
- 缓存到 data/calibration/，避免重复拉取、支持离线复现
- market regime 判断严格 point-in-time：只使用 date 及之前的指数K线
"""

from __future__ import annotations

import os
import json
import time
from typing import List, Optional, Dict, Tuple

from core.types import OHLCV, AdjustmentType, MarketDataSeries
from core.market_data import MultiSourceDataProvider
from core.market_data.trading_calendar import (
    CALENDAR_BSE,
    CALENDAR_SSE,
    CALENDAR_SZSE,
    derive_calendar_id,
)
from core.calibration.types import MarketRegime


# MA60 窗口（交易日）
REGIME_MA_PERIOD = 60
# MA60 方向判断的回看天数（"5日前MA60"）
REGIME_MA_LOOKBACK = 5

# ---------------------------------------------------------------------------
# FIR-13/23 (P0-2): canonical provenance 占位 —— 当 provenance 无法从
# MarketDataSeries 获取时，显式标记为 "unknown"，而不是硬编码假值（如
# multi_source/qfq/OK）。字段集为完整 canonical provenance schema。
#
# 注意：data_quality 为详细 dict（fail-closed status + 子指标）。
# signal_generator 会把该 dict 原样透传到 SignalOccurrence.data_quality；
# provenance_manifest（dataset.metadata）保留完整结构化质量信息。
# ---------------------------------------------------------------------------
UNKNOWN_PROVENANCE: Dict[str, object] = {
    "data_source": "unknown",
    "adjustment": "unknown",
    "frequency": "unknown",
    "schema_version": "unknown",
    "trading_calendar": "unknown",
    "fetched_at": "unknown",
    "last_bar_date": "unknown",
    "bar_count": 0,
    "date_range": {"start": "unknown", "end": "unknown"},
    "data_quality": {
        "status": "unknown",
        "missing_days": None,
        "suspended_days": None,
        "ohlc_violations": None,
        "adjustment_consistency": "unknown",
        "source_consistency": "unknown",
        "anomalies_count": None,
        "completeness_ratio": None,
    },
    "quality_detail": None,
    "provenance_hash": "unknown",
}


def to_market_symbol(code: str) -> str:
    """6位股票代码 → canonical provider symbol（FIR-25/FIR-27）。

    交易所归属必须复用 FIR-25 的 ``derive_calendar_id``，避免 calibration
    自己维护另一套前缀规则而发生 contract drift：
    - SSE  -> ``sh``
    - SZSE -> ``sz``
    - BSE  -> ``bj``

    无法权威识别的代码直接 fail-closed，不再静默回退到 SZSE。
    """
    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"Invalid A-share stock code: {code!r}")

    calendar_id = derive_calendar_id(code)
    prefix_by_calendar = {
        CALENDAR_SSE: "sh",
        CALENDAR_SZSE: "sz",
        CALENDAR_BSE: "bj",
    }
    prefix = prefix_by_calendar.get(calendar_id)
    if prefix is None:
        raise ValueError(
            f"Unknown/unallocated exchange for stock code {code!r}; "
            "refusing to guess a provider market prefix"
        )
    return f"{prefix}{code}"


class CalibrationDataCollector:
    """校准数据采集器 — 批量获取历史日线数据并缓存

    使用 FIR-23 MultiSourceDataProvider 获取 qfq 前复权数据（用于技术分析）。
    同时获取上证指数数据用于 market regime 判断。
    数据缓存到 data/calibration/ 目录，避免重复拉取。
    """

    def __init__(self, cache_dir: str = "data/calibration", use_cache: bool = True):
        self.cache_dir = cache_dir
        self.use_cache = use_cache
        self._provider: Optional[MultiSourceDataProvider] = None

    # ------------------------------------------------------------------
    # provider 懒加载（避免 import / 离线测试时构造网络客户端）
    # ------------------------------------------------------------------
    @property
    def provider(self) -> MultiSourceDataProvider:
        if self._provider is None:
            self._provider = MultiSourceDataProvider()
        return self._provider

    # ------------------------------------------------------------------
    # 缓存工具
    # ------------------------------------------------------------------
    def _ensure_cache_dir(self) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)

    def _cache_path(self, name: str) -> str:
        return os.path.join(self.cache_dir, f"{name}_qfq.json")

    def _load_cache(self, path: str) -> Optional[Tuple[List[OHLCV], dict]]:
        """读取缓存，返回 (klines, provenance_dict)。

        新格式：{"provenance": {...}, "klines": [...]}
        旧格式：纯 OHLCV 列表 —— 兼容加载，provenance 用 UNKNOWN_PROVENANCE 占位。
        缓存损坏或未启用缓存 → 返回 None。
        """
        if not (self.use_cache and os.path.exists(path)):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            # 缓存损坏则忽略，重新拉取
            return None

        # 新格式：dict 带 klines / provenance
        if isinstance(data, dict) and "klines" in data:
            klines = [OHLCV.from_dict(d) for d in data["klines"]]
            prov = data.get("provenance")
            if not isinstance(prov, dict):
                prov = dict(UNKNOWN_PROVENANCE)
            return klines, dict(prov)

        # 旧格式：纯列表
        if isinstance(data, list):
            klines = [OHLCV.from_dict(d) for d in data]
            return klines, dict(UNKNOWN_PROVENANCE)

        return None

    def _save_cache(self, path: str, klines: List[OHLCV],
                    provenance: Optional[dict] = None) -> None:
        if not self.use_cache:
            return
        self._ensure_cache_dir()
        payload = {
            "provenance": dict(provenance) if provenance else dict(UNKNOWN_PROVENANCE),
            "klines": [k.to_dict() for k in klines],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    # ------------------------------------------------------------------
    # provenance 提取（P0-2 / FIR-23 canonical）
    # ------------------------------------------------------------------
    @staticmethod
    def _build_provenance(series: Optional[MarketDataSeries]) -> dict:
        """从 MarketDataSeries 提取完整 canonical provenance 元数据。

        无法获取的字段一律标记为 "unknown" / None / 0，绝不硬编码假值。
        data_quality 为详细 dict：status 由 fail-closed 规则推导，
        子指标直接镜像 DataQualityReport。
        """
        if series is None:
            return dict(UNKNOWN_PROVENANCE)

        def _safe(fn, default="unknown"):
            try:
                val = fn()
                return val if val not in (None, "") else default
            except Exception:
                return default

        # ---- 标量元数据 ----
        data_source = _safe(
            lambda: series.source.value if series.source is not None else None)
        adjustment = _safe(
            lambda: series.adjustment.value if series.adjustment is not None else None)
        frequency = _safe(
            lambda: series.frequency.value if series.frequency is not None else None)
        schema_version = _safe(lambda: series.schema_version)
        trading_calendar = _safe(lambda: series.trading_calendar)
        fetched_at = _safe(lambda: series.fetched_at)
        last_bar_date = _safe(lambda: series.last_bar_date)

        # ---- bars 派生：bar_count / date_range / provenance_hash ----
        try:
            bars = list(series.bars or [])
        except Exception:
            bars = []
        bar_count = len(bars)
        if bars:
            date_range = {"start": bars[0].date, "end": bars[-1].date}
        else:
            date_range = {"start": "unknown", "end": "unknown"}

        provenance_hash = "unknown"
        if bars:
            try:
                h = bars[0].provenance.raw_payload_hash
                if h:
                    provenance_hash = h
            except Exception:
                provenance_hash = "unknown"

        # ---- quality_report ----
        qr = getattr(series, "quality_report", None)
        quality_detail = None
        if qr is not None:
            try:
                quality_detail = qr.to_dict()
            except Exception:
                quality_detail = None

        # ---- data_quality fail-closed（核心）----
        # 无 quality_report → status="unknown"，所有子字段为 None。
        # 任一异常信号触发即在 status 中追加对应 tag；多项用逗号连接；
        # 全部正常 → status="OK"。
        if qr is None:
            data_quality: Dict[str, object] = {
                "status": "unknown",
                "missing_days": None,
                "suspended_days": None,
                "ohlc_violations": None,
                "adjustment_consistency": "unknown",
                "source_consistency": "unknown",
                "anomalies_count": None,
                "completeness_ratio": None,
            }
        else:
            missing_days = getattr(qr, "missing_days", 0) or 0
            unknown_missing_days = getattr(qr, "unknown_missing_days", 0) or 0
            suspended_days = getattr(qr, "suspended_days", 0) or 0
            anomalies = getattr(qr, "anomalies", None) or []
            cr = getattr(qr, "completeness_ratio", None)
            adj_consistent = getattr(qr, "adjustment_consistent", False)
            src_consistency = getattr(qr, "source_consistency", None)

            issues = []
            if not adj_consistent:
                issues.append("adjustment_unverified")
            # FIR-23 source-consistency policy (fail-closed):
            #   source_consistency = 1.0 - avg_diff (multi-source cross-validation)
            #   None  → unknown (single-source, cannot verify) → fail-closed
            #   < 0.95 → inconsistent (cross-source diff > 5%)
            #   >= 0.95 → consistent
            if src_consistency is None:
                issues.append("source_consistency_unknown")
            elif src_consistency < 0.95:
                issues.append("source_inconsistent")
            if missing_days > 0 or unknown_missing_days > 0:
                issues.append("has_missing")
            if suspended_days > 0:
                issues.append("has_suspension")
            if anomalies:
                issues.append("has_anomalies")
            if cr is not None and cr < 0.95:
                issues.append("low_completeness")
            status = ",".join(issues) if issues else "OK"

            data_quality = {
                "status": status,
                "missing_days": missing_days,
                "suspended_days": suspended_days,
                "ohlc_violations": None,
                "adjustment_consistency": "consistent" if adj_consistent else "unverified",
                "source_consistency": getattr(qr, "source_consistency", None),
                "anomalies_count": len(anomalies),
                "completeness_ratio": cr,
            }

        return {
            "data_source": data_source,
            "adjustment": adjustment,
            "frequency": frequency,
            "schema_version": schema_version,
            "trading_calendar": trading_calendar,
            "fetched_at": fetched_at,
            "last_bar_date": last_bar_date,
            "bar_count": bar_count,
            "date_range": date_range,
            "data_quality": data_quality,
            "quality_detail": quality_detail,
            "provenance_hash": provenance_hash,
        }

    # ------------------------------------------------------------------
    # 单只股票
    # ------------------------------------------------------------------
    def fetch_stock_data_with_provenance(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> Tuple[List[OHLCV], dict]:
        """获取单只股票的 qfq 前复权日线数据及其真实 provenance（P0-2）。

        - 先查缓存（新格式含 provenance；旧格式纯列表视为 unknown 占位）
        - 未命中则通过 MultiSourceDataProvider.fetch_klines 获取
        - 从返回的 MarketDataSeries 提取真实 data_source/adjustment/data_quality
        - 返回 (klines, provenance_dict)
        """
        cache_path = self._cache_path(f"{code}_{start_date}_{end_date}")
        cached = self._load_cache(cache_path)
        if cached is not None:
            return cached

        symbol = to_market_symbol(code)
        series = self.provider.fetch_klines(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjustment=AdjustmentType.FORWARD,
        )
        klines = series.to_ohlcv_list()
        provenance = self._build_provenance(series)
        self._save_cache(cache_path, klines, provenance)
        return klines, provenance

    def fetch_stock_data(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCV]:
        """获取单只股票的 qfq 前复权日线数据（向后兼容，仅返回 klines）"""
        klines, _ = self.fetch_stock_data_with_provenance(code, start_date, end_date)
        return klines

    # ------------------------------------------------------------------
    # 指数
    # ------------------------------------------------------------------
    def fetch_index_data(
        self,
        symbol: str = "sh000001",
        start_date: str = "2023-01-01",
        end_date: str = "2026-10-01",
    ) -> List[OHLCV]:
        """获取指数数据（用于 market regime 判断）

        指数本身无除权，使用 AdjustmentType.NONE。
        """
        cache_path = self._cache_path(f"index_{symbol}_{start_date}_{end_date}")
        cached = self._load_cache(cache_path)
        if cached is not None:
            return cached[0]

        series = self.provider.fetch_klines(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjustment=AdjustmentType.NONE,
        )
        klines = series.to_ohlcv_list()
        self._save_cache(cache_path, klines, self._build_provenance(series))
        return klines

    # ------------------------------------------------------------------
    # 批量
    # ------------------------------------------------------------------
    def fetch_batch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        delay_seconds: float = 0.5,
    ) -> Dict[str, List[OHLCV]]:
        """批量获取多只股票数据，带延迟防限流"""
        result: Dict[str, List[OHLCV]] = {}
        for idx, code in enumerate(codes):
            result[code] = self.fetch_stock_data(code, start_date, end_date)
            # 非最后一只才延迟，避免多余等待
            if delay_seconds > 0 and idx < len(codes) - 1:
                time.sleep(delay_seconds)
        return result

    def fetch_batch_with_provenance(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        delay_seconds: float = 0.5,
    ) -> Dict[str, Tuple[List[OHLCV], dict]]:
        """批量获取多只股票数据，返回 {code: (klines, provenance_dict)}"""
        result: Dict[str, Tuple[List[OHLCV], dict]] = {}
        for idx, code in enumerate(codes):
            result[code] = self.fetch_stock_data_with_provenance(
                code, start_date, end_date)
            if delay_seconds > 0 and idx < len(codes) - 1:
                time.sleep(delay_seconds)
        return result

    # ------------------------------------------------------------------
    # market regime 判断（point-in-time）
    # ------------------------------------------------------------------
    @staticmethod
    def _ma(closes: List[float], end_idx: int, period: int) -> Optional[float]:
        """计算到 end_idx 为止（含 end_idx）的 period 日均线。

        数据不足 period 根时返回 None。
        """
        start = end_idx - period + 1
        if start < 0:
            return None
        window = closes[start:end_idx + 1]
        if len(window) < period:
            return None
        return sum(window) / period

    def determine_market_regime(
        self,
        index_klines: List[OHLCV],
        date: str,
    ) -> MarketRegime:
        """判断某一天的大盘环境（基于上证指数，严格 point-in-time）

        规则：
        - 计算 MA60（60日均线，到 date 为止）
        - BULL: 收盘价 > MA60 且 MA60 向上（今日MA60 > 5日前MA60）
        - BEAR: 收盘价 < MA60 且 MA60 向下（今日MA60 < 5日前MA60）
        - RANGE: 其他情况
        - 数据不足60天（或无法计算 5 日前 MA60）→ RANGE

        防 look-ahead：只使用 date 及之前的数据。
        """
        if not index_klines:
            return MarketRegime.RANGE

        # 找到 date 在序列中的位置：最后一根 date <= 给定 date 的K线
        pos: Optional[int] = None
        for i, k in enumerate(index_klines):
            if k.date <= date:
                pos = i
            else:
                break
        if pos is None:
            return MarketRegime.RANGE

        closes = [k.close for k in index_klines]

        # 需要至少 60 根才能算 MA60
        ma60 = self._ma(closes, pos, REGIME_MA_PERIOD)
        if ma60 is None:
            return MarketRegime.RANGE

        # 需要 5 日前的 MA60 来判断方向；即 pos 处还需再往前 5 根也能成 MA60
        ma60_prior = self._ma(closes, pos - REGIME_MA_LOOKBACK, REGIME_MA_PERIOD)
        if ma60_prior is None:
            return MarketRegime.RANGE

        close = closes[pos]
        ma_rising = ma60 > ma60_prior
        ma_falling = ma60 < ma60_prior

        if close > ma60 and ma_rising:
            return MarketRegime.BULL
        if close < ma60 and ma_falling:
            return MarketRegime.BEAR
        return MarketRegime.RANGE

    # ------------------------------------------------------------------
    def clear_cache(self) -> None:
        """清除缓存"""
        if not os.path.isdir(self.cache_dir):
            return
        for name in os.listdir(self.cache_dir):
            if name.endswith("_qfq.json"):
                try:
                    os.remove(os.path.join(self.cache_dir, name))
                except OSError:
                    pass