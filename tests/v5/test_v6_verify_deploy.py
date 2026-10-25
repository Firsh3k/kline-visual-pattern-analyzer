"""FIR-41 第 5 项：scripts/v6_verify_deploy.py 把线上 /api/health 与 git 版本逐项比对"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FP = _load("v6_fingerprint_vd", ROOT / "scripts" / "v6_fingerprint.py")
VD = _load("v6_verify_deploy", ROOT / "scripts" / "v6_verify_deploy.py")


def _good_health(fp):
    return {
        "status": "ok",
        "core_analyzer": "real_core",
        "core_sha256": fp["core_sha256"],
        "analyzer_sha256": fp["analyzer_sha256"],
        "watchlist_store": {"kind": "postgres", "ready": True, "error": None},
        "deploy": {
            "cwd": "/srv/app",
            "node_version": "v22.21.0",
            "page_sha256": fp["page_sha256"],
            "page_bytes": 1,
            "files_found": {"page": True, "analyzer": True, "core": True},
        },
    }


def _results(rows):
    return {name: result for name, result, _ in rows}


def test_all_pass_for_matching_health():
    fp = FP.fingerprint("HEAD")
    rows = VD.verify(_good_health(fp), fp)
    assert set(_results(rows).values()) == {"PASS"}, rows


def test_each_mismatch_fails():
    fp = FP.fingerprint("HEAD")
    cases = {
        "core_sha256": lambda h: h.update(core_sha256="0" * 64),
        "analyzer_sha256": lambda h: h.update(analyzer_sha256="0" * 64),
        "page_sha256": lambda h: h["deploy"].update(page_sha256="0" * 64),
        "runtime_files": lambda h: h["deploy"]["files_found"].update(core=False),
        "core_analyzer": lambda h: h.update(core_analyzer="error"),
        "status": lambda h: h.update(status="degraded"),
        "watchlist_store": lambda h: h["watchlist_store"].update(ready=False),
    }
    for key, mutate in cases.items():
        h = _good_health(fp)
        mutate(h)
        res = _results(VD.verify(h, fp))
        assert res[key] == "FAIL", (key, res)
        assert [k for k, v in res.items() if v != "PASS"] == [key], (key, res)


def test_watchlist_down_is_warning_when_allowed():
    fp = FP.fingerprint("HEAD")
    h = _good_health(fp)
    h["watchlist_store"] = {"kind": "unavailable", "ready": False, "error": "未配置"}
    assert _results(VD.verify(h, fp, allow_watchlist_down=True))["watchlist_store"] == "WARN"


def test_old_health_without_deploy_skips_page(tmp_path, capsys):
    fp = FP.fingerprint("HEAD")
    h = _good_health(fp)
    del h["deploy"]
    assert _results(VD.verify(h, fp))["page_sha256"] == "SKIP"
    f = tmp_path / "health.json"
    f.write_text(json.dumps(h), encoding="utf-8")
    assert VD.main(["--ref", "HEAD", "--health", str(f)]) == 0
    assert "[SKIP] page_sha256" in capsys.readouterr().out


def test_cli_exit_code(tmp_path, capsys):
    fp = FP.fingerprint("HEAD")
    f = tmp_path / "health.json"
    f.write_text(json.dumps(_good_health(fp)), encoding="utf-8")
    assert VD.main(["--ref", "HEAD", "--health", str(f)]) == 0
    bad = _good_health(fp)
    bad["deploy"]["page_sha256"] = "f" * 64
    f.write_text(json.dumps(bad), encoding="utf-8")
    assert VD.main(["--ref", "HEAD", "--health", str(f)]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] page_sha256" in out and "不通过" in out


def test_python_runtime_self_report_matches_ref_fingerprint():
    """真实 python_analyzer.py 的 health 输出能通过核对（与线上同一条路径）"""
    analyzer = _load("ab_python_analyzer_vd", ROOT / "v6" / "app-builder" / "python_analyzer.py")
    real = analyzer.health_check()
    fp = FP.fingerprint()
    h = _good_health(fp)
    h["core_sha256"], h["analyzer_sha256"] = real["core_sha256"], real["analyzer_sha256"]
    assert _results(VD.verify(h, fp))["core_sha256"] == "PASS"
    assert _results(VD.verify(h, fp))["analyzer_sha256"] == "PASS"
    sys.modules.pop("ab_python_analyzer_vd", None)
