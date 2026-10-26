#!/usr/bin/env python3
"""
把 Python Canonical Core 引擎包（core/ + python_analyzer.py）打包成一个 TypeScript 文件
server/modules/stock-analysis/engine-bundle.ts，导出 gzip + base64 后的文件映射常量。

背景（经线上诊断确认）：
1. 妙搭 fullstack 生产部署会排除所有 .py 文件（运行时 /opt/bytefaas 内 py_count=0）；
2. server/ 只部署 nest 编译“模块依赖图”跟踪的文件——build.sh 后加的孤立
   dist/server/engine-bundle.js 不会进入运行时（线上 server/ 顶层只有 app.module/main/
   modules/common/database）。
因此必须把引擎写成被 service 静态 import 的 .ts 模块，随 nest 编译跟踪并部署；
运行时由 materializeEngine 解压到 /tmp（已确认可写）再 spawn python。本地开发仍可
直接使用真实 core/。该 .ts 文件需入库（云端构建从仓库拉取，必须存在）。

用法：
    python3 scripts/build_engine_bundle.py
    python3 scripts/build_engine_bundle.py --output server/modules/stock-analysis/engine-bundle.ts
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import sys
from pathlib import Path


def collect_files(root: Path) -> dict[str, str]:
    """收集 core/ 全部文件与 python_analyzer.py，返回 {相对路径: base64 内容}。"""
    files: dict[str, str] = {}

    core_dir = root / "core"
    if not core_dir.is_dir():
        raise SystemExit(f"未找到 core 目录: {core_dir}")

    for path in sorted(core_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        files[rel] = base64.b64encode(path.read_bytes()).decode("ascii")

    analyzer = root / "python_analyzer.py"
    if not analyzer.is_file():
        raise SystemExit(f"未找到 python_analyzer.py: {analyzer}")
    files["python_analyzer.py"] = base64.b64encode(analyzer.read_bytes()).decode("ascii")

    return files


def build(root: Path, output: Path) -> None:
    files = collect_files(root)
    raw = json.dumps(files, separators=(",", ":")).encode("utf-8")
    payload = base64.b64encode(gzip.compress(raw, compresslevel=9, mtime=0)).decode("ascii")

    output.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "// 自动生成，请勿手改。由 scripts/build_engine_bundle.py 生成。\n"
        "// Python Canonical Core 引擎包：妙搭部署排除 .py，且 server 只部署 nest 编译跟踪的\n"
        "// 模块，故以 gzip+base64 内嵌为 TS 常量随模块编译部署；运行时由 stock-analysis.service.ts\n"
        "// 的 materializeEngine 解压到 /tmp 后调用。core 变更后需重新运行本脚本并提交。\n"
    )
    body = (
        "export const ENGINE_BUNDLE_FILE_COUNT = %d;\n"
        "export const ENGINE_BUNDLE_PAYLOAD = %s;\n"
    ) % (len(files), json.dumps(payload))
    output.write_text(header + body, encoding="utf-8")

    raw_kb = len(raw) / 1024
    out_kb = output.stat().st_size / 1024
    print(f"engine-bundle 生成: {output}")
    print(f"  文件数: {len(files)}（含 core/ 与 python_analyzer.py）")
    print(f"  原始 JSON: {raw_kb:.1f} KB -> 输出 .ts: {out_kb:.1f} KB")


def main() -> int:
    here = Path(__file__).resolve().parent
    root = here.parent
    parser = argparse.ArgumentParser(description="打包 Python 引擎为 engine-bundle.ts")
    parser.add_argument(
        "--output",
        default=str(root / "server" / "modules" / "stock-analysis" / "engine-bundle.ts"),
        help="输出 TS 路径（默认 server/modules/stock-analysis/engine-bundle.ts）",
    )
    args = parser.parse_args()
    build(root, Path(args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
