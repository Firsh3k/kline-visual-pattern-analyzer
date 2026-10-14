"""
个股分析 / 大盘环境 —— 个股页、自选股批量、市场扫描全部经过 analyze_code()，保证同一输入同一输出。

失败后旧结果（审核项）：行情源失败时不编造、不静默返回旧数据；错误响应里附带 last_good
（上次成功结果，明确标记 stale=true、存储时间与 data_hash），由前端让用户自行选择查看。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Dict, Optional

from core import analysis as A

from .db import Database, dumps
from .errors import ApiError
from .providers import Provider
from .userstate import fmt, utcnow


class AnalysisService:
    def __init__(self, db: Database, provider: Provider, concurrency: int = 8, clock=None):
        self.db = db
        self.provider = provider
        self._sem = threading.BoundedSemaphore(concurrency)
        self._names: Dict[str, str] = {}
        self._clock = clock or (lambda: datetime.now(A.CN_TZ))

    def now(self) -> datetime:
        return self._clock()

    def name_of(self, code: str, hint: Optional[str] = None) -> str:
        if hint:
            self._names[code] = hint
        if code not in self._names:
            self._names.update(self.provider.names([code]))
        return self._names.get(code, code)

    def prefetch_names(self, codes) -> None:
        missing = [c for c in codes if c not in self._names]
        for i in range(0, len(missing), 60):
            self._names.update(self.provider.names(missing[i:i + 60]))

    def analyze_code(self, code: str, adjustment: str = "qfq", as_of: Optional[str] = None) -> Dict[str, Any]:
        if adjustment not in ("qfq", "none"):
            raise A.InvalidInputError("adjustment 只能是 qfq / none")
        A.exchange_symbol(code)
        if as_of is not None and not A._DATE_RE.match(str(as_of)):
            raise A.InvalidInputError(f"asOf 必须为 YYYY-MM-DD：{as_of!r}")
        try:
            with self._sem:
                fetched = self.provider.daily(code, adjustment)
            try:
                doc = A.analyze(code, fetched.bars, adjustment=adjustment, provenance=fetched.provenance,
                                as_of=as_of, name=self.name_of(code, fetched.name), now=self.now())
            except A.InvalidInputError as e:
                if e.issues:   # 上游行情本身不合法 → 502，拒绝计算（不修补）
                    raise ApiError(str(e), status=502, code="invalid_market_data",
                                   details={"issues": e.issues[:20], "provenance": fetched.provenance})
                raise ApiError(str(e), status=422, code="insufficient_data")
        except ApiError as e:
            if e.status >= 500:
                lg = self.last_good(code, adjustment, as_of)
                if lg is not None:
                    e.details = {**e.details, "last_good": lg}
            raise
        self._store_last_good(code, adjustment, as_of, doc)
        return doc

    def _store_last_good(self, code, adjustment, as_of, doc) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO last_good_analysis(code, adjustment, as_of_key, data_hash, doc, stored_at) "
                      "VALUES(?,?,?,?,?,?) ON CONFLICT(code, adjustment, as_of_key) DO UPDATE SET "
                      "data_hash=excluded.data_hash, doc=excluded.doc, stored_at=excluded.stored_at",
                      (code, adjustment, as_of or "", doc["input"]["data_hash"], dumps(doc), fmt(utcnow())))

    def last_good(self, code: str, adjustment: str, as_of: Optional[str]) -> Optional[Dict[str, Any]]:
        rows = self.db.query("SELECT doc, stored_at FROM last_good_analysis WHERE code=? AND adjustment=? AND as_of_key=?",
                             (code, adjustment, as_of or ""))
        if not rows:
            return None
        return {"stale": True, "stored_at": rows[0]["stored_at"],
                "note": "这是上次成功的分析结果，不是本次结果；本次行情获取失败。",
                "result": json.loads(rows[0]["doc"])}

    def market(self, as_of: Optional[str] = None) -> Dict[str, Any]:
        with self._sem:
            fetched = self.provider.index_daily()
        env = A.analyze_market(fetched.bars, as_of=as_of)
        return {"market_env": env, "provenance": fetched.provenance,
                "calendar": A.calendar_info("sh000001", env["as_of"], self.now()) if env else None}
