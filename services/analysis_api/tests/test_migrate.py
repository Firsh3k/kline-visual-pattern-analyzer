"""非破坏性迁移：快照 + 备份 + 对账 + 幂等 + tombstone 不复活 + 失败自动回滚"""
import hashlib
import json
from pathlib import Path

import pytest

from conftest import REPO
from app import migrate as M
from app import userstate


def _run(app, tmp_path, cfg, **kw):
    src = tmp_path / "watchlist.json"
    src.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return M.migrate(app.state.db, src, tmp_path, app.state.sectors, "owner", **kw), src


def test_migrate_real_repo_config(app, tmp_path):
    src = REPO / "config" / "watchlist.json"
    raw = src.read_bytes()
    cfg = json.loads(raw)
    log = M.migrate(app.state.db, src, tmp_path, app.state.sectors, "owner")
    assert log["result"] == "ok" and log["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert src.read_bytes() == raw                                              # 源文件未被修改
    assert (tmp_path / "migrations" / log["source_snapshot"]).read_bytes() == raw   # 原样快照
    assert Path(log["db_backup"]).exists()
    st = userstate.snapshot(app.state.db, "owner")
    assert sorted(w["code"] for w in st["watchlist"]) == sorted(cfg["watchlist"])
    assert {t["code"] for t in st["tombstones"]} == {t["code"] for t in cfg["tombstones"]}
    assert st["settings"] == cfg["settings"]
    assert log["reconciliation"]["problems"] == []
    # 幂等：再跑一次不产生变化
    v = st["version"]
    log2 = M.migrate(app.state.db, src, tmp_path, app.state.sectors, "owner")
    assert userstate.snapshot(app.state.db, "owner")["version"] == v
    assert any("已于" in n for n in log2["notes"])


def test_migrate_preserves_existing_and_respects_tombstones(app, tmp_path):
    db = app.state.db
    userstate.apply_ops(db, "owner", [
        {"opId": "pre-00000001", "op": "add", "code": "600519", "at": "2026-09-01T00:00:00Z"},   # 早于源 tombstone
        {"opId": "pre-00000002", "op": "add", "code": "300750", "at": "2026-10-12T00:00:00Z"},   # 源文件没有 → 保留
    ])
    cfg = {"version": "1.2", "exportTime": "2026-10-08T00:00:00.000Z",
           "watchlist": ["002436", "000001"],
           "tombstones": [{"code": "000001", "deletedAt": "2026-10-03T00:00:00Z"},
                          {"code": "600519", "deletedAt": "2026-10-05T00:00:00Z"}],
           "customSectors": {"我的池": ["002436", "600519"]},
           "dynamicSectors": [{"type": "ths_sector_index", "sectorCode": "886032", "sectorName": "", "constituents": []}],
           "dynamicSectorTombstones": [{"sectorCode": "886050", "deletedAt": "2026-10-04T00:00:00Z"}],
           "settings": {"scanMinScore": "70", "unknownKey": 1}}
    log, _ = _run(app, tmp_path, cfg)
    st = userstate.snapshot(db, "owner")
    codes = {w["code"] for w in st["watchlist"]}
    assert codes == {"002436", "300750"}             # 000001 被源 tombstone 阻止；600519 早于源删除 → 合法删除
    assert any(s.get("code") == "000001" for s in log["skipped"])
    assert any(s.get("setting") == "unknownKey" for s in log["skipped"])
    sec = app.state.sectors.list("owner")
    assert sec["custom"][0]["name"] == "我的池"
    assert sec["dynamic"][0]["sectorCode"] == "886032" and sec["dynamic"][0]["status"] == "pending"
    assert sec["tombstones"]["dynamic"][0]["sectorCode"] == "886050"


def test_failed_reconciliation_rolls_back(app, tmp_path, monkeypatch):
    db = app.state.db
    userstate.apply_ops(db, "owner", [{"opId": "pre-00000001", "op": "add", "code": "300750"}])
    before = userstate.snapshot(db, "owner")

    def boom(*a, **k):
        raise M.MigrationError("模拟对账失败")
    monkeypatch.setattr(M, "_reconcile", boom)
    with pytest.raises(M.MigrationError):
        _run(app, tmp_path, {"version": "1.2", "exportTime": "2026-10-08T00:00:00Z", "watchlist": ["002436"]})
    assert userstate.snapshot(db, "owner") == before
    logs = list((tmp_path / "migrations").glob("*_log.json"))
    assert json.loads(logs[0].read_text(encoding="utf-8"))["result"] == "failed_rolled_back"


def test_dry_run_changes_nothing(app, tmp_path):
    log, _ = _run(app, tmp_path, {"version": "1.2", "exportTime": "2026-10-08T00:00:00Z", "watchlist": ["002436"]},
                  dry_run=True)
    assert log["planned_ops"][0]["code"] == "002436"
    assert userstate.snapshot(app.state.db, "owner")["version"] == 0
    assert not (tmp_path / "migrations").exists()


def test_rejects_unknown_version(app, tmp_path):
    with pytest.raises(M.MigrationError):
        _run(app, tmp_path, {"version": "9", "watchlist": []})
