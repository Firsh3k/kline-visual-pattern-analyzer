#!/usr/bin/env python3
"""
规则变更回归证据工具：比较两个提交的形态识别输出（逐字段）

用法：
  python3 tests/tools/compare_detector_outputs.py <BASE_REF> [--head <HEAD_REF 或 WORKTREE>] [--json out.json]

做法：
- 在临时 git worktree 中检出 BASE_REF（HEAD 默认为当前工作区）；
- 两边各在独立子进程中 import 各自的 core，使用 tests/v5/test_v4_migration_parity.py 的
  同一组数据集（仓库 CSV + 真实 fixture + 合成数据），对每个时间点（前缀长度 60..n）
  运行全部注册的形态识别器，输出 PatternResult.to_dict()；
- 父进程逐条比较，按“字段路径”统计差异数量，并列出前若干个示例。

输出可直接贴到 PR / Linear 作为“规则调整的回归证据”。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

WORKER = r'''
import json, sys
from pathlib import Path
root = Path(sys.argv[1]); data_root = Path(sys.argv[2]); out = sys.argv[3]
sys.path.insert(0, str(root))
sys.path.insert(0, str(data_root / "tests" / "v5"))
sys.path.insert(0, str(data_root / "tests" / "v5" / "oracle"))
import importlib
core = importlib.import_module("core")
assert Path(core.__file__).resolve().is_relative_to(root.resolve()), core.__file__
import test_v4_migration_parity as P   # 数据集来自 data_root（两边相同）
ids = sorted(core.registry.get_pattern_ids())
with open(out, "w", encoding="utf-8") as f:
    for name in sorted(P.DATASETS):
        rows = P.DATASETS[name]
        for n in P._prefixes(rows):
            kl = [core.OHLCV(date=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5]) for r in rows[:n]]
            for pid in ids:
                res = [r.to_dict() for r in core.registry.get_pattern(pid).detect(kl)]
                f.write(json.dumps([name, n, pid, res], ensure_ascii=False, sort_keys=True) + "\n")
'''


def _run(side_root: Path, out: Path) -> None:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"}
    # 数据集固定取 HEAD 侧（当前仓库）的定义，保证两边输入完全相同
    subprocess.run([sys.executable, "-c", WORKER, str(side_root), str(ROOT), str(out)],
                   check=True, env=env, cwd=str(side_root))


def _diff(a, b, path=""):
    if type(a) != type(b):
        yield path or "<root>", a, b
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                yield f"{path}.{k}", a.get(k, "<absent>"), b.get(k, "<absent>")
            else:
                yield from _diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        if len(a) != len(b):
            yield f"{path}[len]", len(a), len(b)
        for i, (x, y) in enumerate(zip(a, b)):
            yield from _diff(x, y, f"{path}[]")
    elif a != b:
        yield path, a, b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--head", default=None, help="对比的 ref；默认当前工作区")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--examples", type=int, default=5)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        base_wt = td / "base"
        subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", "-q", str(base_wt), args.base], check=True)
        head_root = ROOT
        head_wt = None
        if args.head:
            head_wt = td / "head"
            subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", "-q", str(head_wt), args.head], check=True)
            head_root = head_wt
        try:
            _run(base_wt, td / "base.jsonl")
            _run(head_root, td / "head.jsonl")
            base_sha = subprocess.check_output(["git", "-C", str(base_wt), "rev-parse", "HEAD"]).decode().strip()
            head_desc = args.head or "WORKTREE"
            total = changed = 0
            by_field: Counter = Counter()
            by_pid: Counter = Counter()
            examples = defaultdict(list)
            transitions: Counter = Counter()   # 阶段 / 方向 / 置信等级的迁移统计
            with open(td / "base.jsonl", encoding="utf-8") as fa, open(td / "head.jsonl", encoding="utf-8") as fb:
                for la, lb in zip(fa, fb):
                    na, nn, pa, ra = json.loads(la)
                    nb, mm, pb, rb = json.loads(lb)
                    if (na, nn, pa) != (nb, mm, pb):
                        raise SystemExit(f"枚举不一致：{(na, nn, pa)} vs {(nb, mm, pb)}（两边注册的形态不同？）")
                    total += 1
                    diffs = list(_diff(ra, rb))
                    if diffs:
                        changed += 1
                        by_pid[pa] += 1
                        for path, x, y in diffs:
                            key = f"{pa}{path}"
                            by_field[key] += 1
                            if path.split(".")[-1] in ("stage", "direction", "confidence"):
                                transitions[f"{key}: {x} -> {y}"] += 1
                            if len(examples[key]) < args.examples:
                                examples[key].append({"dataset": na, "prefix": nn, "base": x, "head": y})
            report = {"base": base_sha, "head": head_desc, "evaluations": total, "changed_evaluations": changed,
                      "changed_by_pattern": dict(by_pid), "changed_by_field": dict(by_field),
                      "transitions": dict(transitions), "examples": dict(examples)}
            print(f"base={base_sha[:7]} head={head_desc}  evaluations={total}  changed={changed}")
            for k, v in sorted(by_field.items(), key=lambda kv: -kv[1]):
                print(f"  {v:6d}  {k}")
                for ex in examples[k][:2]:
                    print(f"          e.g. {ex['dataset']} n={ex['prefix']}: {ex['base']!r} -> {ex['head']!r}")
            if transitions:
                print("transitions:")
                for k, v in sorted(transitions.items()):
                    print(f"  {v:6d}  {k}")
            if args.json:
                args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        finally:
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(base_wt)], check=False)
            if head_wt:
                subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(head_wt)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
