#!/usr/bin/env python3
"""
Create FIR-12 Pattern Migration PR via GitHub Git Data API.
Workflow: blob -> tree -> commit -> update ref -> create PR
"""
import json
import base64
import os
import sys
import urllib.request

REPO = "Firsh3k/kline-visual-pattern-analyzer"
BRANCH = "v5/fir-12-pattern-migration"
BASE_BRANCH = "main"
TOKEN = os.popen("grep oauth_token ~/.config/gh/hosts.yml | awk '{print $2}'").read().strip()
PROJECT_ROOT = "/home/user/Doubao/chats/38444703140272130/kline_pattern_scanner"

FILES = [
    "core/pivot_patterns.py",
    "tests/v5/test_pivot_patterns.py",
    "core/__init__.py",
    "scripts/v5_validation.py",
    "docs/v5_pattern_migration_validation.md",
    "data/v5_validation/600519.csv",
    "data/v5_validation/002594.csv",
    "data/v5_validation/300750.csv",
    "data/v5_validation/601318.csv",
    "data/v5_validation/000858.csv",
    "data/v5_validation/002415.csv",
    "data/v5_validation/results.json",
]

def api(method, path, data=None):
    url = f"https://api.github.com/{path}"
    headers = {
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"API ERROR {e.code}: {error_body[:500]}", file=sys.stderr)
        raise

def get_main_sha():
    ref = api("GET", f"repos/{REPO}/git/ref/heads/{BASE_BRANCH}")
    return ref["object"]["sha"]

def create_branch(from_sha):
    try:
        ref = api("POST", f"repos/{REPO}/git/refs", {
            "ref": f"refs/heads/{BRANCH}",
            "sha": from_sha,
        })
        print(f"Created branch {BRANCH} at {ref['object']['sha'][:8]}")
        return ref["object"]["sha"]
    except Exception as e:
        if "Reference already exists" in str(e):
            print(f"Branch {BRANCH} already exists, will update it")
            ref = api("GET", f"repos/{REPO}/git/ref/heads/{BRANCH}")
            return ref["object"]["sha"]
        raise

def create_blob(filepath):
    full_path = os.path.join(PROJECT_ROOT, filepath)
    with open(full_path, "rb") as f:
        content = f.read()
    b64 = base64.b64encode(content).decode()
    blob = api("POST", f"repos/{REPO}/git/blobs", {
        "content": b64,
        "encoding": "base64",
    })
    print(f"  Blob: {filepath} -> {blob['sha'][:8]} ({len(content)} bytes)")
    return blob["sha"]

def create_tree(base_tree_sha, tree_entries):
    tree = api("POST", f"repos/{REPO}/git/trees", {
        "base_tree": base_tree_sha,
        "tree": tree_entries,
    })
    print(f"Tree: {tree['sha'][:8]} ({len(tree_entries)} entries)")
    return tree["sha"]

def create_commit(tree_sha, parent_sha, message):
    commit = api("POST", f"repos/{REPO}/git/commits", {
        "message": message,
        "tree": tree_sha,
        "parents": [parent_sha],
    })
    print(f"Commit: {commit['sha'][:8]}")
    return commit["sha"]

def update_ref(commit_sha):
    api("PATCH", f"repos/{REPO}/git/refs/heads/{BRANCH}", {
        "sha": commit_sha,
        "force": False,
    })
    print(f"Updated {BRANCH} -> {commit_sha[:8]}")

def create_pr():
    pr = api("POST", f"repos/{REPO}/pulls", {
        "title": "FIR-12: Pattern Migration — W/M/H&S 形态迁移到 SwingPivotEngine + A股验证",
        "head": BRANCH,
        "base": BASE_BRANCH,
        "body": PR_BODY,
    })
    print(f"\nPR created: #{pr['number']}")
    print(f"URL: {pr['html_url']}")
    return pr

PR_BODY = """## FIR-12 — Pattern Migration (V5 Phase 1C)

将 V4 的 W Bottom / M Top / H&S Bottom / H&S Top 四种形态识别迁移到使用 V5 SwingPivotEngine，并用代表性 A 股数据做 Before/After 对比验证。

### 核心变更

| 文件 | 说明 |
|------|------|
| `core/pivot_patterns.py` | 基于 SwingPivotEngine 的四种形态识别器（654行） |
| `tests/v5/test_pivot_patterns.py` | 25 个单元测试，覆盖标准形态/边界/状态机/回归 |
| `core/__init__.py` | 导出四个 detector 类 |
| `scripts/v5_validation.py` | A股 Before/After 验证脚本（可重复运行） |
| `docs/v5_pattern_migration_validation.md` | 验证报告 |
| `data/v5_validation/` | 6只A股K线数据 + 验证结果 |

### 实现架构

- **`PivotPatternDetectorBase`**：封装已确认 pivot 获取、量能确认、通用突破状态机、评分→置信度映射
- **`WBottomDetector`**（w_bottom/BULL）：两个 swing low + 水平颈线 + 突破确认
- **`MTopDetector`**（m_top/BEAR）：两个 swing high + 水平颈线 + 跌破确认
- **`HSBottomDetector`**（hs_bottom/BULL）：三点 swing low + 两点连线倾斜颈线
- **`HSTopDetector`**（hs_top/BEAR）：对称的顶部形态
- 全部用 `@registry.pattern` 注册到 AlgorithmRegistry
- 只使用已确认 pivot（`detect_confirmed_only`），避免未来函数

### 突破状态机

`FORMING` → `PRE_BREAK` → `BREAKOUT_DAY` → `POST_BREAK` / `RETEST` / `FAILED_BREAK`

### A股验证结果（6只代表性股票，100日前复权日K）

| 指标 | 结果 | 验收标准 | 判定 |
|------|------|----------|------|
| 形态级 Recall | 100% | 下降 <15% | ✅ |
| 重叠形态 FP Reduction | 75% | >20% | ✅ |
| V5 形态总数 | 37个（w_bottom 12, m_top 13, hs_bottom 7, hs_top 5） | - | - |
| Confidence S级 | 22/37 (59%) | - | - |

**典型案例**：
- 601318 中国平安：V4 对同一 W 底重复触发 4 次（8/5、8/17、8/28、9/8），V5 只识别 1 次结构确认 → 误报减少 75%
- 000858 五粮液：V5 识别 FAILED_BREAK 并自动扣分至 35/C
- 600519 贵州茅台：V5 高质量 RETEST，score 100/S

### 硬约束遵守

- ✅ 未修改任何 V4 production 文件（`pattern_scanner.py`/`analyze_stock.py`/`kline_analyzer.html`/`batch_scan.py`）
- ✅ 新代码放在 `core/` 目录
- ✅ 独立 PR、独立测试、独立 QA
- ✅ 使用 Canonical Schema 类型（PatternResult/PatternKeyPoint/OHLCV 等）
- ✅ 25 个新测试全部通过，不影响已有测试

### 注意事项

- 验证样本为 6 股 × 100 根，V4 历史信号仅 4 个（集中于同一形态），统计功效有限；建议接入更长历史后复跑 `scripts/v5_validation.py` 回归
- V4 Python 端仅有 W Bottom 实现（M Top/H&S 在 JS 端），因此 W Bottom 做了直接 Before/After 对比，其余三种为 V5 新增 Python 覆盖
"""

def main():
    print(f"=== Creating FIR-12 Pattern Migration PR ===")
    print(f"Repo: {REPO}")
    print(f"Branch: {BRANCH}")
    print()

    # 1. Get main SHA
    main_sha = get_main_sha()
    print(f"Main SHA: {main_sha[:8]}")

    # 2. Create branch
    branch_sha = create_branch(main_sha)

    # 3. Create blobs
    print("\nCreating blobs...")
    tree_entries = []
    for filepath in FILES:
        blob_sha = create_blob(filepath)
        tree_entries.append({
            "path": filepath,
            "mode": "100644",
            "type": "blob",
            "sha": blob_sha,
        })

    # 4. Create tree
    print("\nCreating tree...")
    tree_sha = create_tree(main_sha, tree_entries)

    # 5. Create commit
    print("\nCreating commit...")
    commit_msg = """FIR-12: Pattern Migration — W/M/H&S 形态迁移到 SwingPivotEngine + A股验证

- 新增 core/pivot_patterns.py：四种基于已确认 swing pivot 的形态识别器
- 新增 tests/v5/test_pivot_patterns.py：25个单元测试
- 更新 core/__init__.py：导出四个 detector
- 新增 scripts/v5_validation.py：A股 Before/After 验证脚本
- 新增 docs/v5_pattern_migration_validation.md：验证报告
- 新增 data/v5_validation/：6只A股K线数据 + 验证结果

验收：形态级Recall 100%，重叠形态FP减少75%"""
    commit_sha = create_commit(tree_sha, main_sha, commit_msg)

    # 6. Update ref
    print("\nUpdating branch ref...")
    update_ref(commit_sha)

    # 7. Create PR
    print("\nCreating PR...")
    pr = create_pr()
    print(f"\n=== DONE ===")
    print(f"PR #{pr['number']}: {pr['title']}")
    print(f"URL: {pr['html_url']}")

if __name__ == "__main__":
    main()
