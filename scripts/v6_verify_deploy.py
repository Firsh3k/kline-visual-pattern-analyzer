#!/usr/bin/env python3
"""
V6 部署核对（FIR-41 第 5 项）：把线上 /api/health 的返回与“这个 git 版本应该是什么样”逐项比对。

    # 1. 在已登录的浏览器里打开线上 /api/health，把返回的 JSON 存成 health.json
    # 2. 按本次部署的提交 / 标签核对：
    python3 scripts/v6_verify_deploy.py --ref 0bedd30 --health health.json

逐项检查（任一 FAIL → exit 1）：
- status=ok，core_analyzer=real_core
- core_sha256 / analyzer_sha256 与 --ref 算出的一致（Python Core 与执行器）
- deploy.page_sha256 与 --ref 的 client/public/v4/index.html 一致（浏览器实际拿到的页面）
- deploy.files_found：页面、python_analyzer.py、core/ 都在服务的 cwd 下
- watchlist_store：kind=postgres 且 ready=true（--allow-watchlist-down 时只提示不判失败）

health 里没有 deploy 字段（第 5 项之前的版本）时，页面一项记为 SKIP 并提示，不判失败。
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_fingerprint():
    spec = importlib.util.spec_from_file_location("v6_fingerprint", ROOT / "scripts" / "v6_fingerprint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def verify(health, expected, allow_watchlist_down=False):
    """返回 [(检查项, 结果 PASS/FAIL/WARN/SKIP, 说明)]"""
    out = []

    def check(name, ok, detail, soft=False):
        out.append((name, "PASS" if ok else ("WARN" if soft else "FAIL"), detail))

    check("status", health.get("status") == "ok", f"status={health.get('status')!r}")
    check("core_analyzer", health.get("core_analyzer") == "real_core", f"core_analyzer={health.get('core_analyzer')!r}")
    for key in ("core_sha256", "analyzer_sha256"):
        got, want = health.get(key), expected[key]
        check(key, got == want, f"线上 {got} / 应为 {want}")

    deploy = health.get("deploy")
    if not isinstance(deploy, dict):
        out.append(("page_sha256", "SKIP", "health 里没有 deploy 字段（部署的是第 5 项之前的版本），页面无法自证"))
    else:
        got, want = deploy.get("page_sha256"), expected["page_sha256"]
        check("page_sha256", got == want, f"线上 {got} / 应为 {want}")
        found = deploy.get("files_found") or {}
        missing = [k for k in ("page", "analyzer", "core") if not found.get(k)]
        check("runtime_files", not missing, f"cwd={deploy.get('cwd')!r}" + (f"，缺少 {missing}" if missing else "，页面 / 执行器 / core 均在"))

    wl = health.get("watchlist_store") or {}
    ok = wl.get("kind") == "postgres" and wl.get("ready") is True
    check("watchlist_store", ok, f"kind={wl.get('kind')!r} ready={wl.get('ready')!r} error={wl.get('error')!r}", soft=allow_watchlist_down)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True, help="本次部署的 git 提交 / 标签")
    ap.add_argument("--health", required=True, help="线上 /api/health 返回的 JSON 文件；- 表示从标准输入读")
    ap.add_argument("--allow-watchlist-down", action="store_true", help="自选股不可用只提示、不判失败")
    args = ap.parse_args(argv)

    raw = sys.stdin.read() if args.health == "-" else Path(args.health).read_text(encoding="utf-8")
    health = json.loads(raw)
    expected = _load_fingerprint().fingerprint(args.ref)
    rows = verify(health, expected, args.allow_watchlist_down)

    print(f"核对版本：{args.ref}（{expected['commit']}）")
    for name, result, detail in rows:
        print(f"  [{result}] {name}: {detail}")
    failed = [r for r in rows if r[1] == "FAIL"]
    print("结论：" + ("通过" if not failed else f"不通过（{len(failed)} 项 FAIL）"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
