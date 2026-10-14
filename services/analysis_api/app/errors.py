"""结构化错误：code 机器可读，message 中文可读；绝不包含密钥"""
from __future__ import annotations

from typing import Any, Dict, Optional


class ApiError(Exception):
    status = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None, status: Optional[int] = None,
                 code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if status:
            self.status = status
        if code:
            self.code = code


class InvalidRequest(ApiError):
    status, code = 422, "invalid_request"


class NotFound(ApiError):
    status, code = 404, "not_found"


class Conflict(ApiError):
    status, code = 409, "conflict"


class PreconditionFailed(ApiError):
    status, code = 412, "precondition_failed"


class UpstreamUnavailable(ApiError):
    """行情源不可用（fail-closed，不编造数据）"""
    status, code = 503, "market_data_unavailable"


class Unauthorized(ApiError):
    status, code = 401, "unauthorized"
