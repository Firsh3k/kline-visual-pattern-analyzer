"""
同花顺板块成分股自动获取（ths_web）

解析：JSONP 解包、代码校验、名称、无法识别交易所的代码跳过；失败一律报错不猜测。
接入：添加板块即自动验证可扫描；拉取失败保持待验证并给出原因与手工指令；刷新；扫描前当天未拉取则重拉。
"""
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from conftest import NOW, TOKEN, make_settings
from app.main import create_app
from app.ths_web import SectorSourceError, THSWebSectorSource, URL_TEMPLATE
from core import analysis as A


def _jsonp(code, items, name="固态电池"):
    body = json.dumps({"block": {"name": name, "code": code}, "items": items}, ensure_ascii=False)
    return f"quotebridge_v2_blockrank_{code}_199112_d1000({body})".encode("utf-8")


_CN_TODAY = datetime.now(A.CN_TZ).date()   # 与服务端一致按北京时间取“今天”，避免 UTC 跨日时测试失败
TODAY = _CN_TODAY.isoformat()
RECENT = (_CN_TODAY - timedelta(days=3)).isoformat()   # 7 天内：仍可扫描


def _source(payloads, calls=None):
    """payloads: code -> bytes | Exception"""
    def get(url, timeout):
        if calls is not None:
            calls.append(url)
        code = url.split("/blockrank/")[1].split("/")[0]
        p = payloads[code]
        if isinstance(p, Exception):
            raise p
        return p
    return THSWebSectorSource(http_get=get, clock=lambda: datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc))


# ------------------------------------------------------------------ 解析

def test_parse_codes_name_and_skipped():
    src = _source({"886032": _jsonp("886032", [{"5": "002436", "55": "兴森科技"}, {"5": "600001"},
                                               {"5": "002436"}, {"5": "930001"}])})
    m = src.fetch("886032")
    assert m.codes == ["002436", "600001"] and m.name == "固态电池"
    assert m.skipped == ["930001"]                        # 无法识别交易所，不参与扫描
    assert m.source_url == URL_TEMPLATE.format(code="886032") and m.fetched_at == "2026-10-14T08:00:00Z"


@pytest.mark.parametrize("payload, msg", [
    (b"<html>forbidden</html>", "格式无法识别"),
    (b"cb({not json})", "不是合法 JSON"),
    (_jsonp("886032", []), "没有返回"),
    (_jsonp("886032", [{"5": "12345"}]), "代码不合法"),
    (urllib.error.HTTPError("u", 403, "Forbidden", {}, None), "HTTP 403"),
    (TimeoutError("timed out"), "连接同花顺失败"),
])
def test_parse_failures_fail_closed(payload, msg):
    with pytest.raises(SectorSourceError, match=msg):
        _source({"886032": payload}).fetch("886032")


# ------------------------------------------------------------------ 接入 API

@pytest.fixture
def make_client(tmp_path, provider):
    apps = []

    def make(payloads, calls=None):
        a = create_app(make_settings(tmp_path), provider=provider, clock=lambda: NOW,
                       sector_source=_source(payloads, calls))
        apps.append(a)
        c = TestClient(a)
        c.__enter__()
        c.headers.update({"Authorization": f"Bearer {TOKEN}", "X-User-Id": "owner"})
        return c
    yield make
    for a in apps:
        a.state.scans.shutdown()


def test_add_sector_auto_verifies_and_scans(make_client):
    c = make_client({"886032": _jsonp("886032", [{"5": "002436"}, {"5": "600001"}])})
    r = c.post("/api/v1/sectors/dynamic", json={"sectorCode": "886032"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["autoFetch"]["ok"] and body["autoFetch"]["count"] == 2
    sec = body["sector"]
    assert sec["status"] == "verified" and sec["scannable"] and sec["name"] == "固态电池"
    assert sec["codes"] == ["002436", "600001"] and sec["provenance"]["source"] == "ths_web"
    assert sec["provenance"]["reconciliation_method"] == "ths_web"
    assert c.post("/api/v1/scans", json={"scope": {"type": "sectors", "ids": ["ths:886032"]}}).status_code == 202


def test_fetch_failure_keeps_pending_with_reason(make_client):
    c = make_client({"886099": SectorSourceError("同花顺接口拒绝了板块 886099 的请求（HTTP 403）")})
    body = c.post("/api/v1/sectors/dynamic", json={"sectorCode": "886099"}).json()
    assert body["autoFetch"]["ok"] is False and "HTTP 403" in body["autoFetch"]["error"]
    assert body["sector"]["status"] == "pending" and body["sector"]["verification"]["instruction"]
    r = c.post("/api/v1/sectors/886099/verification", json={})
    assert r.status_code == 202 and r.json()["status"] == "manual_required" and not r.json()["autoFetch"]["ok"]
    assert c.post("/api/v1/scans", json={"scope": {"type": "sectors", "ids": ["ths:886099"]}}).status_code == 409


def test_refresh_and_scan_refetch_when_not_today(make_client):
    calls = []
    payloads = {"886032": _jsonp("886032", [{"5": "002436"}])}
    c = make_client(payloads, calls)
    # 先用 3 天前的手工快照记录（仍在 7 天内，可扫描）
    c.app.state.sectors.auto_source = None
    c.post("/api/v1/sectors/dynamic", json={"sectorCode": "886032"})
    snap = {"schema_version": "1.0", "source": "ths_ifind", "as_of_date": RECENT,
            "sectors": {"固态电池": {"sector_code": "886032", "codes": ["002436"]}}}
    c.app.state.sectors.verify("owner", "886032", snap, today=TODAY)
    c.app.state.sectors.auto_source = _source(payloads, calls)
    # 扫描前当天未拉取 → 自动重拉，成分股更新
    payloads["886032"] = _jsonp("886032", [{"5": "002436"}, {"5": "600001"}])
    assert c.post("/api/v1/scans", json={"scope": {"type": "sectors", "ids": ["ths:886032"]}}).status_code == 202
    assert len(calls) == 1
    sec = c.get("/api/v1/sectors").json()["dynamic"][0]
    assert sec["codes"] == ["002436", "600001"] and sec["provenance"]["source"] == "ths_web"
    # 今天已拉取 → 再扫描不重复拉取
    c.post("/api/v1/scans", json={"scope": {"type": "sectors", "ids": ["ths:886032"]}})
    assert len(calls) == 1
    # 手动刷新（不带快照）→ 重新拉取
    r = c.post("/api/v1/sectors/886032/verification", json={})
    assert r.status_code == 200 and r.json()["autoFetch"]["ok"] and len(calls) == 2


def test_scan_uses_recent_members_when_refetch_fails(make_client):
    c = make_client({"886032": SectorSourceError("连接同花顺失败：timed out")})
    snap = {"schema_version": "1.0", "source": "ths_ifind", "as_of_date": RECENT,
            "sectors": {"固态电池": {"sector_code": "886032", "codes": ["002436"]}}}
    c.post("/api/v1/sectors/dynamic", json={"sectorCode": "886032"})
    c.app.state.sectors.verify("owner", "886032", snap, today=TODAY)
    assert c.post("/api/v1/scans", json={"scope": {"type": "sectors", "ids": ["ths:886032"]}}).status_code == 202
