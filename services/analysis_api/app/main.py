"""
FastAPI 应用（FIR-31 §5 / FIR-38）

只被 Node BFF 以服务端凭证调用（Authorization: Bearer <ANALYSIS_API_TOKEN>），不面向浏览器，不开 CORS。
用户身份由 BFF 鉴权后通过 X-User-Id 传入（仅在服务凭证校验通过后才信任）。
"""
from __future__ import annotations

import hmac
import json
import logging
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import Body, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import API_SCHEMA_VERSION, SERVICE_NAME
from .config import REPO_ROOT, Settings

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import core  # noqa: E402
from core import analysis as A  # noqa: E402
from core import registry  # noqa: E402

from . import calibration  # noqa: E402
from .analysis_service import AnalysisService  # noqa: E402
from .db import Database  # noqa: E402
from .errors import ApiError, InvalidRequest, Unauthorized  # noqa: E402
from .providers import Provider, make_provider  # noqa: E402
from .scans import ScanService  # noqa: E402
from .sectors import SectorService  # noqa: E402
from . import userstate  # noqa: E402

log = logging.getLogger("analysis_api")
_REQID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SELFTEST_FIXTURE = REPO_ROOT / "tests" / "v5" / "fixtures" / "002436_qfq_20261013.csv"


# ------------------------------------------------------------------ 请求模型

class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisRequest(_M):
    code: str = Field(..., description="6 位 A 股代码")
    asOf: Optional[str] = Field(None, description="截至日期 YYYY-MM-DD（point-in-time）")
    adjustment: Literal["qfq", "none"] = "qfq"
    window: int = Field(250, description="固定 250 根交易日 K 线")


class WatchlistPatch(_M):
    ops: List[Dict[str, Any]]


class CustomSectorBody(_M):
    codes: List[str]
    at: Optional[str] = None


class DynamicSectorBody(_M):
    sectorCode: str
    at: Optional[str] = None


class VerificationBody(_M):
    snapshot: Optional[Dict[str, Any]] = None


class ScanRequest(_M):
    scope: Dict[str, Any]
    adjustment: Literal["qfq", "none"] = "qfq"
    asOf: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None


# ------------------------------------------------------------------ 应用

def create_app(settings: Optional[Settings] = None, provider: Optional[Provider] = None, clock=None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.data_dir / "analysis.sqlite3")
    provider = provider or make_provider(settings)
    analysis = AnalysisService(db, provider, settings.analysis_concurrency, clock=clock)
    sectors = SectorService(db, settings.preset_sectors_path, settings.data_dir / "sector_evidence", REPO_ROOT)
    scans = ScanService(db, analysis, sectors, concurrency=settings.scan_concurrency,
                        max_codes=settings.scan_max_codes, per_code_timeout_s=settings.per_code_timeout_s)
    started_at = userstate.fmt(userstate.utcnow())

    @asynccontextmanager
    async def lifespan(_app):
        yield
        scans.shutdown()

    app = FastAPI(title="K线形态分析服务（V6）", version=API_SCHEMA_VERSION, lifespan=lifespan,
                  description="单一 Python core 的形态分析 API。只供 Node BFF 服务端调用。",
                  docs_url="/docs", redoc_url=None, openapi_url="/openapi.json")
    app.state.settings, app.state.db, app.state.analysis = settings, db, analysis
    app.state.sectors, app.state.scans = sectors, scans

    def rid(request: Request) -> str:
        return request.state.request_id

    def uid(request: Request) -> str:
        return request.state.user_id

    @app.middleware("http")
    async def _context(request: Request, call_next):
        t0 = time.monotonic()
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if _REQID_RE.match(incoming) else uuid.uuid4().hex
        request.state.user_id = "owner"
        path = request.url.path
        status = 500
        try:
            if path.startswith("/api/") or path in ("/docs", "/openapi.json"):
                auth = request.headers.get("authorization", "")
                token = auth[7:] if auth.startswith("Bearer ") else ""
                if not token or not hmac.compare_digest(token.encode(), settings.api_token.encode()):
                    resp = _error(request.state.request_id, Unauthorized("缺少或无效的服务凭证"))
                    status = resp.status_code
                    return resp
                user = request.headers.get("x-user-id", "owner")
                if not _USER_RE.match(user):
                    resp = _error(request.state.request_id, InvalidRequest("X-User-Id 不合法"))
                    status = resp.status_code
                    return resp
                request.state.user_id = user
            resp = await call_next(request)
            status = resp.status_code
            resp.headers["X-Request-Id"] = request.state.request_id
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["X-Content-Type-Options"] = "nosniff"
            return resp
        finally:
            log.info(json.dumps({"event": "request", "requestId": request.state.request_id, "method": request.method,
                                 "path": path, "status": status, "ms": round((time.monotonic() - t0) * 1000, 1),
                                 "user": request.state.user_id}, ensure_ascii=False))

    def _error(request_id: str, e: ApiError) -> JSONResponse:
        return JSONResponse(status_code=e.status, headers={"X-Request-Id": request_id, "Cache-Control": "no-store"},
                            content={"requestId": request_id, "error": {"code": e.code, "message": e.message,
                                                                        "details": e.details}})

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, e: ApiError):
        return _error(rid(request), e)

    @app.exception_handler(A.InvalidInputError)
    async def _invalid(request: Request, e: A.InvalidInputError):
        return _error(rid(request), InvalidRequest(str(e), details={"issues": e.issues[:20]} if e.issues else None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, e: Exception):
        log.exception("unhandled error requestId=%s", rid(request))
        return _error(rid(request), ApiError("服务内部错误（已记录，可凭 requestId 排查）"))

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, e: RequestValidationError):
        errs = [{"loc": list(x.get("loc", [])), "msg": x.get("msg")} for x in e.errors()][:20]
        return _error(rid(request), InvalidRequest("请求参数不合法", details={"errors": errs}))

    def ok(request: Request, body: Dict[str, Any], status: int = 200, headers: Optional[Dict[str, str]] = None):
        return JSONResponse(status_code=status, content={"requestId": rid(request), **body}, headers=headers or {})

    # -------------------------------------------------------------- 健康
    @app.get("/healthz", tags=["health"])
    def healthz():
        return {"status": "ok", "service": SERVICE_NAME}

    @app.get("/readyz", tags=["health"])
    def readyz():
        checks = {"database": db.writable(), "core_selftest": False, "provider": provider.name,
                  "calibration_file": settings.calibration_path.exists(),
                  "preset_sectors": len(sectors.presets) == 17}
        try:
            import csv
            with open(SELFTEST_FIXTURE, newline="", encoding="utf-8") as f:
                bars = list(csv.DictReader(f))
            bars = [{**b, **{k: float(b[k]) for k in ("open", "high", "low", "close", "volume")}} for b in bars]
            doc = A.analyze("002436", bars, adjustment="qfq", provenance={"source": "selftest"},
                            now=datetime(2026, 10, 14, 16, 0, tzinfo=A.CN_TZ))
            checks["core_selftest"] = len(doc["core"]["patterns"]) == 8
        except Exception as e:  # pragma: no cover - 防御
            checks["core_selftest_error"] = str(e)[:200]
        ready = checks["database"] and checks["core_selftest"] and checks["preset_sectors"]
        return JSONResponse(status_code=200 if ready else 503,
                            content={"status": "ready" if ready else "not_ready", "checks": checks})

    # -------------------------------------------------------------- 版本
    @app.get("/api/v1/version", tags=["meta"])
    def version(request: Request):
        return ok(request, {"engine": {**settings.engine_identity(), "service": SERVICE_NAME,
                                       "core_version": core.__version__, "api_schema_version": API_SCHEMA_VERSION,
                                       "analysis_schema_version": A.ANALYSIS_SCHEMA_VERSION,
                                       "db_schema_version": db.schema_version(), "provider": provider.name,
                                       "started_at": started_at}})

    @app.get("/api/v1/meta/patterns", tags=["meta"])
    def pattern_meta(request: Request):
        from core.indicators import INDICATOR_CONVENTIONS
        items = []
        for pid in A.PATTERN_ORDER:
            d = registry.get_pattern(pid)
            items.append({"id": pid, "name": d.pattern_name})
        return ok(request, {"patterns": items, "pattern_order": A.PATTERN_ORDER,
                            "indicator_conventions": INDICATOR_CONVENTIONS, "window": A.WINDOW})

    # -------------------------------------------------------------- 分析
    @app.post("/api/v1/analyses", tags=["analysis"])
    def create_analysis(request: Request, body: AnalysisRequest):
        if body.window != A.WINDOW:
            raise InvalidRequest(f"window 固定为 {A.WINDOW}")
        doc = analysis.analyze_code(body.code, body.adjustment, body.asOf)
        return ok(request, {"analysis": doc})

    @app.get("/api/v1/market", tags=["analysis"])
    def market(request: Request, asOf: Optional[str] = Query(None)):
        return ok(request, analysis.market(asOf))

    @app.get("/api/v1/calibration", tags=["analysis"])
    def get_calibration(request: Request):
        return ok(request, calibration.load(settings.calibration_path))

    # -------------------------------------------------------------- 自选股
    @app.get("/api/v1/watchlist", tags=["watchlist"])
    def get_watchlist(request: Request):
        snap = userstate.snapshot(db, uid(request))
        return ok(request, snap, headers={"ETag": userstate.etag(snap["version"])})

    @app.patch("/api/v1/watchlist", tags=["watchlist"])
    def patch_watchlist(request: Request, body: WatchlistPatch, if_match: Optional[str] = Header(None)):
        res = userstate.apply_ops(db, uid(request), body.ops, if_match)
        snap = userstate.snapshot(db, uid(request))
        return ok(request, {**res, "state": snap}, headers={"ETag": userstate.etag(snap["version"])})

    @app.get("/api/v1/watchlist/export", tags=["watchlist"])
    def export_watchlist(request: Request):
        return ok(request, {"export": userstate.export_v12(db, uid(request), sectors.export(uid(request)))})

    # -------------------------------------------------------------- 板块
    @app.get("/api/v1/sectors", tags=["sectors"])
    def list_sectors(request: Request):
        return ok(request, sectors.list(uid(request)))

    @app.put("/api/v1/sectors/custom/{name}", tags=["sectors"])
    def put_custom(request: Request, name: str, body: CustomSectorBody):
        return ok(request, sectors.put_custom(uid(request), name, body.codes, body.at))

    @app.delete("/api/v1/sectors/custom/{name}", tags=["sectors"])
    def delete_custom(request: Request, name: str, at: Optional[str] = Query(None)):
        return ok(request, sectors.delete_custom(uid(request), name, at))

    @app.post("/api/v1/sectors/dynamic", tags=["sectors"], status_code=201)
    def add_dynamic(request: Request, body: DynamicSectorBody):
        return ok(request, sectors.add_dynamic(uid(request), body.sectorCode, body.at), status=201)

    @app.delete("/api/v1/sectors/dynamic/{code}", tags=["sectors"])
    def delete_dynamic(request: Request, code: str, at: Optional[str] = Query(None)):
        return ok(request, sectors.delete_dynamic(uid(request), code, at))

    @app.post("/api/v1/sectors/{code}/verification", tags=["sectors"])
    def verify_sector(request: Request, code: str, body: VerificationBody = Body(default_factory=VerificationBody)):
        res = sectors.verify(uid(request), code, body.snapshot)
        return ok(request, res, status=200 if res["status"] == "recorded" else 202)

    # -------------------------------------------------------------- 扫描
    @app.post("/api/v1/scans", tags=["scans"], status_code=202)
    def create_scan(request: Request, body: ScanRequest):
        wl = [w["code"] for w in userstate.snapshot(db, uid(request))["watchlist"]]
        return ok(request, {"job": scans.create(uid(request), body.model_dump(), wl)}, status=202)

    @app.get("/api/v1/scans", tags=["scans"])
    def list_scans(request: Request):
        return ok(request, {"jobs": scans.list(uid(request))})

    @app.get("/api/v1/scans/{job_id}", tags=["scans"])
    def scan_status(request: Request, job_id: str):
        return ok(request, {"job": scans.status(uid(request), job_id)})

    @app.get("/api/v1/scans/{job_id}/results", tags=["scans"])
    def scan_results(request: Request, job_id: str, matchedOnly: bool = Query(False)):
        return ok(request, scans.results(uid(request), job_id, matchedOnly))

    @app.post("/api/v1/scans/{job_id}/cancel", tags=["scans"])
    def scan_cancel(request: Request, job_id: str):
        return ok(request, {"job": scans.cancel(uid(request), job_id)})

    return app


def _configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)


def app_factory() -> FastAPI:   # uvicorn --factory app.main:app_factory
    _configure_logging()
    return create_app()
