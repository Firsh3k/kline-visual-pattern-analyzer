#!/usr/bin/env python3
"""
V6 线上版本自证：按 git 版本算出“线上应该看到的指纹”，与 /api/health 返回值逐项比对。

    python3 scripts/v6_fingerprint.py --ref v6-web-1.0.0      # 某个标签 / 提交
    python3 scripts/v6_fingerprint.py                         # 当前工作区

输出：
- core_sha256      对应 /api/health 的 core_sha256，算法同 core.analysis.core_content_sha256
- analyzer_sha256  对应 /api/health 的 analyzer_sha256（python_analyzer.py 文件本身）
- page_sha256      对应 /api/health 的 deploy.page_sha256（线上实际返回的 V4 页面，FIR-41 第 5 项）
- engine_bundle_sha256   对应 deploy.engine.bundle_sha256：由该版本的 core/**/*.py + python_analyzer.py 算出
                         （与 scripts/build_runtime_bundles.py 同一算法；该版本还没有内嵌引擎包时为 null）
- sector_catalog_sha256  对应 deploy.sector_catalog_sha256：该版本 sector_data/catalog.json 的 sha256
- runtime_files    该版本线上必须报告“找得到 / 可用”的项（deploy.files_found 的键）
- files            关键部署文件的 sha256，供人工抽查（NestJS 线上跑编译产物，.ts 无法自报）
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = "v6/app-builder"
PAGE = f"{APP}/client/public/v4/index.html"
KEY_FILES = [
    f"{APP}/client/public/v4/index.html",
    f"{APP}/python_analyzer.py",
    f"{APP}/core/analysis.py",
    f"{APP}/core/signals.py",
    f"{APP}/server/modules/stock-analysis/stock-analysis.service.ts",
    f"{APP}/server/modules/stock-analysis/stock-analysis.pure.ts",
    f"{APP}/server/modules/stock-analysis/stock-analysis.controller.ts",
]


def _git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def _read(ref, path):
    if ref is None:
        return (ROOT / path).read_bytes()
    return _git("show", f"{ref}:{path}")


def _core_files(ref):
    prefix = f"{APP}/core/"
    if ref is None:
        base = ROOT / APP / "core"
        return sorted(p.relative_to(base).as_posix() for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    names = _git("ls-tree", "-r", "--name-only", ref, prefix).decode().split()
    return sorted(n[len(prefix):] for n in names if n.endswith(".py"))


def core_sha256(ref=None):
    h = hashlib.sha256()
    for rel in _core_files(ref):
        data = _read(ref, f"{APP}/core/{rel}")
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).hexdigest().encode() + b"\n")
    return h.hexdigest()


ENGINE_TS = "server/modules/stock-analysis/engine-bundle.ts"
CATALOG = "sector_data/catalog.json"

# 第 5 项（#77）时服务按 cwd 找这些文件；内嵌引擎包之前的版本按这张表核对
LEGACY_RUNTIME_FILE_PATHS = {
    "page": "client/public/v4/index.html",
    "analyzer": "python_analyzer.py",
    "core": "core/__init__.py",
    "sector_catalog": "sector_data/catalog.json",
}


def _names(ref):
    if ref is None:
        return None
    return set(_git("ls-tree", "-r", "--name-only", ref, f"{APP}/").decode().split())


def _has(ref, rel, names=None):
    full = f"{APP}/{rel}"
    if ref is None:
        return (ROOT / full).exists()
    return full in (names if names is not None else _names(ref))


def runtime_files(ref=None):
    """该版本线上 deploy.files_found 里必须为 true 的键"""
    names = _names(ref)
    if _has(ref, ENGINE_TS, names):
        # 引擎与板块清单随模块内嵌（平台不部署 .py / 按 cwd 读的文件），见 deploy-info.ts
        return ["page", "engine"] + (["sector_catalog"] if _has(ref, CATALOG, names) else [])
    return [k for k, rel in LEGACY_RUNTIME_FILE_PATHS.items() if _has(ref, rel, names)]


def _bundles():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_runtime_bundles_fp", ROOT / "scripts" / "build_runtime_bundles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def engine_bundle_sha256(ref=None):
    """该版本应有的内嵌引擎包 sha256（按源文件算，不读生成物）；还没有内嵌引擎包的版本返回 None"""
    if not _has(ref, ENGINE_TS):
        return None
    files = {"python_analyzer.py": _read(ref, f"{APP}/python_analyzer.py")}
    for rel in _core_files(ref):
        files[f"core/{rel}"] = _read(ref, f"{APP}/core/{rel}")
    return _bundles().engine_bundle_sha256(files)


def sector_catalog_sha256(ref=None):
    return hashlib.sha256(_read(ref, f"{APP}/{CATALOG}")).hexdigest() if _has(ref, CATALOG) else None


def fingerprint(ref=None):
    commit = _git("rev-parse", ref).decode().strip() if ref else None
    return {
        "ref": ref or "(working tree)",
        "commit": commit,
        "core_sha256": core_sha256(ref),
        "analyzer_sha256": hashlib.sha256(_read(ref, f"{APP}/python_analyzer.py")).hexdigest(),
        "page_sha256": hashlib.sha256(_read(ref, PAGE)).hexdigest(),
        "engine_bundle_sha256": engine_bundle_sha256(ref),
        "sector_catalog_sha256": sector_catalog_sha256(ref),
        "runtime_files": runtime_files(ref),
        "files": {p: hashlib.sha256(_read(ref, p)).hexdigest()[:16] for p in KEY_FILES},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default=None, help="git 标签 / 分支 / 提交；省略则用当前工作区")
    args = ap.parse_args()
    json.dump(fingerprint(args.ref), sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
