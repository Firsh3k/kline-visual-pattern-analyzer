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
            "cwd": "/opt/bytefaas",
            "node_version": "v22.21.0",
            "page_path": "dist/client/v4/index.html",
            "page_sha256": fp["page_sha256"],
            "page_bytes": 1,
            "engine": {"source": "bundle", "bundle_sha256": fp["engine_bundle_sha256"], "file_count": 36, "error": None},
            "sector_catalog_sha256": fp["sector_catalog_sha256"],
            "files_found": {k: True for k in fp["runtime_files"]},
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
        "runtime_files": lambda h: h["deploy"]["files_found"].update(page=False),
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


def test_sector_catalog_required_when_ref_has_it(tmp_path, capsys):
    """部署时忘了同步 sector_data/：该版本仓库里有 catalog.json，线上 files_found.sector_catalog 必须为 true"""
    fp = FP.fingerprint("HEAD")
    assert "sector_catalog" in fp["runtime_files"]
    h = _good_health(fp)
    h["deploy"]["files_found"]["sector_catalog"] = False
    assert _results(VD.verify(h, fp))["runtime_files"] == "FAIL"
    del h["deploy"]["files_found"]["sector_catalog"]
    assert _results(VD.verify(h, fp))["runtime_files"] == "FAIL"
    # 更早的版本（仓库里还没有板块清单）不要求
    old = dict(fp, runtime_files=["page", "engine"])
    assert _results(VD.verify(h, old))["runtime_files"] == "PASS"


def test_engine_bundle_must_match_ref_and_come_from_bundle():
    """平台打包修复之后的版本：引擎必须来自内嵌包且 sha256 与该版本源码算出的一致；板块清单 sha256 一致"""
    fp = FP.fingerprint("HEAD")
    assert fp["engine_bundle_sha256"] and fp["runtime_files"] == ["page", "engine", "sector_catalog"]
    cases = {
        "引擎包 sha256 不对": ("engine_bundle", lambda h: h["deploy"]["engine"].update(bundle_sha256="0" * 64)),
        "引擎来自 cwd 文件": ("engine_bundle", lambda h: h["deploy"]["engine"].update(source="files", bundle_sha256=None)),
        "deploy.engine 缺失": ("engine_bundle", lambda h: h["deploy"].pop("engine")),
        "引擎不可用": ("runtime_files", lambda h: h["deploy"]["files_found"].update(engine=False)),
        "板块清单 sha256 不对": ("sector_catalog_sha256", lambda h: h["deploy"].update(sector_catalog_sha256="1" * 64)),
        "板块清单不可用": ("sector_catalog_sha256", lambda h: h["deploy"].update(sector_catalog_sha256=None)),
    }
    for label, (key, mutate) in cases.items():
        h = _good_health(fp)
        mutate(h)
        res = _results(VD.verify(h, fp))
        assert res[key] == "FAIL", (label, res)
        assert [k for k, v in res.items() if v != "PASS"] == [key], (label, res)


def test_legacy_ref_keeps_cwd_file_checks():
    """内嵌引擎包之前的版本（如 6a41532）仍按 cwd 文件核对，不要求 deploy.engine"""
    old = FP.fingerprint("6a41532")
    assert old["engine_bundle_sha256"] is None
    assert old["runtime_files"] == ["page", "analyzer", "core", "sector_catalog"]
    h = _good_health(old)
    del h["deploy"]["engine"], h["deploy"]["sector_catalog_sha256"]
    assert set(_results(VD.verify(h, old)).values()) == {"PASS"}
    h["deploy"]["files_found"]["core"] = False
    assert _results(VD.verify(h, old))["runtime_files"] == "FAIL"


def test_watchlist_down_is_warning_when_allowed():
    fp = FP.fingerprint("HEAD")
    h = _good_health(fp)
    h["watchlist_store"] = {"kind": "unavailable", "ready": False, "error": "未配置"}
    assert _results(VD.verify(h, fp, allow_watchlist_down=True))["watchlist_store"] == "WARN"


def _cli(tmp_path, health, *extra):
    f = tmp_path / "health.json"
    f.write_text(health if isinstance(health, str) else json.dumps(health), encoding="utf-8")
    return VD.main(["--ref", "HEAD", "--health", str(f), *extra])


def test_r77_1_missing_or_broken_deploy_is_fail_not_pass(tmp_path, capsys):
    """QA R77-1：Core / 执行器 / 自选股都对，但 deploy 缺失、不是对象、files_found 值不是严格 true → 一律 FAIL（exit 1）"""
    fp = FP.fingerprint("HEAD")
    cases = {}
    h = _good_health(fp); del h["deploy"]; cases["deploy 缺失"] = h
    h = _good_health(fp); h["deploy"] = "BROKEN"; cases["deploy 不是对象"] = h
    h = _good_health(fp); h["deploy"]["files_found"]["page"] = "false"; cases["files_found.page 是字符串"] = h
    h = _good_health(fp); h["deploy"]["files_found"] = ["page"]; cases["files_found 不是对象"] = h
    h = _good_health(fp); h["deploy"]["files_found"]["engine"] = 1; cases["files_found.engine 是 1"] = h
    h = _good_health(fp); h["deploy"]["page_sha256"] = None; cases["page_sha256 为空"] = h
    for label, health in cases.items():
        assert _cli(tmp_path, health) == 1, label
        out = capsys.readouterr().out
        assert "[FAIL]" in out and "结论：不通过" in out and "结论：通过" not in out, (label, out)


def test_r77_1_legacy_health_needs_explicit_opt_in_and_is_partial(tmp_path, capsys):
    fp = FP.fingerprint("HEAD")
    h = _good_health(fp)
    del h["deploy"]
    assert _cli(tmp_path, h) == 1
    assert "--allow-legacy-health" in capsys.readouterr().out
    assert _cli(tmp_path, h, "--allow-legacy-health") == 3
    out = capsys.readouterr().out
    assert "[SKIP] page_sha256" in out and "[SKIP] runtime_files" in out
    assert "部分核对" in out and "不是完整部署通过" in out and "结论：通过" not in out
    # 显式兼容旧版本时，deploy 存在但损坏仍按新版本严格判失败
    h = _good_health(fp)
    h["deploy"] = "BROKEN"
    assert _cli(tmp_path, h, "--allow-legacy-health") == 1
    capsys.readouterr()
    # 兼容模式下其他项不对仍是 FAIL
    h = _good_health(fp)
    del h["deploy"]
    h["core_sha256"] = "0" * 64
    assert _cli(tmp_path, h, "--allow-legacy-health") == 1


def test_r77_1_malformed_input_is_reported_not_traceback(tmp_path, capsys):
    assert _cli(tmp_path, "[1, 2]") == 1
    assert "health 不是 JSON 对象" in capsys.readouterr().out
    assert _cli(tmp_path, "{not json") == 1
    assert "不是合法 JSON" in capsys.readouterr().out
    fp = FP.fingerprint("HEAD")
    h = _good_health(fp)
    h["watchlist_store"] = "down"
    assert _cli(tmp_path, h) == 1


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
