#!/usr/bin/env python3
"""
FIR-15 — Sector Membership Refresh Script
受控的板块成分股刷新/对账脚本。

此脚本用于：
1. 验证现有板块成分股的代码格式和存在性
2. （可选）与同花顺 iFinD 权威数据源对账
3. 更新 provenance 元数据（来源、刷新日期、对账结果）
4. 标记过期/不可验证的成员

用法：
  # 仅离线验证（不需要网络）
  python3 scripts/refresh_sector_membership.py --validate-only

  # 验证并更新 provenance（不需要网络）
  python3 scripts/refresh_sector_membership.py --update-provenance

  # 完整刷新（需要 iFinD MCP 工具，暂未实现自动对账）
  python3 scripts/refresh_sector_membership.py --full-refresh

  # 指定板块
  python3 scripts/refresh_sector_membership.py --sector 创新药 --validate-only

  # 输出 JSON 格式报告
  python3 scripts/refresh_sector_membership.py --validate-only --json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

# 添加项目根目录到路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.sector_validation import (
    validate_sector, validate_all_sectors, get_validation_summary,
    load_provenance, save_provenance, update_provenance_after_refresh,
    update_provenance_after_reconciliation,
    check_freshness, list_all_provenances, get_stale_sectors,
    reconcile_with_ths_ifind, reconcile_with_snapshot,
    apply_reconciliation_to_constituents, get_source_only_candidates,
    AuthoritativeSourceUnavailableError,
    SectorProvenance, ReconciliationMethod, FreshnessLevel,
)


# 板块代码映射（与 new_sectors.js 一致）
SECTOR_CODE_MAP = {
    "创新药": "886015",
    "CRO概念": "885927",
    "细胞免疫治疗": "885769",
    "AI视频": "886068",
    "PET铜箔": "886020",
    "MLCC概念": "886112",
    "PCB概念": "885959",
}


def load_sectors_from_js(sector_file: str) -> Dict[str, List[str]]:
    """从 JS 文件加载板块数据"""
    with open(sector_file, 'r', encoding='utf-8') as f:
        content = f.read()

    match = re.search(r'const NEW_SECTORS\s*=\s*(\{[\s\S]*?\});', content)
    if not match:
        raise ValueError("无法找到 NEW_SECTORS 定义")

    js_obj = match.group(1)
    js_obj = re.sub(r"'([^']*)'", r'"\1"', js_obj)
    js_obj = re.sub(r',\s*([}\]])', r'\1', js_obj)

    return json.loads(js_obj)


def run_validation(
    sectors: Dict[str, List[str]],
    sector_filter: Optional[str] = None,
) -> Dict:
    """运行离线验证"""
    if sector_filter:
        if sector_filter not in sectors:
            print(f"错误：板块 '{sector_filter}' 不存在", file=sys.stderr)
            sys.exit(1)
        sectors = {sector_filter: sectors[sector_filter]}

    # 加载现有 provenance
    provenances = {}
    for name in sectors:
        p = load_provenance(name)
        if p:
            provenances[name] = p

    # 验证所有板块
    reports = validate_all_sectors(
        sectors,
        sector_codes=SECTOR_CODE_MAP,
        provenances=provenances,
    )
    summary = get_validation_summary(reports)

    # 检查新鲜度
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    freshness_info = {}
    for name in sectors:
        freshness = check_freshness(name, current_date=today)
        freshness_info[name] = freshness.value

    stale_sectors = get_stale_sectors(current_date=today)

    return {
        'reports': {name: r.to_dict() for name, r in reports.items()},
        'summary': summary,
        'freshness': freshness_info,
        'stale_sectors': stale_sectors,
        'validated_at': datetime.now(timezone.utc).isoformat(),
    }


def update_provenances(
    sectors: Dict[str, List[str]],
    sector_filter: Optional[str] = None,
) -> List[str]:
    """更新所有板块的 provenance（标记为已刷新）"""
    updated = []
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    for name, codes in sectors.items():
        if sector_filter and name != sector_filter:
            continue

        sector_code = SECTOR_CODE_MAP.get(name, "")
        provenance = update_provenance_after_refresh(
            sector_name=name,
            sector_code=sector_code,
            source="manual_refresh",
            total_constituents=len(codes),
            source_url="",
        )
        updated.append(name)
        print(f"  已更新 provenance: {name} ({len(codes)} 只成分股, 刷新日期={today})")

    return updated


def print_human_readable_report(result: Dict):
    """打印人类可读的验证报告"""
    summary = result['summary']
    print("=" * 70)
    print("FIR-15 板块成分股验证报告")
    print(f"验证时间: {result['validated_at']}")
    print("=" * 70)
    print()
    print("--- 汇总统计 ---")
    print(f"  板块数量: {summary['total_sectors']}")
    print(f"  成分股总数: {summary['total_constituents']}")
    print(f"  离线验证通过 (UNVERIFIED + VERIFIED): {summary.get('total_offline_valid', summary['total_verified'])}")
    print(f"  权威对账 VERIFIED: {summary['total_verified']}")
    print(f"  无效: {summary['total_invalid']}")
    print(f"  重复: {summary['total_duplicates']}")
    print(f"  离线验证通过率: {summary.get('overall_offline_valid_rate', summary['overall_verification_rate']):.2%}")
    print(f"  权威对账验证率: {summary['overall_verification_rate']:.2%}")
    print()
    print("  说明：离线验证通过 ≠ VERIFIED。VERIFIED 必须经过权威源对账 matched。")
    print("  未对账的成分股状态为 UNVERIFIED，新鲜度为 NOT_RECONCILED。")
    print()

    if summary['sectors_with_issues']:
        print("--- 有问题的板块 ---")
        for name in summary['sectors_with_issues']:
            report = result['reports'][name]
            print(f"  {name}:")
            print(f"    总数={report['total']}, 已验证={report['verified']}, "
                  f"无效={report['invalid']}, 重复={len(report['duplicates'])}")
            if report['duplicates']:
                print(f"    重复代码: {report['duplicates']}")
            # 列出无效代码
            invalid_codes = [c['code'] for c in report['constituents']
                           if c['status'] == 'invalid']
            if invalid_codes:
                print(f"    无效代码: {invalid_codes[:10]}")
                if len(invalid_codes) > 10:
                    print(f"    ... 还有 {len(invalid_codes) - 10} 个")
        print()

    print("--- 新鲜度状态（基于权威对账时间）---")
    for name, freshness in result['freshness'].items():
        status_icon = {
            'fresh': '✅',
            'aging': '⚠️',
            'stale': '❌',
            'not_reconciled': '🔒',
            'unknown': '❓',
        }.get(freshness, '❓')
        print(f"  {status_icon} {name}: {freshness}")
    print()
    print("  🔒 NOT_RECONCILED = 从未进行过权威对账，绝不能视为 FRESH")
    print()

    if result['stale_sectors']:
        print(f"--- 过期板块（需要刷新）---")
        for name in result['stale_sectors']:
            print(f"  ❌ {name}")
        print()

    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="FIR-15 板块成分股刷新/验证脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 仅离线验证
  python3 scripts/refresh_sector_membership.py --validate-only

  # 验证并更新 provenance
  python3 scripts/refresh_sector_membership.py --update-provenance

  # 指定板块
  python3 scripts/refresh_sector_membership.py --sector 创新药 --validate-only

  # JSON 输出
  python3 scripts/refresh_sector_membership.py --validate-only --json
        """,
    )
    parser.add_argument("--validate-only", action="store_true",
                        help="仅运行离线验证（结果为 UNVERIFIED，不等于权威对账 VERIFIED）")
    parser.add_argument("--update-provenance", action="store_true",
                        help="验证后更新本地 provenance（只更新 local_refresh_date，不影响权威对账新鲜度）")
    parser.add_argument("--full-refresh", action="store_true",
                        help="完整刷新：调用权威源（同花顺 iFinD）对账。权威源不可用时 fail-closed 非零退出，绝不静默降级。")
    parser.add_argument("--reconcile-with-snapshot", type=str, default=None, metavar="SNAPSHOT_FILE",
                        help="使用本地 snapshot JSON 文件进行受控对账（用于测试/CI/离线环境）。格式: {\"板块名\": [\"code1\", \"code2\"]}")
    parser.add_argument("--sector", type=str, default=None,
                        help="指定板块名称（默认验证所有板块）")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON 格式报告")
    parser.add_argument("--sector-file", type=str, default=None,
                        help="板块数据文件路径（默认使用 sector_data/new_sectors.js）")

    args = parser.parse_args()

    # 确定板块数据文件
    if args.sector_file:
        sector_file = args.sector_file
    else:
        sector_file = os.path.join(PROJECT_ROOT, "sector_data", "new_sectors.js")

    if not os.path.exists(sector_file):
        print(f"错误：板块数据文件不存在: {sector_file}", file=sys.stderr)
        sys.exit(1)

    # 加载板块数据
    print(f"加载板块数据: {sector_file}")
    sectors = load_sectors_from_js(sector_file)
    print(f"已加载 {len(sectors)} 个板块")
    print()

    # === Fail-closed: --full-refresh 必须调用权威源，不可用时非零退出 ===
    if args.full_refresh:
        print("=" * 70)
        print("完整刷新模式：尝试调用同花顺 iFinD 权威源对账")
        print("=" * 70)
        print()

        # 尝试对每个板块调用权威源
        sectors_to_reconcile = [args.sector] if args.sector else list(sectors.keys())
        reconciliation_failed = False

        for sector_name in sectors_to_reconcile:
            sector_code = SECTOR_CODE_MAP.get(sector_name, "")
            local_codes = sectors.get(sector_name, [])
            try:
                recon = reconcile_with_ths_ifind(sector_name, sector_code, local_codes)
                print(f"  ✅ {sector_name}: 权威对账成功 (matched={len(recon.matched)})")
            except AuthoritativeSourceUnavailableError as e:
                print(f"  ❌ {sector_name}: 权威源不可用 — fail-closed")
                print(f"     {e}")
                reconciliation_failed = True

        if reconciliation_failed:
            print()
            print("=" * 70)
            print("FAIL-CLOSED: 权威源不可用，完整刷新中止。")
            print("未对账的数据集保持 NOT_RECONCILED 状态，绝不标记为 FRESH。")
            print("请在有 iFinD MCP 工具的环境中运行，或使用 --reconcile-with-snapshot。")
            print("=" * 70)
            sys.exit(2)  # 非零退出码：权威源不可用

    # === 受控 snapshot 对账（用于测试/CI/离线环境）===
    if args.reconcile_with_snapshot:
        print(f"使用 snapshot 文件对账: {args.reconcile_with_snapshot}")
        if not os.path.exists(args.reconcile_with_snapshot):
            print(f"错误：snapshot 文件不存在: {args.reconcile_with_snapshot}", file=sys.stderr)
            sys.exit(1)

        with open(args.reconcile_with_snapshot, 'r', encoding='utf-8') as f:
            snapshot = json.load(f)

        sectors_to_reconcile = [args.sector] if args.sector else list(sectors.keys())
        for sector_name in sectors_to_reconcile:
            if sector_name not in snapshot:
                print(f"  ⚠️ {sector_name}: snapshot 中无此板块，跳过")
                continue
            source_codes = snapshot[sector_name]
            local_codes = sectors.get(sector_name, [])
            recon = reconcile_with_snapshot(sector_name, local_codes, source_codes,
                                             source=f"snapshot:{os.path.basename(args.reconcile_with_snapshot)}")
            print(f"  ✅ {sector_name}: matched={len(recon.matched)}, "
                  f"local_only={len(recon.only_in_local)}, "
                  f"source_only={len(recon.only_in_source)}")

            # 更新 provenance（权威对账成功）
            update_provenance_after_reconciliation(
                sector_name=sector_name,
                method=ReconciliationMethod.THS_IFIND,
                verified_count=len(recon.matched),
                reconciliation_result=f"matched={len(recon.matched)}, "
                                       f"local_only={len(recon.only_in_local)}, "
                                       f"source_only={len(recon.only_in_source)}",
            )
        print()

    # 运行验证
    result = run_validation(sectors, sector_filter=args.sector)

    # 更新本地 provenance（只更新 local_refresh_date，不影响权威新鲜度）
    if args.update_provenance and not args.full_refresh:
        print("更新本地 provenance（只更新 local_refresh_date，不影响权威对账新鲜度）...")
        updated = update_provenances(sectors, sector_filter=args.sector)
        print(f"已更新 {len(updated)} 个板块的本地 provenance")
        print()

    # 输出报告
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human_readable_report(result)

    # 如果有真正的问题（invalid/duplicates），返回非零退出码
    # 注意：未对账（unverified 多）不是问题，是正常状态
    if result['summary']['total_invalid'] > 0 or result['summary']['sectors_with_issues']:
        sys.exit(1)


if __name__ == '__main__':
    main()
