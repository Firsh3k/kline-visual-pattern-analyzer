"""服务配置：只从环境变量读取（密钥不进 Git / 日志 / 前端）"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+(-rc\.\d+)?$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ConfigError(RuntimeError):
    pass


def _int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} 必须是整数") from e
    if not lo <= v <= hi:
        raise ConfigError(f"{name} 必须在 {lo}..{hi} 之间")
    return v


@dataclass
class Settings:
    api_token: str
    data_dir: Path
    provider: str = "live"                      # live | fixture
    fixture_dir: Optional[Path] = None
    scan_concurrency: int = 4
    scan_max_codes: int = 600
    per_code_timeout_s: int = 25
    analysis_concurrency: int = 8
    engine_version: Optional[str] = None        # vX.Y.Z（发布时注入）
    engine_commit: Optional[str] = None         # 40 位 SHA（发布时注入）
    build_time: Optional[str] = None
    environment: str = "development"            # development | staging | production
    calibration_path: Path = field(default_factory=lambda: REPO_ROOT / "data" / "calibration" / "output" / "calibration_summary.json")
    preset_sectors_path: Path = field(default_factory=lambda: REPO_ROOT / "config" / "preset_sectors.json")

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("ANALYSIS_API_TOKEN", "")
        if len(token) < 32:
            raise ConfigError("ANALYSIS_API_TOKEN 必须设置且至少 32 个字符（服务端凭证，不得为空）")
        provider = os.environ.get("MARKET_DATA_PROVIDER", "live")
        if provider not in ("live", "fixture"):
            raise ConfigError("MARKET_DATA_PROVIDER 只能是 live / fixture")
        fixture_dir = os.environ.get("FIXTURE_DIR")
        if provider == "fixture" and not fixture_dir:
            raise ConfigError("MARKET_DATA_PROVIDER=fixture 时必须设置 FIXTURE_DIR")
        env = os.environ.get("DEPLOY_ENV", "development")
        if env not in ("development", "staging", "production"):
            raise ConfigError("DEPLOY_ENV 只能是 development / staging / production")
        if env == "production" and provider != "live":
            raise ConfigError("production 环境禁止使用 fixture 行情")
        return cls(
            api_token=token,
            data_dir=Path(os.environ.get("ANALYSIS_DATA_DIR", str(REPO_ROOT / "var"))),
            provider=provider,
            fixture_dir=Path(fixture_dir) if fixture_dir else None,
            scan_concurrency=_int("SCAN_CONCURRENCY", 4, 1, 16),
            scan_max_codes=_int("SCAN_MAX_CODES", 600, 1, 5000),
            per_code_timeout_s=_int("PER_CODE_TIMEOUT_S", 25, 1, 120),
            analysis_concurrency=_int("ANALYSIS_CONCURRENCY", 8, 1, 64),
            engine_version=os.environ.get("ENGINE_VERSION") or None,
            engine_commit=os.environ.get("ENGINE_COMMIT") or None,
            build_time=os.environ.get("BUILD_TIME") or None,
            environment=env,
        )

    def engine_identity(self) -> dict:
        """FIR-28：独立的引擎身份；元数据缺失或不合法 → verified=false（前端显示 Unknown）"""
        ok = bool(self.engine_version and _VERSION_RE.match(self.engine_version)
                  and self.engine_commit and _SHA_RE.match(self.engine_commit))
        return {
            "verified": ok,
            "version": self.engine_version if ok else None,
            "commit": self.engine_commit if ok else None,
            "build_time": self.build_time if ok else None,
            "environment": self.environment,
        }
