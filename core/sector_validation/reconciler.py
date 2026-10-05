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

    状态流转：
    - matched（权威源存在）→ 从 UNVERIFIED 升级为 VERIFIED，写入 reconciled_at/source
    - only_in_local（本地有但权威源没有）→ 标记为 NOT_MEMBER
    - INVALID / DUPLICATE 等保持原状态（对账不影响格式验证结果）

    注意：only_in_source（权威源有但本地没有）不在现有成分股列表中，
    需通过 get_source_only_candidates() 获取，作为明确新增候选。

    Args:
        constituents: 现有成分股列表
        reconciliation: 对账结果

    Returns:
        更新后的成分股列表
    """
    now = datetime.now(timezone.utc).isoformat()
    matched_set = set(reconciliation.matched)
    only_local_set = set(reconciliation.only_in_local)
    recon_source = reconciliation.source or "authoritative"

    updated = []
    for c in constituents:
        if c.code in only_local_set:
            # 权威源中不存在，标记为 NOT_MEMBER
            updated.append(SectorConstituent(
                code=c.code,
                name=c.name,
                status=MembershipStatus.NOT_MEMBER,
                verified_at=c.verified_at,
                reconciled_at=now,
                reconciliation_source=recon_source,
                source=f"reconciliation:{recon_source}",
                notes=f"权威源({recon_source})中不存在该股票",
            ))
        elif c.code in matched_set and c.status == MembershipStatus.UNVERIFIED:
            # 权威源匹配成功，从 UNVERIFIED 升级为 VERIFIED
            updated.append(SectorConstituent(
                code=c.code,
                name=c.name,
                status=MembershipStatus.VERIFIED,
                verified_at=c.verified_at,
                reconciled_at=now,
                reconciliation_source=recon_source,
                source=c.source,
                notes=c.notes,
            ))
        elif c.code in matched_set and c.status == MembershipStatus.VERIFIED:
            # 已经是 VERIFIED，更新对账时间
            updated.append(SectorConstituent(
                code=c.code,
                name=c.name,
                status=MembershipStatus.VERIFIED,
                verified_at=c.verified_at,
                reconciled_at=now,
                reconciliation_source=recon_source,
                source=c.source,
                notes=c.notes,
            ))
        else:
            # 其他状态（INVALID、DUPLICATE 等）保持不变
            updated.append(c)

    return updated


def get_source_only_candidates(
    reconciliation: ReconciliationResult,
) -> List[Dict[str, str]]:
    """
    获取权威源有但本地没有的成分股（明确新增候选）。

    这些股票在权威源中存在，但不在本地板块列表中。
    应该作为明确的新增候选呈现给用户，由用户决定是否添加。

    Args:
        reconciliation: 对账结果

    Returns:
        新增候选列表，每个元素包含 code 和 source
    """
    return [
        {'code': code, 'source': reconciliation.source or 'authoritative'}
        for code in reconciliation.only_in_source
    ]


class AuthoritativeSourceUnavailableError(Exception):
    """权威数据源不可用异常 — fail-closed

    当权威源（如同花顺 iFinD）不可用时抛出。
    调用方必须处理此异常，绝不能静默降级为离线验证并标记 FRESH。
    """
    pass


def reconcile_with_ths_ifind(
    sector_name: str,
    sector_code: str,
    local_codes: List[str],
) -> ReconciliationResult:
    """
    与同花顺 iFinD 概念板块成分股接口对账。

    Fail-closed 语义：
    - iFinD MCP 工具不可用时 → 抛出 AuthoritativeSourceUnavailableError
    - 绝不静默降级为离线验证
    - 绝不因为权威源不可用就更新成员新鲜度

    注意：此函数需要 iFinD MCP 工具可用。
    在离线环境中，请使用 reconcile_with_snapshot() 进行受控对账。

    Args:
        sector_name: 板块名称
        sector_code: 同花顺板块代码（如 886015）
        local_codes: 本地维护的股票代码列表

    Returns:
        ReconciliationResult

    Raises:
        AuthoritativeSourceUnavailableError: iFinD MCP 工具不可用时
    """
    try:
        # 尝试导入 iFinD MCP 工具
        # 实际调用需要在有 MCP 工具的环境中执行
        # 这里保留接口，具体实现需要在有 iFinD MCP 的运行时中完成
        raise ImportError("iFinD MCP 工具未在当前环境中可用")

    except ImportError as e:
        raise AuthoritativeSourceUnavailableError(
            f"同花顺 iFinD 权威源不可用，无法对板块 '{sector_name}' ({sector_code}) 进行对账。 "
            f"原因: {e}. "
            f"请在有 iFinD MCP 工具的环境中运行，或使用 reconcile_with_snapshot() 进行受控对账。"
        ) from e


def reconcile_with_snapshot(
    sector_name: str,
    local_codes: List[str],
    source_codes: List[str],
    source: str = "controlled_snapshot",
) -> ReconciliationResult:
    """
    使用受控 snapshot 进行对账（用于测试、CI 和离线受控环境）。

    与 reconcile_with_ths_ifind() 不同，此函数接受预先获取的权威源代码列表，
    不依赖实时 MCP 连接。snapshot 必须有明确的来源和时间戳。

    Args:
        sector_name: 板块名称
        local_codes: 本地维护的股票代码列表
        source_codes: 权威源 snapshot 的股票代码列表
        source: snapshot 来源标识（如 "ths_ifind_snapshot_2026-10-05"）

    Returns:
        ReconciliationResult
    """
    return reconcile_codes(sector_name, local_codes, source_codes, source=source)


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
