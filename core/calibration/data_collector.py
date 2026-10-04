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
from typing import List, Optional, Dict

from core.types import OHLCV, AdjustmentType
from core.market_data import MultiSourceDataProvider
from core.calibration.types import MarketRegime


# MA60 窗口（交易日）
REGIME_MA_PERIOD = 60
# MA60 方向判断的回看天数（"5日前MA60"）
REGIME_MA_LOOKBACK = 5


def to_market_symbol(code: str) -> str:
    """6位股票代码 → 带市场前缀的 symbol

    规则：6 开头（沪市主板/科创板/沪B）→ sh；0/3 开头（深市主板/创业板）→ sz。
    """
    code = code.strip()
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


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

    def _load_cache(self, path: str) -> Optional[List[OHLCV]]:
        if not (self.use_cache and os.path.exists(path)):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [OHLCV.from_dict(d) for d in data]
        except Exception:
            # 缓存损坏则忽略，重新拉取
            return None

    def _save_cache(self, path: str, klines: List[OHLCV]) -> None:
        if not self.use_cache:
            return
        self._ensure_cache_dir()
        with open(path, "w", encoding="utf-8") as f:
            json.dump([k.to_dict() for k in klines], f, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 单只股票
    # ------------------------------------------------------------------
    def fetch_stock_data(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCV]:
        """获取单只股票的 qfq 前复权日线数据

        - 先查缓存（data/calibration/{code}_{start}_{end}_qfq.json）
        - 缓存命中则直接加载
        - 未命中则通过 MultiSourceDataProvider.fetch_klines 获取
        - 保存到缓存
        - 返回 List[OHLCV]（通过 MarketDataSeries.to_ohlcv_list()）
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
        self._save_cache(cache_path, klines)
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
            return cached

        series = self.provider.fetch_klines(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjustment=AdjustmentType.NONE,
        )
        klines = series.to_ohlcv_list()
        self._save_cache(cache_path, klines)
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
