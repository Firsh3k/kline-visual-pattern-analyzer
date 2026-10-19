"""线上版本自证：/api/health 的 core_sha256 / analyzer_sha256 与 scripts/v6_fingerprint.py 算法一致"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FP = _load("v6_fingerprint", ROOT / "scripts" / "v6_fingerprint.py")


def test_core_hash_matches_runtime_self_report():
    from core import analysis as A
    fp = FP.fingerprint()
    # 根 core 与部署副本逐字节一致（另有测试保证），故运行时自报值 = 脚本按部署副本算出的值
    assert A.core_content_sha256() == fp["core_sha256"]
    analyzer = _load("ab_python_analyzer_fp", ROOT / "v6" / "app-builder" / "python_analyzer.py")
    health = analyzer.health_check()
    assert health["core_sha256"] == fp["core_sha256"]
    assert health["analyzer_sha256"] == fp["analyzer_sha256"]


def test_ref_mode_matches_working_tree_when_clean():
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "v6/app-builder"], cwd=ROOT).returncode
    if dirty:
        pytest.skip("工作区有未提交改动，按 git 版本计算的结果与工作区不同属正常")
    a, b = FP.fingerprint("HEAD"), FP.fingerprint()
    assert (a["core_sha256"], a["analyzer_sha256"], a["files"]) == (b["core_sha256"], b["analyzer_sha256"], b["files"])
