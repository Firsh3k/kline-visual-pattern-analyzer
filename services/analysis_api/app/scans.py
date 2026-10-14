"""
批量扫描任务（自选股批量分析 / 市场扫描，FIR-22 / FIR-29 / FIR-31 §6B-C）

- 有界并发（线程池），单只超时，进度、失败清单（含失败类型）、去重、板块归属、可取消；
- 每只股票都调用 AnalysisService.analyze_code()（与个股页同一路径），摘要来自 core.analysis.pattern_summary；
- 过滤只作用于 Python 返回的形态结果字段（方向 / 分数 / 形态 / 阶段 / 距今K线数），不做任何二次判断；
- 不计算任何综合推荐分（FIR-32 D4）。
"""
from __future__ import annotations

import json
import threading
import uuid
import time
from concurrent.futures import FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, wait
from typing import Any, Dict, List, Optional

from core import analysis as A

from .analysis_service import AnalysisService
from .db import Database, dumps
from .errors import ApiError, Conflict, InvalidRequest, NotFound
from .userstate import fmt, utcnow, valid_code

STAGES = ("forming", "pre_break", "breakout_day", "post_break", "retest", "failed_break")
DIRECTIONS = ("bull", "bear", "any")


def parse_filters(f: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    f = dict(f or {})
    out = {"direction": f.get("direction", "any"), "minScore": f.get("minScore", 0),
           "patterns": f.get("patterns") or list(A.PATTERN_ORDER), "stages": f.get("stages") or list(STAGES),
           "maxBarsSince": f.get("maxBarsSince"), "categories": f.get("categories") or ["price_pattern", "trend_state"]}
    if out["direction"] not in DIRECTIONS:
        raise InvalidRequest("filters.direction 只能是 bull / bear / any")
    if not isinstance(out["minScore"], (int, float)) or not 0 <= out["minScore"] <= 100:
        raise InvalidRequest("filters.minScore 需在 0~100")
    if not set(out["patterns"]) <= set(A.PATTERN_ORDER):
        raise InvalidRequest(f"filters.patterns 只能取 {A.PATTERN_ORDER}")
    if not set(out["stages"]) <= set(STAGES):
        raise InvalidRequest(f"filters.stages 只能取 {STAGES}")
    if not set(out["categories"]) <= {"price_pattern", "trend_state"}:
        raise InvalidRequest("filters.categories 只能取 price_pattern / trend_state")
    mb = out["maxBarsSince"]
    if mb is not None and (not isinstance(mb, int) or not 0 <= mb <= 250):
        raise InvalidRequest("filters.maxBarsSince 需在 0~250")
    return out


def match(summary: Dict[str, Any], f: Dict[str, Any]) -> List[str]:
    """返回满足过滤条件的形态 id 列表（空 = 不匹配）"""
    hits = []
    for pid, p in summary["patterns_latest"].items():
        if pid not in f["patterns"] or p["stage"] not in f["stages"] or p["category"] not in f["categories"]:
            continue
        if f["direction"] != "any" and p["direction"] != f["direction"]:
            continue
        if p["score"] < f["minScore"]:
            continue
        if f["maxBarsSince"] is not None and p["bars_since"] > f["maxBarsSince"]:
            continue
        hits.append(pid)
    return hits


class ScanService:
    def __init__(self, db: Database, analysis: AnalysisService, sectors, *, concurrency: int, max_codes: int,
                 per_code_timeout_s: int):
        self.db, self.analysis, self.sectors = db, analysis, sectors
        self.max_codes, self.timeout = max_codes, per_code_timeout_s
        self._pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="scan")
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._concurrency = concurrency
        # 服务重启时把未完成的任务标为 interrupted（不假装完成）
        with self.db.tx() as c:
            for r in c.execute("SELECT job_id, state FROM scan_jobs").fetchall():
                st = json.loads(r["state"])
                if st["status"] in ("queued", "running"):
                    st.update(status="interrupted", finishedAt=fmt(utcnow()), message="服务重启，任务中断，请重新发起")
                    c.execute("UPDATE scan_jobs SET state=? WHERE job_id=?", (dumps(st), r["job_id"]))

    # ------------------------------------------------------------ 创建
    def create(self, user_id: str, body: Dict[str, Any], watchlist_codes: List[str]) -> Dict[str, Any]:
        scope = body.get("scope") or {}
        kind = scope.get("type")
        adjustment = body.get("adjustment", "qfq")
        if adjustment not in ("qfq", "none"):
            raise InvalidRequest("adjustment 只能是 qfq / none")
        as_of = body.get("asOf")
        if as_of is not None and not A._DATE_RE.match(str(as_of)):
            raise InvalidRequest("asOf 必须为 YYYY-MM-DD")
        filters = parse_filters(body.get("filters"))
        membership: Dict[str, List[str]] = {}
        order: List[str] = []
        invalid: List[str] = []

        def add(code, label):
            try:
                valid_code(code)
            except Exception:
                invalid.append(code)
                return
            if code not in membership:
                membership[code] = []
                order.append(code)
            if label not in membership[code]:
                membership[code].append(label)

        if kind == "sectors":
            ids = scope.get("ids")
            if not isinstance(ids, list) or not ids or len(ids) > 50:
                raise InvalidRequest("scope.ids 需为 1~50 个板块 id")
            for s in self.sectors.resolve_scope(user_id, ids):     # 不可扫描的动态板块在这里 409
                for c in s["codes"]:
                    add(c, s["name"])
        elif kind == "codes":
            codes = scope.get("codes")
            if not isinstance(codes, list) or not codes:
                raise InvalidRequest("scope.codes 需为非空数组")
            for c in codes:
                add(str(c).strip(), "自定义股票池")
        elif kind == "watchlist":
            if not watchlist_codes:
                raise InvalidRequest("自选股为空")
            for c in watchlist_codes:
                add(c, "自选股")
        else:
            raise InvalidRequest("scope.type 只能是 sectors / codes / watchlist")
        if not order:
            raise InvalidRequest("扫描范围内没有有效股票代码", details={"invalid": invalid[:50]})
        if len(order) > self.max_codes:
            raise InvalidRequest(f"去重后 {len(order)} 只，超过单次上限 {self.max_codes}")

        job_id = uuid.uuid4().hex
        state = {"jobId": job_id, "status": "queued", "createdAt": fmt(utcnow()), "startedAt": None, "finishedAt": None,
                 "total": len(order), "done": 0, "succeeded": 0, "failed": 0, "matched": 0,
                 "failures": [], "invalidCodes": invalid, "concurrency": self._concurrency,
                 "perCodeTimeoutS": self.timeout, "dedupedFrom": sum(len(v) for v in membership.values())}
        request = {"scope": scope, "adjustment": adjustment, "asOf": as_of, "filters": filters}
        job = {"user": user_id, "state": state, "request": request, "order": order, "membership": membership,
               "results": {}, "started": {}, "cancel": threading.Event()}
        with self._lock:
            self._jobs[job_id] = job
        self._persist(job, create=True)
        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"scanjob-{job_id[:8]}").start()
        return self.status(user_id, job_id)

    # ------------------------------------------------------------ 执行
    def _one(self, job, code):
        job["started"][code] = time.monotonic()
        if job["cancel"].is_set():
            raise _Cancelled()
        req = job["request"]
        doc = self.analysis.analyze_code(code, req["adjustment"], req["asOf"])
        s = A.pattern_summary(doc)
        return {**s, "sectors": job["membership"][code], "matchedPatterns": match(s, req["filters"])}

    def _record(self, job, code, fut=None, exc=None):
        st = job["state"]
        if exc is None:
            try:
                res = fut.result()
                job["results"][code] = res
                st["succeeded"] += 1
                if res["matchedPatterns"]:
                    st["matched"] += 1
            except BaseException as e:   # noqa: BLE001 —— 统一归类
                exc = e
        if exc is not None:
            if isinstance(exc, (_Cancelled, CancelledError)):
                f = {"code": code, "kind": "cancelled", "error": "任务已取消"}
            elif isinstance(exc, _TimedOut):
                f = {"code": code, "kind": "timeout", "error": f"超过 {self.timeout}s 未完成"}
            elif isinstance(exc, ApiError):
                f = {"code": code, "kind": exc.code, "error": exc.message, "status": exc.status}
            elif isinstance(exc, A.InvalidInputError):
                f = {"code": code, "kind": "invalid_request", "error": str(exc), "status": 422}
            else:
                f = {"code": code, "kind": "internal_error", "error": f"{type(exc).__name__}: {exc}"}
            st["failures"].append(f)
            st["failed"] += 1
        st["done"] += 1

    def _run(self, job):
        st = job["state"]
        st.update(status="running", startedAt=fmt(utcnow()))
        try:
            self.analysis.prefetch_names(job["order"])
        except Exception:
            pass
        futs = {self._pool.submit(self._one, job, code): code for code in job["order"]}
        pending = set(futs)
        last_persist = time.monotonic()
        while pending:
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for fut in done:
                self._record(job, futs[fut], fut)
            now = time.monotonic()
            for fut in list(pending):
                code = futs[fut]
                t0 = job["started"].get(code)
                if job["cancel"].is_set() and fut.cancel():
                    pending.discard(fut)
                    self._record(job, code, exc=_Cancelled())
                elif t0 is not None and now - t0 > self.timeout:
                    pending.discard(fut)          # 线程无法强杀：放弃其结果并记为超时
                    self._record(job, code, exc=_TimedOut())
            if now - last_persist > 1.0:
                self._persist(job)
                last_persist = now
        st.update(status="cancelled" if job["cancel"].is_set() else "completed", finishedAt=fmt(utcnow()))
        self._persist(job)

    def _persist(self, job, create=False):
        st, rid = job["state"], job["state"]["jobId"]
        ordered = [job["results"][c] for c in job["order"] if c in job["results"]]
        with self.db.tx() as c:
            if create:
                c.execute("INSERT INTO scan_jobs(job_id, user_id, created_at, request, state, results) VALUES(?,?,?,?,?,?)",
                          (rid, job["user"], st["createdAt"], dumps(job["request"]), dumps(st), dumps(ordered)))
            else:
                c.execute("UPDATE scan_jobs SET state=?, results=? WHERE job_id=?", (dumps(st), dumps(ordered), rid))

    # ------------------------------------------------------------ 查询
    def _load(self, user_id, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None and job["user"] == user_id:
            return job["state"], job["request"], [job["results"][c] for c in job["order"] if c in job["results"]]
        rows = self.db.query("SELECT state, request, results FROM scan_jobs WHERE job_id=? AND user_id=?", (job_id, user_id))
        if not rows:
            raise NotFound("扫描任务不存在")
        return json.loads(rows[0]["state"]), json.loads(rows[0]["request"]), json.loads(rows[0]["results"])

    def status(self, user_id, job_id):
        st, req, _ = self._load(user_id, job_id)
        return {**st, "request": req, "progress": round(st["done"] / st["total"], 4) if st["total"] else 1.0}

    def results(self, user_id, job_id, matched_only=False):
        st, req, res = self._load(user_id, job_id)
        items = [r for r in res if r["matchedPatterns"]] if matched_only else res
        return {"jobId": job_id, "status": st["status"], "total": st["total"], "done": st["done"],
                "matched": st["matched"], "failures": st["failures"], "filters": req["filters"], "items": items}

    def cancel(self, user_id, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None or job["user"] != user_id:
            self._load(user_id, job_id)
            raise Conflict("任务已结束，无法取消")
        if job["state"]["status"] not in ("queued", "running"):
            raise Conflict("任务已结束，无法取消")
        job["cancel"].set()
        return self.status(user_id, job_id)

    def list(self, user_id, limit=20):
        rows = self.db.query("SELECT state FROM scan_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit))
        out = []
        for r in rows:
            st = json.loads(r["state"])
            with self._lock:
                live = self._jobs.get(st["jobId"])
            out.append(live["state"] if live else st)
        return out

    def shutdown(self):
        for job in list(self._jobs.values()):
            job["cancel"].set()
        self._pool.shutdown(wait=False, cancel_futures=True)


class _Cancelled(Exception):
    pass


class _TimedOut(Exception):
    pass
