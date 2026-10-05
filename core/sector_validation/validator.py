"""
FIR-15 — Sector Membership Validator
离线验证板块成分股的代码格式、存在性和可交易性。

所有验证都是确定性的，不需要网络连接。
外部对账（与同花顺 iFinD）由 reconciler.py 处理。
"""

import re
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Optional, Any

from .types import (
    SectorConstituent, SectorValidationReport, MembershipStatus,
    SectorProvenance,
)


# A 股交易所代码前缀权威范围
# 参考：上交所/深交所/北交所官方代码分配表
VALID_CODE_PREFIXES = {
    # 上海证券交易所
    '60': 'SSE_MAIN',       # 沪市主板
    '68': 'SSE_STAR',       # 科创板
    '90': 'SSE_B',          # 沪市B股（权威范围 900000-900999）
    '51': 'SSE_ETF',        # 沪市ETF
    '56': 'SSE_ETF',        # 沪市ETF
    '58': 'SSE_ETF',        # 沪市ETF
    # 深圳证券交易所
    '00': 'SZSE_MAIN',      # 深市主板
    '30': 'SZSE_CHINEXT',   # 创业板
    '15': 'SZSE_ETF',       # 深市ETF
    '16': 'SZSE_FUND',      # 深市基金
    # 北京证券交易所
    '43': 'BSE_OLD',        # 北交所（原新三板精选层，历史代码）
    '83': 'BSE_OLD',        # 北交所（历史代码）
    '87': 'BSE_OLD',        # 北交所（历史代码）
    '88': 'BSE_OLD',        # 北交所（历史代码）
    '92': 'BSE_NEW',        # 北交所（2024-04-22 启用的新代码段）
}

# 股票类代码前缀（排除 ETF/基金）
STOCK_PREFIXES = {'60', '68', '90', '00', '30', '43', '83', '87', '88', '92'}

# 沪市 B 股权威范围（900000-900999）
SSE_B_SHARE_MIN = 900000
SSE_B_SHARE_MAX = 900999

# 北交所新代码段范围（920000-920999）
BSE_NEW_MIN = 920000
BSE_NEW_MAX = 920999


def validate_code_format(code: str) -> Tuple[bool, str]:
    """
    验证股票代码格式。

    Returns:
        (is_valid, error_message)
    """
    if not code:
        return False, "代码为空"

    if not re.match(r'^\d{6}$', code):
        return False, f"代码格式错误（应为6位数字）: {code}"

    prefix = code[:2]
    if prefix not in VALID_CODE_PREFIXES:
        return False, f"代码前缀不在已知交易所范围内: {code} (前缀{prefix})"

    # 沪市 B 股精确范围检查
    if prefix == '90':
        code_num = int(code)
        if code_num < SSE_B_SHARE_MIN or code_num > SSE_B_SHARE_MAX:
            return False, f"沪市B股代码超出权威范围(900000-900999): {code}"

    # 北交所新代码段范围检查
    if prefix == '92':
        code_num = int(code)
        if code_num < BSE_NEW_MIN or code_num > BSE_NEW_MAX:
            return False, f"北交所新代码超出范围(920000-920999): {code}"

    return True, ""


def is_stock_code(code: str) -> bool:
    """判断是否为股票代码（排除 ETF/基金）"""
    if not re.match(r'^\d{6}$', code):
        return False
    return code[:2] in STOCK_PREFIXES


def detect_potential_delisting(code: str) -> Optional[str]:
    """
    基于代码规则检测潜在退市风险。
    注意：这是基于代码前缀的启发式检测，不是实时退市状态查询。
    实时状态需要通过权威数据源确认。

    Returns:
        风险提示字符串，或 None（无明显风险）
    """
    # 退市整理期代码通常以 400/420 开头（老三板），不在 A 股主板范围内
    # 这里只做基于代码的基本检查
    prefix = code[:2]

    # 北交所历史代码（43/83/87/88）可能存在流动性较差的股票
    if prefix in ('43', '83', '87', '88'):
        return "北交所历史代码段，需确认当前交易状态"

    return None


def validate_constituent(code: str, name: str = "") -> SectorConstituent:
    """
    验证单只板块成分股。

    Args:
        code: 股票代码
        name: 股票名称（可选）

    Returns:
        SectorConstituent with validation status
    """
    now = datetime.now(timezone.utc).isoformat()

    # 格式验证
    is_valid, error_msg = validate_code_format(code)
    if not is_valid:
        return SectorConstituent(
            code=code,
            name=name,
            status=MembershipStatus.INVALID,
            verified_at=now,
            source="offline_validator",
            notes=error_msg,
        )

    # 股票类型检查
    if not is_stock_code(code):
        return SectorConstituent(
            code=code,
            name=name,
            status=MembershipStatus.INVALID,
            verified_at=now,
            source="offline_validator",
            notes=f"非股票类代码（可能是ETF/基金）: {code}",
        )

    # 潜在退市风险检测
    delisting_note = detect_potential_delisting(code)
    notes = delisting_note if delisting_note else ""

    # 离线验证通过（格式正确、是股票代码、在已知交易所范围内）
    # 关键语义：离线验证通过 → UNVERIFIED，不是 VERIFIED
    # VERIFIED 必须经过权威源对账 matched 后才能获得（由 reconciler 处理）
    return SectorConstituent(
        code=code,
        name=name,
        status=MembershipStatus.UNVERIFIED,
        verified_at=now,
        source="offline_validator",
        notes=notes,
    )


def validate_sector(
    sector_name: str,
    codes: List[str],
    sector_code: str = "",
    provenance: Optional[SectorProvenance] = None,
) -> SectorValidationReport:
    """
    验证整个板块的成分股。

    Args:
        sector_name: 板块名称
        codes: 股票代码列表
        sector_code: 板块代码（可选）
        provenance: 板块 provenance 元数据（可选）

    Returns:
        SectorValidationReport
    """
    now = datetime.now(timezone.utc).isoformat()
    constituents: List[SectorConstituent] = []
    duplicates: List[str] = []
    seen: Dict[str, int] = {}

    # 统计
    verified = 0
    unverified = 0
    stale = 0
    invalid = 0
    delisted = 0

    for code in codes:
        code = code.strip()
        if not code:
            continue

        # 重复检测
        if code in seen:
            seen[code] += 1
            if seen[code] == 2:  # 第二次出现时记录
                duplicates.append(code)
            constituents.append(SectorConstituent(
                code=code,
                status=MembershipStatus.DUPLICATE,
                verified_at=now,
                source="offline_validator",
                notes=f"板块内重复代码（第{seen[code]}次出现）",
            ))
            continue
        else:
            seen[code] = 1

        # 验证单只股票
        constituent = validate_constituent(code)
        constituents.append(constituent)

        # 统计
        if constituent.status == MembershipStatus.VERIFIED:
            verified += 1
        elif constituent.status == MembershipStatus.UNVERIFIED:
            unverified += 1
        elif constituent.status == MembershipStatus.STALE:
            stale += 1
        elif constituent.status == MembershipStatus.INVALID:
            invalid += 1
        elif constituent.status == MembershipStatus.DELISTED:
            delisted += 1

    total = len(constituents)

    return SectorValidationReport(
        sector_name=sector_name,
        sector_code=sector_code,
        total=total,
        verified=verified,
        unverified=unverified,
        stale=stale,
        invalid=invalid,
        delisted=delisted,
        duplicates=duplicates,
        constituents=constituents,
        provenance=provenance,
        validated_at=now,
    )


def validate_all_sectors(
    sectors: Dict[str, List[str]],
    sector_codes: Optional[Dict[str, str]] = None,
    provenances: Optional[Dict[str, SectorProvenance]] = None,
) -> Dict[str, SectorValidationReport]:
    """
    验证所有板块。

    Args:
        sectors: {板块名称: [股票代码列表]}
        sector_codes: {板块名称: 板块代码}（可选）
        provenances: {板块名称: SectorProvenance}（可选）

    Returns:
        {板块名称: SectorValidationReport}
    """
    reports = {}
    for name, codes in sectors.items():
        report = validate_sector(
            sector_name=name,
            codes=codes,
            sector_code=(sector_codes or {}).get(name, ""),
            provenance=(provenances or {}).get(name),
        )
        reports[name] = report
    return reports


def get_validation_summary(reports: Dict[str, SectorValidationReport]) -> Dict[str, Any]:
    """
    生成所有板块验证的汇总统计。

    Returns:
        {
            'total_sectors': int,
            'total_constituents': int,
            'total_verified': int,
            'total_invalid': int,
            'total_duplicates': int,
            'sectors_with_issues': [str],
            'overall_verification_rate': float,
        }
    """
    total_sectors = len(reports)
    total_constituents = sum(r.total for r in reports.values())
    total_verified = sum(r.verified for r in reports.values())  # 权威对账 matched
    total_unverified = sum(r.unverified for r in reports.values())  # 离线验证通过但未对账
    total_invalid = sum(r.invalid for r in reports.values())
    total_duplicates = sum(len(r.duplicates) for r in reports.values())
    sectors_with_issues = [name for name, r in reports.items() if r.has_issues]

    # 离线验证通过率 = (verified + unverified) / total
    # 权威对账验证率 = verified / total
    total_offline_valid = total_verified + total_unverified
    offline_valid_rate = total_offline_valid / total_constituents if total_constituents > 0 else 0.0
    overall_rate = total_verified / total_constituents if total_constituents > 0 else 0.0

    return {
        'total_sectors': total_sectors,
        'total_constituents': total_constituents,
        'total_verified': total_verified,  # 权威对账 matched
        'total_unverified': total_unverified,  # 离线验证通过但未对账
        'total_offline_valid': total_offline_valid,  # 离线验证通过总数
        'total_invalid': total_invalid,
        'total_duplicates': total_duplicates,
        'sectors_with_issues': sectors_with_issues,
        'overall_offline_valid_rate': round(offline_valid_rate, 4),  # 离线验证通过率
        'overall_verification_rate': round(overall_rate, 4),  # 权威对账验证率
    }
