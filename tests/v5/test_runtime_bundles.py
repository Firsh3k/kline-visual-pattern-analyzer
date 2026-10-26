"""FIR-41 平台打包问题：scripts/build_runtime_bundles.py 生成的内嵌模块与仓库来源一致（CI 门禁）"""
import base64
import gzip
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "v6" / "app-builder"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RB = _load("build_runtime_bundles_t", ROOT / "scripts" / "build_runtime_bundles.py")
FP = _load("v6_fingerprint_rb", ROOT / "scripts" / "v6_fingerprint.py")


def _copy_app(tmp_path):
    app = tmp_path / "app"
    shutil.copytree(APP / "core", app / "core", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(APP / "python_analyzer.py", app / "python_analyzer.py")
    (app / "sector_data").mkdir()
    shutil.copy(APP / "sector_data" / "catalog.json", app / "sector_data" / "catalog.json")
    mod = app / "server"
    mod.mkdir()
    shutil.copy(RB.ENGINE_TS, mod / "engine-bundle.ts")
    shutil.copy(RB.CATALOG_TS, mod / "sector-catalog.data.ts")
    return app, mod / "engine-bundle.ts", mod / "sector-catalog.data.ts", app / "sector_data" / "catalog.json"


def _check(app, eng, cat, cj):
    return RB.check(app=app, engine_ts=eng, catalog_ts=cat, catalog_json=cj)


def test_committed_bundles_match_sources():
    assert RB.check() == []
    assert RB.main(["--check"]) == 0


def test_bundle_content_is_exactly_the_python_sources():
    sha, count, raw = RB.read_engine_ts()
    doc = json.loads(raw)
    paths = [f["path"] for f in doc["files"]]
    want = sorted(["python_analyzer.py"] + [p.relative_to(APP).as_posix() for p in (APP / "core").rglob("*.py") if "__pycache__" not in p.parts])
    assert paths == want and count == len(want)
    for f in doc["files"]:
        data = base64.b64decode(f["data"])
        assert data == (APP / f["path"]).read_bytes()
        assert f["sha256"] == hashlib.sha256(data).hexdigest()
    assert sha == hashlib.sha256(raw).hexdigest() == FP.engine_bundle_sha256(None)


def test_check_fails_when_core_changes_without_regenerating(tmp_path, capsys):
    """改了 core 却忘了重新生成 → --check 失败（线上会跑旧引擎）"""
    app, eng, cat, cj = _copy_app(tmp_path)
    assert _check(app, eng, cat, cj) == []
    (app / "core" / "signals.py").write_text((app / "core" / "signals.py").read_text(encoding="utf-8") + "\n# 改动\n", encoding="utf-8")
    assert any("engine-bundle.ts 与 core/" in p for p in _check(app, eng, cat, cj))
    (app / "core" / "signals.py").write_text((APP / "core" / "signals.py").read_text(encoding="utf-8"), encoding="utf-8")
    (app / "core" / "new_module.py").write_text("X = 1\n", encoding="utf-8")
    assert any("不一致" in p for p in _check(app, eng, cat, cj))


def test_check_fails_when_analyzer_or_catalog_changes(tmp_path):
    app, eng, cat, cj = _copy_app(tmp_path)
    (app / "python_analyzer.py").write_text("# x\n", encoding="utf-8")
    assert _check(app, eng, cat, cj)
    app, eng, cat, cj = _copy_app(tmp_path / "b")
    cj.write_text(cj.read_text(encoding="utf-8").replace("CRO", "CR0", 1), encoding="utf-8")
    assert any("sector-catalog.data.ts 与 catalog.json 不一致" in p for p in _check(app, eng, cat, cj))


def test_check_catches_hand_edited_constants(tmp_path):
    app, eng, cat, cj = _copy_app(tmp_path)
    src = eng.read_text(encoding="utf-8")
    sha = RB.read_engine_ts(eng)[0]
    eng.write_text(src.replace(sha, "0" * 64), encoding="utf-8")
    assert any("ENGINE_BUNDLE_SHA256" in p for p in _check(app, eng, cat, cj))
    eng.write_text(src.replace(f"ENGINE_BUNDLE_FILE_COUNT = {RB.read_engine_ts()[1]}", "ENGINE_BUNDLE_FILE_COUNT = 1"), encoding="utf-8")
    assert any("FILE_COUNT" in p for p in _check(app, eng, cat, cj))


def test_generation_is_deterministic_and_rejects_bad_paths():
    files = RB.engine_sources_from_tree()
    assert RB.render_engine_ts(files) == RB.render_engine_ts(dict(reversed(list(files.items()))))
    assert gzip.decompress(base64.b64decode(RB.render_engine_ts(files).split("'")[-2])) == RB.engine_raw(files)
    for bad in ("core/../x.py", "/abs.py", "core/x.sh", "other/x.py"):
        try:
            RB.engine_raw({**files, bad: b""})
        except ValueError:
            continue
        raise AssertionError(f"应拒绝 {bad}")


def test_sector_catalog_generator_updates_embedded_copy(tmp_path):
    """build_sector_catalog.py 生成 catalog.json 时同步内嵌副本；嵌入副本 sha256 = catalog.json 字节 sha256"""
    sha, text = RB.read_catalog_ts()
    raw = (APP / "sector_data" / "catalog.json").read_bytes()
    assert text.encode("utf-8") == raw and sha == hashlib.sha256(raw).hexdigest() == FP.sector_catalog_sha256(None)
    src = (ROOT / "scripts" / "build_sector_catalog.py").read_text(encoding="utf-8")
    assert "write_catalog_module()" in src
