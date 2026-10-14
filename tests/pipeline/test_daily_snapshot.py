"""每日快照：用 fixture 行情 + 假同花顺源跑完整流程，检查输出文件与 API 响应一致、报告可读。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "services" / "analysis_api"
for p in (str(ROOT), str(SERVICE)):
    if p not in sys.path:
        sys.path.insert(0, p)

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from app.providers import FixtureProvider  # noqa: E402
from app.ths_web import SectorSourceError, SectorMembers  # noqa: E402
from pipeline import daily_snapshot as D  # noqa: E402


class FakeSource:
    def fetch(self, code):
        if code == "886099":
            raise SectorSourceError("同花顺接口拒绝了板块 886099 的请求（HTTP 403）")
        return SectorMembers(code, "测试概念", ["002436", "600001"], "https://example.invalid", "2026-10-14T08:00:00Z")


def test_snapshot_outputs_and_report(tmp_path):
    cfg = {"watchlist": ["002436", "300999"], "ths_sectors": ["886032", "886099"], "preset_sectors": []}
    out = tmp_path / "data"
    snap = D.build(cfg, out, log=lambda *a: None, provider=FixtureProvider(SERVICE / "fixtures"),
                   sector_source=FakeSource())
    assert [s["id"] for s in snap["scopes"]] == ["watchlist", "ths:886032"]
    assert snap["sector_fetch"][1]["ok"] is False and "HTTP 403" in snap["sector_fetch"][1]["error"]
    wl = json.loads((out / snap["scopes"][0]["file"]).read_text(encoding="utf-8"))["results"]
    assert [i["code"] for i in wl["items"]] == ["002436"] and wl["failures"][0]["code"] == "300999"
    doc = json.loads((out / "analyses" / "002436.json").read_text(encoding="utf-8"))["analysis"]
    assert doc["code"] == "002436" and len(doc["core"]["patterns"]) == 8
    for f in ("market", "calibration", "meta_patterns", "version", "watchlist", "sectors", "snapshot"):
        assert (out / f"{f}.json").exists(), f
    sectors = json.loads((out / "sectors.json").read_text(encoding="utf-8"))
    assert {d["sectorCode"]: d["status"] for d in sectors["dynamic"]} == {"886032": "verified", "886099": "pending"}
    text = D.report(out, snap, recent_bars=250, min_score=0)
    assert "## 自选股" in text and "测试概念（同花顺 886032）" in text and "886099 拉取失败" in text
    assert "不构成投资建议" in text
