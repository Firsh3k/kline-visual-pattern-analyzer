#!/usr/bin/env python3
"""
V6 线上版本自证：按 git 版本算出“线上应该看到的指纹”，与 /api/health 返回值逐项比对。

    python3 scripts/v6_fingerprint.py --ref v6-web-1.0.0      # 某个标签 / 提交
    python3 scripts/v6_fingerprint.py                         # 当前工作区

输出：
- core_sha256      对应 /api/health 的 core_sha256，算法同 core.analysis.core_content_sha256
- analyzer_sha256  对应 /api/health 的 analyzer_sha256（python_analyzer.py 文件本身）
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


def fingerprint(ref=None):
    commit = _git("rev-parse", ref).decode().strip() if ref else None
    return {
        "ref": ref or "(working tree)",
        "commit": commit,
        "core_sha256": core_sha256(ref),
        "analyzer_sha256": hashlib.sha256(_read(ref, f"{APP}/python_analyzer.py")).hexdigest(),
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
