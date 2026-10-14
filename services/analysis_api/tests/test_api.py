"""
分析服务 API 契约测试（fixture 行情 + 固定时钟）

重点：API 输出与直接调用 core.analysis 逐字段一致；个股与扫描同源；fail-closed；
自选股删除不复活；动态板块 Pending → 验证 → Verified+Fresh → 可扫描。
"""
import csv
import json
import time
from datetime import date, timedelta

import pytest

from conftest import FIXTURES, NOW, TOKEN, make_settings
from core import analysis as A


def _fixture_bars(code):
    m = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))["series"][code]
    with open(FIXTURES / m["file"], newline="", encoding="utf-8") as f:
        return [{"day": r["date"], **{k: float(r[k]) for k in ("open", "high", "low", "close", "volume")}}
                for r in csv.DictReader(f)], m


def _direct(code, as_of=None):
    bars, m = _fixture_bars(code)
    prov = {"mode": "fixture", "source": m["source"], "adjustment": m["adjustment"], "exchange": m.get("exchange"),
            "fetched_at": m.get("fetched_at"), "file": m["file"], "note": m.get("note")}
    return A.analyze(code, bars, adjustment="qfq", provenance=prov, as_of=as_of, name=m["name"], now=NOW)


# ------------------------------------------------------------------ 鉴权 / 健康

def test_health_open_api_requires_token(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        assert c.get("/healthz").json()["status"] == "ok"
        r = c.get("/readyz")
        assert r.status_code == 200 and r.json()["checks"]["core_selftest"] is True
        r = c.get("/api/v1/version")
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
        assert c.get("/api/v1/version", headers={"Authorization": "Bearer " + "x" * 40}).status_code == 401
        assert c.get("/openapi.json").status_code == 401
        assert c.get("/api/v1/version", headers={"Authorization": f"Bearer {TOKEN}", "X-User-Id": "a b"}).status_code == 422
        r = c.get("/api/v1/version", headers={"Authorization": f"Bearer {TOKEN}", "X-Request-Id": "req-12345678"})
        assert r.headers["X-Request-Id"] == "req-12345678" and r.json()["requestId"] == "req-12345678"
        assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_version_unknown_without_release_metadata(client):
    e = client.get("/api/v1/version").json()["engine"]
    assert e["verified"] is False and e["version"] is None and e["commit"] is None
    assert e["core_version"] and e["analysis_schema_version"] == A.ANALYSIS_SCHEMA_VERSION


def test_version_verified_with_valid_metadata(tmp_path, provider):
    from fastapi.testclient import TestClient
    from app.main import create_app
    s = make_settings(tmp_path, engine_version="v6.0.0-rc.1", engine_commit="a" * 40, build_time="2026-10-14T00:00:00Z",
                      environment="staging")
    app = create_app(s, provider=provider)
    with TestClient(app) as c:
        e = c.get("/api/v1/version", headers={"Authorization": f"Bearer {TOKEN}"}).json()["engine"]
    app.state.scans.shutdown()
    assert e["verified"] is True and e["commit"] == "a" * 40 and e["environment"] == "staging"


# ------------------------------------------------------------------ 分析：与 core 逐字段一致

def test_analysis_equals_direct_core_call(client):
    r = client.post("/api/v1/analyses", json={"code": "002436"})
    assert r.status_code == 200, r.text
    doc = r.json()["analysis"]
    direct = json.loads(json.dumps(_direct("002436")))
    for k in ("core", "bars", "input", "calendar", "session_state", "as_of", "symbol", "name", "schema_version"):
        assert doc[k] == direct[k], k
    assert doc["input"]["provenance"]["source"] == "tencent" and doc["input"]["adjustment"] == "qfq"
    assert doc["input"]["bar_count"] == 250 and doc["session_state"] == "close_confirmed"


def test_analysis_as_of_point_in_time(client):
    doc = client.post("/api/v1/analyses", json={"code": "002436", "asOf": "2026-09-30"}).json()["analysis"]
    assert doc["as_of"] == "2026-09-30"
    assert doc["core"] == json.loads(json.dumps(_direct("002436", "2026-09-30")))["core"]


@pytest.mark.parametrize("body,status,code", [
    ({"code": "2436"}, 422, "invalid_request"),
    ({"code": "abcdef"}, 422, "invalid_request"),
    ({"code": "002436", "window": 120}, 422, "invalid_request"),
    ({"code": "002436", "adjustment": "hfq"}, 422, "invalid_request"),
    ({"code": "002436", "adjustment": "none"}, 422, "adjustment_unavailable"),   # 不混用复权
    ({"code": "002436", "asOf": "2026/09/30"}, 422, "invalid_request"),
    ({"code": "002436", "extra": 1}, 422, "invalid_request"),
    ({"code": "920001"}, 404, "not_found"),                                    # 北交所代码可识别，但无数据
    ({"code": "000002"}, 502, "invalid_market_data"),                          # 上游含 NaN → 拒绝
    ({"code": "300999"}, 503, "market_data_unavailable"),                      # 行情源失败 → fail-closed
])
def test_analysis_errors_fail_closed(client, body, status, code):
    r = client.post("/api/v1/analyses", json=body)
    assert r.status_code == status, r.text
    err = r.json()["error"]
    assert err["code"] == code
    assert "last_good" not in err["details"]


def test_no_pattern_sample_returns_empty_patterns_not_fabricated(client):
    doc = client.post("/api/v1/analyses", json={"code": "600001"}).json()["analysis"]
    assert all(v == [] for k, v in doc["core"]["patterns"].items() if k in ("w_bottom", "m_top", "hs_bottom", "hs_top"))
    assert doc["name"].startswith("【测试】") and doc["input"]["provenance"]["source"] == "synthetic"


def test_failure_returns_last_good_explicitly_marked(client, provider):
    ok = client.post("/api/v1/analyses", json={"code": "002436"}).json()["analysis"]
    provider.manifest.setdefault("unavailable", {})["002436"] = "down"
    try:
        r = client.post("/api/v1/analyses", json={"code": "002436"})
    finally:
        provider.manifest["unavailable"].pop("002436")
    assert r.status_code == 503
    lg = r.json()["error"]["details"]["last_good"]
    assert lg["stale"] is True and lg["result"]["input"]["data_hash"] == ok["input"]["data_hash"]
    assert "不是本次结果" in lg["note"]


def test_market_env_from_core(client):
    body = client.get("/api/v1/market").json()
    bars, _ = _fixture_bars("sh000001")
    assert body["market_env"] == json.loads(json.dumps(A.analyze_market(bars)))
    assert body["calendar"]["status"] == "verified"


def test_calibration_marks_inapplicable_and_insufficient_as_null(client):
    c = client.get("/api/v1/calibration").json()
    assert c["available"] and "不是历史胜率" in c["disclaimer"]
    for g in c["groups"]:
        for m in g["metrics_by_horizon"].values():
            if not g["applicable"] or m["confidence_label"] == "insufficient":
                assert m["win_rate"] is None and m["avg_return"] is None
            assert isinstance(m["sample_size"], int)
    assert any(g["applicable"] for g in c["groups"])
    assert not any(g["applicable"] for g in c["groups"] if g["pattern_type"] in ("ma_bull", "platform_break", "all"))


# ------------------------------------------------------------------ 自选股

def _op(i, op, code=None, at=None, **kw):
    d = {"opId": f"op-{i:06d}", "op": op, **kw}
    if code:
        d["code"] = code
    if at:
        d["at"] = at
    return d


def test_watchlist_ops_tombstones_and_etag(client):
    r = client.get("/api/v1/watchlist")
    assert r.json()["version"] == 0 and r.headers["ETag"] == 'W/"v0"'
    r = client.patch("/api/v1/watchlist", json={"ops": [
        _op(1, "add", "002436", "2026-10-10T00:00:00Z"), _op(2, "add", "600519", "2026-10-10T00:00:01Z"),
        _op(3, "add", "300750", "2026-10-10T00:00:02Z", tags=["新能源"])]})
    assert r.status_code == 200 and r.json()["version"] == 1
    # 删除 600519（t=10-11），另一台设备之前（t=10-10 12:00）离线的 add 不能复活它
    client.patch("/api/v1/watchlist", json={"ops": [_op(4, "remove", "600519", "2026-10-11T00:00:00Z")]})
    r = client.patch("/api/v1/watchlist", json={"ops": [_op(5, "add", "600519", "2026-10-10T12:00:00Z")]})
    assert r.json()["results"][0] == {"opId": "op-000005", "status": "ignored", "reason": "deleted_later"}
    st = r.json()["state"]
    assert [w["code"] for w in st["watchlist"]] == ["002436", "300750"]
    assert st["tombstones"] == [{"code": "600519", "deletedAt": "2026-10-11T00:00:00.000Z"}]
    # 用户之后明确重新添加 → 允许，并清除 tombstone
    r = client.patch("/api/v1/watchlist", json={"ops": [_op(6, "add", "600519", "2026-10-12T00:00:00Z")]})
    assert "600519" in [w["code"] for w in r.json()["state"]["watchlist"]] and r.json()["state"]["tombstones"] == []
    # 幂等：同一 opId 重放不重复生效
    r = client.patch("/api/v1/watchlist", json={"ops": [_op(4, "remove", "600519", "2026-10-13T00:00:00Z")]})
    assert r.json()["results"][0]["status"] == "duplicate"
    assert "600519" in [w["code"] for w in r.json()["state"]["watchlist"]]
    # 乐观锁
    v = client.get("/api/v1/watchlist").headers["ETag"]
    r = client.patch("/api/v1/watchlist", json={"ops": [_op(7, "remove", "300750")]}, headers={"If-Match": 'W/"v1"'})
    assert r.status_code == 412 and r.json()["error"]["code"] == "precondition_failed"
    r = client.patch("/api/v1/watchlist", json={"ops": [_op(8, "remove", "300750")]}, headers={"If-Match": v})
    assert r.status_code == 200
    # 清空：只清除 at 之前加入的
    r = client.patch("/api/v1/watchlist", json={"ops": [_op(9, "clear", at="2026-10-12T00:00:00Z")]})
    assert [w["code"] for w in r.json()["state"]["watchlist"]] == []
    assert {t["code"] for t in r.json()["state"]["tombstones"]} == {"002436", "300750", "600519"}


def test_watchlist_validation_is_atomic(client):
    r = client.patch("/api/v1/watchlist", json={"ops": [_op(1, "add", "002436"), _op(2, "add", "12345")]})
    assert r.status_code == 422
    assert client.get("/api/v1/watchlist").json()["watchlist"] == []        # 整批不生效
    assert client.patch("/api/v1/watchlist", json={"ops": [{"op": "add", "code": "002436"}]}).status_code == 422
    assert client.patch("/api/v1/watchlist", json={"ops": [_op(3, "setting", key="evil", value=1)]}).status_code == 422


def test_future_client_clock_is_clamped(client):
    client.patch("/api/v1/watchlist", json={"ops": [_op(1, "add", "002436", "2099-01-01T00:00:00Z")]})
    w = client.get("/api/v1/watchlist").json()["watchlist"][0]
    assert w["addedAt"] < "2030"
    # 因此之后的删除能正常生效（不会被“未来时间”永久压制）
    r = client.patch("/api/v1/watchlist", json={"ops": [_op(2, "remove", "002436")]})
    assert r.json()["results"][0]["status"] == "applied"


def test_users_are_isolated(client):
    client.patch("/api/v1/watchlist", json={"ops": [_op(1, "add", "002436")]})
    other = client.get("/api/v1/watchlist", headers={"X-User-Id": "someone"}).json()
    assert other["watchlist"] == []


def test_export_v12_format(client):
    client.patch("/api/v1/watchlist", json={"ops": [_op(1, "add", "002436"), _op(2, "remove", "600519"),
                                                     _op(3, "setting", key="scanMinScore", value="60")]})
    e = client.get("/api/v1/watchlist/export").json()["export"]
    assert e["version"] == "1.2" and e["watchlist"] == ["002436"]
    assert [t["code"] for t in e["tombstones"]] == ["600519"] and e["settings"] == {"scanMinScore": "60"}


# ------------------------------------------------------------------ 板块

def _snapshot(code, as_of, codes, name="测试概念"):
    return {"schema_version": "1.0", "source": "ths_ifind", "as_of_date": as_of,
            "sectors": {name: {"sector_code": code, "codes": codes}}}


def test_presets_preserved_from_v5_page(client):
    s = client.get("/api/v1/sectors").json()
    assert len(s["presets"]) == 17 and all(p["scannable"] for p in s["presets"])
    names = [p["name"] for p in s["presets"]]
    assert names[:5] == ["光模块", "半导体设备", "AI算力", "新能源车", "光伏储能"]
    cxy = next(p for p in s["presets"] if p["name"] == "创新药")
    assert cxy["count"] == 106 and cxy["sectorCode"] == "886015"
    assert cxy["membership"]["freshness"] == "not_reconciled" and "尚未经权威对账" in cxy["membership"]["note"]


def test_custom_sector_crud_with_tombstone(client):
    r = client.put("/api/v1/sectors/custom/我的池", json={"codes": ["002436", "002436", "600519"], "at": "2026-10-10T00:00:00Z"})
    assert r.status_code == 200 and r.json()["codes"] == ["002436", "600519"]
    assert client.put("/api/v1/sectors/custom/我的池", json={"codes": ["12"]}).status_code == 422
    assert client.put("/api/v1/sectors/custom/光模块", json={"codes": ["002436"]}).status_code == 409
    client.delete("/api/v1/sectors/custom/我的池", params={"at": "2026-10-11T00:00:00Z"})
    r = client.put("/api/v1/sectors/custom/我的池", json={"codes": ["002436"], "at": "2026-10-10T06:00:00Z"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "deleted_later"
    assert client.get("/api/v1/sectors").json()["custom"] == []


def test_dynamic_sector_pending_verify_fresh_scan(client):
    today = date.today()
    r = client.post("/api/v1/sectors/dynamic", json={"sectorCode": "886032"})
    assert r.status_code == 201
    sec = r.json()["sector"]
    assert sec["status"] == "pending" and sec["scannable"] is False and sec["codes"] == []
    assert sec["verification"]["instruction"] == "帮我验证同花顺板块 886032"
    # Pending 禁止扫描
    r = client.post("/api/v1/scans", json={"scope": {"type": "sectors", "ids": ["ths:886032"]}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "sector_not_scannable"
    assert r.json()["error"]["details"]["verification"]["instruction"]
    # 不带快照 → 可操作的验证指令（不 dead-end）
    r = client.post("/api/v1/sectors/886032/verification", json={})
    assert r.status_code == 202 and r.json()["status"] == "manual_required"
    # 快照板块代码不符 → 拒绝
    bad = _snapshot("886099", today.isoformat(), ["002436"])
    assert client.post("/api/v1/sectors/886032/verification", json={"snapshot": bad}).status_code == 422
    # 权威快照（今天）→ verified + fresh → 可扫描
    snap = _snapshot("886032", today.isoformat(), ["002436", "600001", "002436"])
    r = client.post("/api/v1/sectors/886032/verification", json={"snapshot": snap})
    assert r.status_code == 200, r.text
    sec = r.json()["sector"]
    assert sec["status"] == "verified" and sec["scannable"] and sec["name"] == "测试概念"
    assert sec["codes"] == ["002436", "600001"] and sec["provenance"]["authoritative_reconciliation_date"] == today.isoformat()
    assert len(r.json()["evidence"]["sha256"]) == 64
    # 重复添加不会重置验证结果
    assert client.post("/api/v1/sectors/dynamic", json={"sectorCode": "886032"}).json()["sector"]["status"] == "verified"
    r = client.post("/api/v1/scans", json={"scope": {"type": "sectors", "ids": ["ths:886032"]}})
    assert r.status_code == 202
    # 过期快照（8 天前）→ stale，禁止扫描
    old = _snapshot("886032", (today - timedelta(days=8)).isoformat(), ["002436"])
    sec = client.post("/api/v1/sectors/886032/verification", json={"snapshot": old}).json()["sector"]
    assert sec["status"] == "stale" and not sec["scannable"] and sec["verification"]
    assert client.post("/api/v1/scans", json={"scope": {"type": "sectors", "ids": ["ths:886032"]}}).status_code == 409


def test_dynamic_sector_delete_no_resurrection(client):
    client.post("/api/v1/sectors/dynamic", json={"sectorCode": "886050", "at": "2026-10-10T00:00:00Z"})
    client.delete("/api/v1/sectors/dynamic/886050", params={"at": "2026-10-11T00:00:00Z"})
    r = client.post("/api/v1/sectors/dynamic", json={"sectorCode": "886050", "at": "2026-10-10T08:00:00Z"})
    assert r.status_code == 409
    assert client.get("/api/v1/sectors").json()["tombstones"]["dynamic"][0]["sectorCode"] == "886050"
    assert client.post("/api/v1/sectors/dynamic", json={"sectorCode": "88605"}).status_code == 422


# ------------------------------------------------------------------ 扫描

def _wait(client, job_id, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/v1/scans/{job_id}").json()["job"]
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("scan timeout")


def test_scan_dedupe_failures_and_parity_with_single_analysis(client):
    r = client.post("/api/v1/scans", json={"scope": {"type": "codes",
                                                     "codes": ["002436", "002436", "600001", "000002", "300999", "12x"]}})
    assert r.status_code == 202
    job = _wait(client, r.json()["job"]["jobId"])
    assert job["status"] == "completed" and job["total"] == 4 and job["done"] == 4
    assert job["invalidCodes"] == ["12x"] and job["succeeded"] == 2 and job["failed"] == 2
    kinds = {f["code"]: f["kind"] for f in job["failures"]}
    assert kinds == {"000002": "invalid_market_data", "300999": "market_data_unavailable"}
    res = client.get(f"/api/v1/scans/{job['jobId']}/results").json()
    items = {i["code"]: i for i in res["items"]}
    single = client.post("/api/v1/analyses", json={"code": "002436"}).json()["analysis"]
    s = A.pattern_summary(single)
    got = {k: v for k, v in items["002436"].items() if k not in ("sectors", "matchedPatterns")}
    assert got == json.loads(json.dumps(s))                                 # 扫描 == 个股页（同一输入同一输出）
    assert items["002436"]["sectors"] == ["自定义股票池"]


def test_scan_sector_membership_and_filters(client):
    client.put("/api/v1/sectors/custom/甲", json={"codes": ["002436", "600001"]})
    client.put("/api/v1/sectors/custom/乙", json={"codes": ["002436"]})
    r = client.post("/api/v1/scans", json={"scope": {"type": "sectors", "ids": ["custom:甲", "custom:乙"]},
                                           "filters": {"direction": "bear", "minScore": 60}})
    job = _wait(client, r.json()["job"]["jobId"])
    assert job["total"] == 2 and job["dedupedFrom"] == 3
    res = client.get(f"/api/v1/scans/{job['jobId']}/results").json()
    it = next(i for i in res["items"] if i["code"] == "002436")
    assert it["sectors"] == ["甲", "乙"]
    for pid in it["matchedPatterns"]:
        p = it["patterns_latest"][pid]
        assert p["direction"] == "bear" and p["score"] >= 60
    matched = client.get(f"/api/v1/scans/{job['jobId']}/results", params={"matchedOnly": True}).json()["items"]
    assert all(i["matchedPatterns"] for i in matched)
    assert client.post("/api/v1/scans", json={"scope": {"type": "codes", "codes": ["002436"]},
                                              "filters": {"direction": "up"}}).status_code == 422


def test_scan_watchlist_scope_and_cancel(client):
    assert client.post("/api/v1/scans", json={"scope": {"type": "watchlist"}}).status_code == 422
    client.patch("/api/v1/watchlist", json={"ops": [_op(1, "add", "002436")]})
    job = _wait(client, client.post("/api/v1/scans", json={"scope": {"type": "watchlist"}}).json()["job"]["jobId"])
    assert job["succeeded"] == 1
    assert client.post(f"/api/v1/scans/{job['jobId']}/cancel").status_code == 409
    assert client.get("/api/v1/scans/nope").status_code == 404
    assert client.get("/api/v1/scans").json()["jobs"][0]["jobId"] == job["jobId"]


def test_scan_timeout_is_reported(tmp_path, provider):
    from fastapi.testclient import TestClient
    from app.main import create_app
    orig = provider.daily

    def slow(code, adj):
        if code == "600001":
            time.sleep(2.5)
        return orig(code, adj)
    provider.daily = slow
    app = create_app(make_settings(tmp_path, per_code_timeout_s=1), provider=provider, clock=lambda: NOW)
    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {TOKEN}"})
        jid = c.post("/api/v1/scans", json={"scope": {"type": "codes", "codes": ["002436", "600001"]}}).json()["job"]["jobId"]
        job = _wait(c, jid)
    app.state.scans.shutdown()
    assert job["succeeded"] == 1 and job["failures"] == [{"code": "600001", "kind": "timeout", "error": "超过 1s 未完成"}]
