import sys
from datetime import datetime
from pathlib import Path

import pytest

SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parents[1]
for p in (str(REPO), str(SERVICE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.providers import FixtureProvider  # noqa: E402
from core import analysis as A  # noqa: E402

TOKEN = "t" * 40
NOW = datetime(2026, 10, 14, 16, 0, tzinfo=A.CN_TZ)
FIXTURES = SERVICE / "fixtures"


def make_settings(tmp_path, **kw):
    base = dict(api_token=TOKEN, data_dir=tmp_path, provider="fixture", fixture_dir=FIXTURES,
                scan_concurrency=3, scan_max_codes=200, per_code_timeout_s=20)
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def provider():
    return FixtureProvider(FIXTURES)


@pytest.fixture
def app(tmp_path, provider):
    a = create_app(make_settings(tmp_path), provider=provider, clock=lambda: NOW)
    yield a
    a.state.scans.shutdown()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {TOKEN}", "X-User-Id": "owner"})
        yield c
