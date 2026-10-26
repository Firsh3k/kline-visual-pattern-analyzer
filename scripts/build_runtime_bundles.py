#!/usr/bin/env python3
"""
把服务运行时要读的非 JS 文件生成 TypeScript 模块，随 nest 模块图一起部署（FIR-41 平台打包问题）。

    python3 scripts/build_runtime_bundles.py            # 重新生成
    python3 scripts/build_runtime_bundles.py --check    # CI：生成物与来源不一致则 exit 1

为什么需要（2026-10-26 线上诊断确认）：
1. 妙搭部署会过滤所有 .py；
2. 服务端只部署从 dist/server/main.js 沿 require 能追踪到的文件（scripts/prune-smart.js 同款 @vercel/nft 追踪），
   按 process.cwd() 读取的 python_analyzer.py、core/、sector_data/catalog.json 都不会上线。
所以把它们生成被 service 静态 import 的 .ts 常量：

- server/modules/stock-analysis/engine-bundle.ts
  Python 引擎（core/**/*.py + python_analyzer.py），gzip + base64。运行时 engine.ts 解压到 /tmp、
  逐个文件校验 sha256 后再 spawn python。Python 仍是唯一算法源，内容与仓库里的 .py 逐字节相同。
- server/modules/stock-analysis/sector-catalog.data.ts
  板块清单 sector_data/catalog.json 的原文（由 scripts/build_sector_catalog.py 生成）。

两个文件都入库（平台从仓库构建，必须存在），不要手改。core / python_analyzer.py / catalog.json 改了之后
重新运行本脚本并一起提交；CI 用 --check 拦住忘了重新生成的情况。--check 比较的是解码后的内容，
不依赖 gzip 实现的字节细节。
"""
import argparse
import base64
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "v6" / "app-builder"
MODULE_DIR = APP / "server" / "modules" / "stock-analysis"
ENGINE_TS = MODULE_DIR / "engine-bundle.ts"
CATALOG_TS = MODULE_DIR / "sector-catalog.data.ts"
CATALOG_JSON = APP / "sector_data" / "catalog.json"

ENGINE_FORMAT = "kline-engine-bundle-1"
# 只允许这些路径进包；运行时 engine.ts 用同一条规则拒绝包里任何别的路径（防路径穿越）
ALLOWED_PATH = re.compile(r"^(?:python_analyzer\.py|core/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.py)$")

HEADER = (
    "// 自动生成，不要手改：python3 scripts/build_runtime_bundles.py（CI 用 --check 校验与来源一致）。\n"
    "// 妙搭部署过滤 .py，且服务端只部署 main.js 沿 import 能追踪到的文件，所以运行时文件以常量形式随模块部署。\n"
)


def engine_sources_from_tree(app=APP):
    """工作区里应进包的文件：core/ 下全部 .py（不含 __pycache__）+ python_analyzer.py → {相对路径: bytes}"""
    files = {"python_analyzer.py": (app / "python_analyzer.py").read_bytes()}
    core = app / "core"
    for p in sorted(core.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        files[p.relative_to(app).as_posix()] = p.read_bytes()
    return files


def engine_raw(files):
    """包的规范内容（JSON 字节）；sha256(raw) 即 bundle_sha256，线上 /api/health 报的就是它"""
    entries = []
    for rel in sorted(files):
        if not ALLOWED_PATH.match(rel):
            raise ValueError(f"不允许进包的路径：{rel}")
        data = files[rel]
        entries.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(),
                        "data": base64.b64encode(data).decode("ascii")})
    if "python_analyzer.py" not in files or "core/__init__.py" not in files:
        raise ValueError("包里必须有 python_analyzer.py 和 core/__init__.py")
    return json.dumps({"format": ENGINE_FORMAT, "files": entries}, separators=(",", ":"), sort_keys=True).encode("utf-8")


def engine_bundle_sha256(files):
    return hashlib.sha256(engine_raw(files)).hexdigest()


def render_engine_ts(files):
    raw = engine_raw(files)
    payload = base64.b64encode(gzip.compress(raw, compresslevel=9, mtime=0)).decode("ascii")
    return (
        HEADER
        + "// Python 引擎包：core/**/*.py + python_analyzer.py。由 engine.ts 解压到 /tmp 并逐个文件校验后调用。\n"
        + f"export const ENGINE_BUNDLE_FORMAT = '{ENGINE_FORMAT}';\n"
        + f"export const ENGINE_BUNDLE_SHA256 = '{hashlib.sha256(raw).hexdigest()}';\n"
        + f"export const ENGINE_BUNDLE_FILE_COUNT = {len(files)};\n"
        + f"export const ENGINE_BUNDLE_GZIP_BASE64 =\n  '{payload}';\n"
    )


def render_catalog_ts(text):
    return (
        HEADER
        + "// 板块清单 sector_data/catalog.json 原文（scripts/build_sector_catalog.py 生成）。sha256 与该文件字节一致。\n"
        + f"export const SECTOR_CATALOG_SHA256 = '{hashlib.sha256(text.encode('utf-8')).hexdigest()}';\n"
        + f"export const SECTOR_CATALOG_JSON =\n  {json.dumps(text, ensure_ascii=False)};\n"
    )


_CONST = r"export const {name} =\s*(.+?);\n"


def _const(src, name):
    m = re.search(_CONST.format(name=name), src, re.S)
    if not m:
        raise ValueError(f"找不到常量 {name}")
    return m.group(1).strip()


def read_engine_ts(path=ENGINE_TS):
    """解析已生成的 engine-bundle.ts → (sha 常量, 文件数常量, 解码后的 raw)"""
    src = path.read_text(encoding="utf-8")
    sha = _const(src, "ENGINE_BUNDLE_SHA256").strip("'")
    count = int(_const(src, "ENGINE_BUNDLE_FILE_COUNT"))
    payload = _const(src, "ENGINE_BUNDLE_GZIP_BASE64").strip("'")
    return sha, count, gzip.decompress(base64.b64decode(payload))


def read_catalog_ts(path=CATALOG_TS):
    src = path.read_text(encoding="utf-8")
    return _const(src, "SECTOR_CATALOG_SHA256").strip("'"), json.loads(_const(src, "SECTOR_CATALOG_JSON"))


def check(app=APP, engine_ts=ENGINE_TS, catalog_ts=CATALOG_TS, catalog_json=CATALOG_JSON):
    """返回问题清单（空 = 一致）"""
    problems = []
    files = engine_sources_from_tree(app)
    want = engine_raw(files)
    try:
        sha, count, raw = read_engine_ts(engine_ts)
        if raw != want:
            problems.append(f"{engine_ts.name} 与 core/ + python_analyzer.py 不一致（需要重新生成）")
        if sha != hashlib.sha256(raw).hexdigest():
            problems.append(f"{engine_ts.name} 的 ENGINE_BUNDLE_SHA256 与包内容不符")
        if count != len(files):
            problems.append(f"{engine_ts.name} 的 ENGINE_BUNDLE_FILE_COUNT={count}，实际应为 {len(files)}")
    except (OSError, ValueError) as e:
        problems.append(f"{engine_ts.name} 读不了：{e}")
    try:
        text = catalog_json.read_text(encoding="utf-8")
        sha, embedded = read_catalog_ts(catalog_ts)
        if embedded != text:
            problems.append(f"{catalog_ts.name} 与 {catalog_json.name} 不一致（需要重新生成）")
        if sha != hashlib.sha256(text.encode("utf-8")).hexdigest():
            problems.append(f"{catalog_ts.name} 的 SECTOR_CATALOG_SHA256 与内容不符")
    except (OSError, ValueError) as e:
        problems.append(f"{catalog_ts.name} 读不了：{e}")
    return problems


def write_catalog_module(catalog_json=CATALOG_JSON, catalog_ts=CATALOG_TS):
    catalog_ts.write_text(render_catalog_ts(catalog_json.read_text(encoding="utf-8")), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="只检查生成物与来源是否一致")
    args = ap.parse_args(argv)
    if args.check:
        problems = check()
        for p in problems:
            print(f"✗ {p}", file=sys.stderr)
        if problems:
            print("请运行 python3 scripts/build_runtime_bundles.py 重新生成并提交", file=sys.stderr)
            return 1
        print("engine-bundle.ts / sector-catalog.data.ts 与来源一致")
        return 0
    files = engine_sources_from_tree()
    ENGINE_TS.write_text(render_engine_ts(files), encoding="utf-8")
    write_catalog_module()
    print(f"已生成 {ENGINE_TS.relative_to(ROOT)}：{len(files)} 个文件，bundle_sha256={engine_bundle_sha256(files)}")
    print(f"已生成 {CATALOG_TS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
