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

退出码：0 完整通过；1 不通过（缺字段、类型不对也算）；3 部分核对（只在显式 --allow-legacy-health、
且 health 确实没有 deploy 时出现——旧版本无法自证页面，不能当作完整通过）。
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


def verify(health, expected, allow_watchlist_down=False, allow_legacy_health=False):
    """返回 [(检查项, 结果 PASS/FAIL/WARN/SKIP, 说明)]

    QA R77-1：结构必须严格——deploy 必须是对象、files_found 必须是对象且三个值严格为 true，
    缺字段或类型不对一律 FAIL（逐项报告，不抛异常）。旧版本 health（没有 deploy）只有显式
    allow_legacy_health 时才把页面 / 运行时文件记为 SKIP，此时结论是“部分核对”，不是完整通过。
    """
    out = []

    def check(name, ok, detail, soft=False):
        out.append((name, "PASS" if ok else ("WARN" if soft else "FAIL"), detail))

    if not isinstance(health, dict):
        out.append(("health", "FAIL", f"health 不是 JSON 对象（{type(health).__name__}）"))
        return out

    check("status", health.get("status") == "ok", f"status={health.get('status')!r}")
    check("core_analyzer", health.get("core_analyzer") == "real_core", f"core_analyzer={health.get('core_analyzer')!r}")
    for key in ("core_sha256", "analyzer_sha256"):
        got, want = health.get(key), expected[key]
        check(key, isinstance(got, str) and got == want, f"线上 {got!r} / 应为 {want}")

    if "deploy" not in health and allow_legacy_health:
        out.append(("page_sha256", "SKIP", "health 没有 deploy 字段（第 5 项之前的版本）：页面未核对"))
        out.append(("runtime_files", "SKIP", "health 没有 deploy 字段（第 5 项之前的版本）：运行时文件未核对"))
    else:
        deploy = health.get("deploy")
        if not isinstance(deploy, dict):
            why = "缺少 deploy 字段" if "deploy" not in health else f"deploy 不是对象（{deploy!r}）"
            hint = "；若部署的确是第 5 项之前的版本，可加 --allow-legacy-health 做部分核对" if "deploy" not in health else ""
            out.append(("page_sha256", "FAIL", why + hint))
            out.append(("runtime_files", "FAIL", why))
        else:
            got, want = deploy.get("page_sha256"), expected["page_sha256"]
            check("page_sha256", isinstance(got, str) and got == want, f"线上 {got!r} / 应为 {want}")
            found = deploy.get("files_found")
            if not isinstance(found, dict):
                out.append(("runtime_files", "FAIL", f"deploy.files_found 不是对象（{found!r}）"))
            else:
                bad = [k for k in ("page", "analyzer", "core") if found.get(k) is not True]
                detail = f"cwd={deploy.get('cwd')!r}"
                detail += f"，以下不是 true：{ {k: found.get(k) for k in bad} }" if bad else "，页面 / 执行器 / core 均在"
                check("runtime_files", not bad, detail)

    wl = health.get("watchlist_store")
    wl = wl if isinstance(wl, dict) else {}
    ok = wl.get("kind") == "postgres" and wl.get("ready") is True
    check("watchlist_store", ok, f"kind={wl.get('kind')!r} ready={wl.get('ready')!r} error={wl.get('error')!r}", soft=allow_watchlist_down)
    return out


EXIT_PASS, EXIT_FAIL, EXIT_PARTIAL = 0, 1, 3


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True, help="本次部署的 git 提交 / 标签")
    ap.add_argument("--health", required=True, help="线上 /api/health 返回的 JSON 文件；- 表示从标准输入读")
    ap.add_argument("--allow-watchlist-down", action="store_true", help="自选股不可用只提示、不判失败")
    ap.add_argument("--allow-legacy-health", action="store_true",
                    help="部署的是第 5 项之前的版本（health 没有 deploy）：页面与运行时文件记为 SKIP，结论为部分核对（exit 3）")
    args = ap.parse_args(argv)

    raw = sys.stdin.read() if args.health == "-" else Path(args.health).read_text(encoding="utf-8")
    expected = _load_fingerprint().fingerprint(args.ref)
    print(f"核对版本：{args.ref}（{expected['commit']}）")
    try:
        health = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  [FAIL] health: 不是合法 JSON（{e}）")
        print("结论：不通过（1 项 FAIL）")
        return EXIT_FAIL
    rows = verify(health, expected, args.allow_watchlist_down, args.allow_legacy_health)

    for name, result, detail in rows:
        print(f"  [{result}] {name}: {detail}")
    failed = [r for r in rows if r[1] == "FAIL"]
    skipped = [r for r in rows if r[1] == "SKIP"]
    if failed:
        print(f"结论：不通过（{len(failed)} 项 FAIL）")
        return EXIT_FAIL
    if skipped:
        print(f"结论：部分核对（{len(skipped)} 项未核对：{'、'.join(r[0] for r in skipped)}），不是完整部署通过")
        return EXIT_PARTIAL
    print("结论：通过")
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
