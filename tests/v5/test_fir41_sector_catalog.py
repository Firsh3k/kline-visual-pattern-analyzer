"""FIR-41 第 4 项：板块清单生成与核对流程（在临时副本里跑，不改仓库数据）

核对流程：权威快照 → build_sector_catalog.py --adopt-snapshot（成分写回 new_sectors.js）
→ refresh_sector_membership.py --reconcile-with-snapshot（写入权威核对日期与结果）→ 重新生成 catalog.json。
完全一致的板块在 catalog 里有 reconciled_at 且 local_only=0、source_only=0（服务端据此显示“已核对”）。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "scripts" / "build_sector_catalog.py"
REFRESH = ROOT / "scripts" / "refresh_sector_membership.py"


def run(*args, check=True):
    r = subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"{args} exit {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


@pytest.fixture
def work(tmp_path):
    d = tmp_path / "sector_data"
    shutil.copytree(ROOT / "sector_data", d)
    return d


def _catalog(work, tmp_path):
    out = tmp_path / "catalog.json"
    run(BUILD, "--sector-dir", work, "--out", out)
    return {s["name"]: s for s in json.loads(out.read_text(encoding="utf-8"))["sectors"]}


def test_repo_catalog_is_up_to_date():
    run(BUILD, "--check")


def test_current_data_is_unverified_with_source_and_date(work, tmp_path):
    cat = _catalog(work, tmp_path)
    ths = [s for s in cat.values() if s["kind"] == "ths_concept"]
    assert ths and all(s["reconciled_at"] is None and s["source"] and s["as_of"] for s in ths)
    assert all(s["source"] is None for s in cat.values() if s["kind"] == "curated_sample")


def test_ths_sector_without_provenance_is_rejected(work, tmp_path):
    (work / "provenance" / "MLCC概念_provenance.json").unlink()
    r = run(BUILD, "--sector-dir", work, "--out", tmp_path / "c.json", check=False)
    assert r.returncode == 2 and "没有来源 / 截止日的数据不接入" in r.stderr


def test_ths_sector_without_date_is_rejected(work, tmp_path):
    p = work / "provenance" / "MLCC概念_provenance.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["local_refresh_date"] = ""
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    r = run(BUILD, "--sector-dir", work, "--out", tmp_path / "c.json", check=False)
    assert r.returncode == 2 and "不是合法日期" in r.stderr


def test_check_detects_stale_catalog(work, tmp_path):
    out = tmp_path / "catalog.json"
    run(BUILD, "--sector-dir", work, "--out", out)
    run(BUILD, "--sector-dir", work, "--out", out, "--check")
    s = json.loads((work / "curated_samples.json").read_text(encoding="utf-8"))
    s["samples"][0]["constituents"].append("600999")
    (work / "curated_samples.json").write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    r = run(BUILD, "--sector-dir", work, "--out", out, "--check", check=False)
    assert r.returncode == 1 and "不一致" in r.stderr


def test_verification_flow_marks_only_exact_match_as_reconciled(work, tmp_path):
    """快照里 MLCC 与本地有差异、PCB 完全一致：adopt 后两者都完全一致；未在快照里的板块保持未核对"""
    snap = {
        "schema_version": "1.0",
        "source": "ths_ifind",
        "as_of_date": "2026-10-24",
        "snapshot_id": "test-fir41",
        "sectors": {
            "MLCC概念": {"sector_code": "886112", "codes": ["000636", "000733", "600552", "688260", "300408"]},
            "PCB概念": {"sector_code": "885959", "codes": json.loads(json.dumps(_catalog(work, tmp_path)["PCB概念"]["constituents"]))},
        },
    }
    snap_file = tmp_path / "snap.json"
    snap_file.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")

    # 只对账、不 adopt：MLCC 有差异 → 不算完全一致
    run(REFRESH, "--reconcile-with-snapshot", snap_file, "--sector-file", work / "new_sectors.js",
        "--provenance-dir", work / "provenance", "--evidence-dir", tmp_path / "evidence", "--as-of", "2026-10-24")
    cat = _catalog(work, tmp_path)
    assert cat["MLCC概念"]["reconciled_at"] == "2026-10-24"
    r = cat["MLCC概念"]["reconciliation"]
    assert r["local_only"] > 0, r                                      # 本地 15 只，快照 5 只
    assert cat["PCB概念"]["reconciliation"] == {"matched": 45, "local_only": 0, "source_only": 0}

    # adopt → 再对账：MLCC 也完全一致
    out = run(BUILD, "--sector-dir", work, "--adopt-snapshot", snap_file).stdout
    assert "MLCC概念: 15 → 5" in out
    run(REFRESH, "--reconcile-with-snapshot", snap_file, "--sector-file", work / "new_sectors.js",
        "--provenance-dir", work / "provenance", "--evidence-dir", tmp_path / "evidence", "--as-of", "2026-10-24")
    cat = _catalog(work, tmp_path)
    assert cat["MLCC概念"]["constituents"] == sorted(snap["sectors"]["MLCC概念"]["codes"])
    assert cat["MLCC概念"]["reconciliation"] == {"matched": 5, "local_only": 0, "source_only": 0}
    assert cat["MLCC概念"]["source"] == "ths_ifind" and cat["MLCC概念"]["reconciled_at"] == "2026-10-24"
    assert cat["创新药"]["reconciled_at"] is None, "快照里没有的板块保持未核对"
    # 仓库数据没被改动
    run(BUILD, "--check")
