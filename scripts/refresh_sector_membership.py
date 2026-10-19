#!/usr/bin/env python3
"""
FIR-15 — Sector Membership Refresh Script
受控的板块成分股刷新/对账脚本。

关键语义：
- --validate-only 只做离线结构验证，合法股票保持 UNVERIFIED。
- --full-refresh 必须取得权威源；不可用时 fail-closed（exit 2）。
- --reconcile-with-snapshot 使用带 source/as_of_date 的受控权威快照。
- live 与 snapshot 共用同一条 apply/persist/report pipeline。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.sector_validation import (
    AuthoritativeSnapshotError,
    AuthoritativeSourceUnavailableError,
    ReconciliationMethod,
    SectorValidationReport,
    check_freshness,
    finalize_authoritative_reconciliation,
    get_stale_sectors,
    get_validation_summary,
    load_authoritative_snapshot,
    load_provenance,
    reconcile_snapshot_to_reports,
    reconcile_with_ths_ifind,
    update_provenance_after_refresh,
    validate_sector,
)


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
    """从 JS 文件加载板块数据。"""
    with open(sector_file, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.search(r"const NEW_SECTORS\s*=\s*(\{[\s\S]*?\});", content)
    if not match:
        raise ValueError("无法找到 NEW_SECTORS 定义")
    js_obj = match.group(1)
    js_obj = re.sub(r"'([^']*)'", r'"\1"', js_obj)
    js_obj = re.sub(r",\s*([}\]])", r"\1", js_obj)
    return json.loads(js_obj)


def run_validation(
    sectors: Dict[str, List[str]],
    sector_filter: Optional[str] = None,
    authoritative_reports: Optional[Dict[str, SectorValidationReport]] = None,
    provenance_dir: Optional[str] = None,
    as_of: Optional[str] = None,
) -> Dict:
    """生成最终报告；已有权威 report 时禁止再降级重建为 UNVERIFIED。

    as_of: 显式评估日期（YYYY-MM-DD）。提供时 freshness/stale 确定性地按该日期计算（CI/回测用）；
           不提供时生产默认使用当前 UTC 日期。
    """
    if sector_filter:
        if sector_filter not in sectors:
            raise ValueError(f"板块 '{sector_filter}' 不存在")
        sectors = {sector_filter: sectors[sector_filter]}

    authoritative_reports = authoritative_reports or {}
    reports: Dict[str, SectorValidationReport] = {}
    for name, codes in sectors.items():
        if name in authoritative_reports:
            reports[name] = authoritative_reports[name]
            continue
        provenance = load_provenance(name, provenance_dir)
        reports[name] = validate_sector(
            sector_name=name,
            codes=codes,
            sector_code=SECTOR_CODE_MAP.get(name, ""),
            provenance=provenance,
        )

    summary = get_validation_summary(reports)
    # 评估日期：显式 as_of 时确定性地按该日期计算；否则生产默认 today（UTC）。
    current_date = as_of if as_of else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    validated_at = f"{as_of}T00:00:00+00:00" if as_of else datetime.now(timezone.utc).isoformat()
    freshness_info = {
        name: check_freshness(name, current_date=current_date, provenance_dir=provenance_dir).value
        for name in sectors
    }
    stale_sectors = get_stale_sectors(
        current_date=current_date,
        provenance_dir=provenance_dir,
    )
    return {
        "reports": {name: report.to_dict() for name, report in reports.items()},
        "summary": summary,
        "freshness": freshness_info,
        "stale_sectors": stale_sectors,
        "validated_at": validated_at,
        "as_of": as_of,
    }


def update_provenances(
    sectors: Dict[str, List[str]],
    sector_filter: Optional[str] = None,
    provenance_dir: Optional[str] = None,
) -> List[str]:
    """只更新本地文件刷新时间，不影响权威对账 freshness。"""
    updated = []
    for name, codes in sectors.items():
        if sector_filter and name != sector_filter:
            continue
        update_provenance_after_refresh(
            sector_name=name,
            sector_code=SECTOR_CODE_MAP.get(name, ""),
            source="manual_refresh",
            total_constituents=len(codes),
            source_url="",
            provenance_dir=provenance_dir,
        )
        updated.append(name)
    return updated


def _run_live_authoritative_refresh(
    sectors: Dict[str, List[str]],
    sector_filter: Optional[str],
    provenance_dir: Optional[str],
    evidence_dir: Optional[str],
) -> Dict[str, SectorValidationReport]:
    """Fetch first, then apply/persist only if every requested authoritative fetch succeeds."""
    names = [sector_filter] if sector_filter else list(sectors.keys())
    recons = {}
    failures = []
    for sector_name in names:
        if sector_name not in sectors:
            failures.append(f"板块不存在: {sector_name}")
            continue
        try:
            recons[sector_name] = reconcile_with_ths_ifind(
                sector_name,
                SECTOR_CODE_MAP.get(sector_name, ""),
                sectors[sector_name],
            )
        except AuthoritativeSourceUnavailableError as exc:
            failures.append(str(exc))

    if failures:
        print("FAIL-CLOSED: 权威源不可用，完整刷新中止。", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise AuthoritativeSourceUnavailableError("; ".join(failures))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reports = {}
    for sector_name, recon in recons.items():
        source_codes = sorted(set(recon.matched) | set(recon.only_in_source))
        finalized = finalize_authoritative_reconciliation(
            sector_name=sector_name,
            sector_code=SECTOR_CODE_MAP.get(sector_name, ""),
            local_codes=sectors[sector_name],
            source_codes=source_codes,
            reconciliation=recon,
            authoritative_source="ths_ifind",
            source_as_of_date=today,
            snapshot_id=f"live-ths-ifind-{SECTOR_CODE_MAP.get(sector_name, sector_name)}-{today}",
            snapshot_sha256="",
            reconciliation_method=ReconciliationMethod.THS_IFIND,
            provenance_dir=provenance_dir,
            evidence_dir=evidence_dir,
        )
        reports[sector_name] = finalized["report"]
    return reports


def print_human_readable_report(result: Dict):
    summary = result["summary"]
    print("=" * 70)
    print("FIR-15 板块成分股验证报告")
    print(f"验证时间: {result['validated_at']}")
    print("=" * 70)
    print(f"板块数量: {summary['total_sectors']}")
    print(f"成分股总数: {summary['total_constituents']}")
    print(f"离线验证通过: {summary.get('total_offline_valid', 0)}")
    print(f"权威对账 VERIFIED: {summary['total_verified']}")
    print(f"未对账 UNVERIFIED: {summary.get('total_unverified', 0)}")
    print(f"无效: {summary['total_invalid']}")
    print(f"重复: {summary['total_duplicates']}")
    print(f"离线验证通过率: {summary.get('overall_offline_valid_rate', 0.0):.2%}")
    print(f"权威对账验证率: {summary['overall_verification_rate']:.2%}")
    print()
    print("新鲜度（基于权威 source as-of date）：")
    for name, freshness in result["freshness"].items():
        print(f"  {name}: {freshness}")
    if result.get("reconciliation_audit"):
        print()
        print("对账审计证据：")
        for name, audit in result["reconciliation_audit"].items():
            print(f"  {name}: {audit.get('evidence_path', 'N/A')}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="FIR-15 板块成分股刷新/验证脚本")
    parser.add_argument("--validate-only", action="store_true",
                        help="仅离线验证；合法股票保持 UNVERIFIED")
    parser.add_argument("--update-provenance", action="store_true",
                        help="只更新 local_refresh_date，不改变权威 freshness")
    parser.add_argument("--full-refresh", action="store_true",
                        help="调用权威 THS/iFinD；不可用时 fail-closed exit 2")
    parser.add_argument("--reconcile-with-snapshot", metavar="SNAPSHOT_FILE",
                        help="使用带 source/as_of_date 的受控权威 snapshot")
    parser.add_argument("--sector", default=None, help="指定板块名称")
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    parser.add_argument("--as-of", default=None, metavar="YYYY-MM-DD",
                        help="计算新鲜度所用的当前日期（默认今天）；受控快照 gate 传快照日期以保证结果确定")
    parser.add_argument("--sector-file", default=None, help="板块数据文件路径")
    parser.add_argument("--provenance-dir", default=None,
                        help="provenance 输出目录（CI/测试可指向临时目录）")
    parser.add_argument("--evidence-dir", default=None,
                        help="reconciliation evidence 输出目录（CI/测试可指向临时目录）")
    args = parser.parse_args()

    sector_file = args.sector_file or os.path.join(PROJECT_ROOT, "sector_data", "new_sectors.js")
    if not os.path.exists(sector_file):
        print(f"错误：板块数据文件不存在: {sector_file}", file=sys.stderr)
        sys.exit(1)

    sectors = load_sectors_from_js(sector_file)
    authoritative_reports: Dict[str, SectorValidationReport] = {}
    reconciliation_audit: Dict[str, Dict] = {}

    if args.full_refresh:
        try:
            authoritative_reports.update(
                _run_live_authoritative_refresh(
                    sectors,
                    args.sector,
                    args.provenance_dir,
                    args.evidence_dir,
                )
            )
        except AuthoritativeSourceUnavailableError:
            sys.exit(2)

    if args.reconcile_with_snapshot:
        try:
            snapshot = load_authoritative_snapshot(args.reconcile_with_snapshot)
            reports, audit = reconcile_snapshot_to_reports(
                snapshot,
                sectors,
                sector_filter=args.sector,
                sector_codes=SECTOR_CODE_MAP,
                provenance_dir=args.provenance_dir,
                evidence_dir=args.evidence_dir,
            )
        except (AuthoritativeSnapshotError, OSError, json.JSONDecodeError) as exc:
            print(f"错误：权威 snapshot 无法使用: {exc}", file=sys.stderr)
            sys.exit(2)
        authoritative_reports.update(reports)
        reconciliation_audit.update(audit)

    if args.update_provenance:
        update_provenances(
            sectors,
            sector_filter=args.sector,
            provenance_dir=args.provenance_dir,
        )

    try:
        result = run_validation(
            sectors,
            sector_filter=args.sector,
            authoritative_reports=authoritative_reports,
            provenance_dir=args.provenance_dir,
            as_of=args.as_of,
        )
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(1)

    result["reconciliation_audit"] = reconciliation_audit

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human_readable_report(result)

    if result["summary"]["total_invalid"] > 0 or result["summary"]["sectors_with_issues"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
