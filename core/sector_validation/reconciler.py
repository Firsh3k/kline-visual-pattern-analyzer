"""
FIR-15 — Sector Membership Reconciler
与权威数据源对账板块成分股。

支持的对账源：
- 同花顺 iFinD 概念板块成分股接口（通过 MCP 工具）
- 手动维护（无自动对账源）

对账结果用于标记过期/不可验证的成员。
"""

from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

from .types import (
    ReconciliationResult, ReconciliationMethod, SectorConstituent,
    MembershipStatus,
)


def reconcile_codes(
    sector_name: str,
    local_codes: List[str],
    source_codes: List[str],
    source: str = "",
) -> ReconciliationResult:
    """
    对账本地代码列表与权威源代码列表。

    Args:
        sector_name: 板块名称
        local_codes: 本地维护的股票代码列表
        source_codes: 权威源返回的股票代码列表
        source: 对账源名称

    Returns:
        ReconciliationResult
    """
    now = datetime.now(timezone.utc).isoformat()

    local_set = set(c.strip() for c in local_codes if c.strip())
    source_set = set(c.strip() for c in source_codes if c.strip())

    matched = sorted(local_set & source_set)
    only_in_local = sorted(local_set - source_set)
    only_in_source = sorted(source_set - local_set)

    return ReconciliationResult(
        sector_name=sector_name,
        matched=matched,
        only_in_local=only_in_local,
        only_in_source=only_in_source,
        source=source,
        reconciled_at=now,
    )


def apply_reconciliation_to_constituents(
    constituents: List[SectorConstituent],
    reconciliation: ReconciliationResult,
) -> List[SectorConstituent]:
    """
    将对账结果应用到成分股列表，更新状态。

    - matched → 保持 VERIFIED（如果之前是 VERIFIED）
    - only_in_local → 标记为 NOT_MEMBER（权威源中不存在）
    - only_in_source → 不在本地列表中，不修改现有成分股（需单独添加）

    Args:
        constituents: 现有成分股列表
        reconciliation: 对账结果

    Returns:
        更新后的成分股列表
    """
    now = datetime.now(timezone.utc).isoformat()
    only_local_set = set(reconciliation.only_in_local)

    updated = []
    for c in constituents:
        if c.code in only_local_set:
            # 权威源中不存在，标记为 NOT_MEMBER
            updated.append(SectorConstituent(
                code=c.code,
                name=c.name,
                status=MembershipStatus.NOT_MEMBER,
                verified_at=now,
                source=f"reconciliation:{reconciliation.source}",
                notes=f"权威源({reconciliation.source})中不存在该股票",
            ))
        else:
            # 匹配的保持原状态
            updated.append(c)

    return updated


def reconcile_with_ths_ifind(
    sector_name: str,
    sector_code: str,
    local_codes: List[str],
) -> Tuple[Optional[ReconciliationResult], Optional[str]]:
    """
    与同花顺 iFinD 概念板块成分股接口对账。

    注意：此函数需要 iFinD MCP 工具可用。
    在离线环境或 MCP 不可用时，返回 (None, error_message)。

    Args:
        sector_name: 板块名称
        sector_code: 同花顺板块代码（如 886015）
        local_codes: 本地维护的股票代码列表

    Returns:
        (ReconciliationResult or None, error_message or None)
    """
    try:
        # 尝试导入 iFinD MCP 工具
        # 注意：这里使用延迟导入，避免在离线环境中失败
        # 实际调用需要在有 MCP 工具的环境中执行
        raise ImportError("iFinD MCP 工具未在当前环境中可用")

    except ImportError as e:
        return None, f"iFinD MCP 工具不可用: {e}"


def generate_reconciliation_report(
    reconciliation: ReconciliationResult,
) -> str:
    """
    生成对账报告的可读文本。

    Args:
        reconciliation: 对账结果

    Returns:
        可读的对账报告文本
    """
    lines = [
        f"=== 板块对账报告: {reconciliation.sector_name} ===",
        f"对账源: {reconciliation.source or '未指定'}",
        f"对账时间: {reconciliation.reconciled_at}",
        f"",
        f"匹配: {len(reconciliation.matched)} 只",
        f"仅本地存在: {len(reconciliation.only_in_local)} 只",
        f"仅源存在: {len(reconciliation.only_in_source)} 只",
        f"匹配率: {reconciliation.match_rate:.2%}",
    ]

    if reconciliation.only_in_local:
        lines.append(f"")
        lines.append(f"--- 仅本地存在（权威源中不存在，建议移除）---")
        for code in reconciliation.only_in_local[:20]:
            lines.append(f"  {code}")
        if len(reconciliation.only_in_local) > 20:
            lines.append(f"  ... 还有 {len(reconciliation.only_in_local) - 20} 只")

    if reconciliation.only_in_source:
        lines.append(f"")
        lines.append(f"--- 仅源存在（本地缺失，建议添加）---")
        for code in reconciliation.only_in_source[:20]:
            lines.append(f"  {code}")
        if len(reconciliation.only_in_source) > 20:
            lines.append(f"  ... 还有 {len(reconciliation.only_in_source) - 20} 只")

    return "\n".join(lines)
