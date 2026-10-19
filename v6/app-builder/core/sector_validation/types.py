"""
FIR-15 — Authoritative Sector Membership Validation
统一数据结构定义

Source of Truth: Python (此文件)
Web 端通过 cross-runtime parity 测试保证字段一致。
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from enum import Enum


class MembershipStatus(Enum):
    """板块成分股验证状态"""
    VERIFIED = "verified"           # 已验证：代码存在且可交易，与权威源对账一致
    UNVERIFIED = "unverified"       # 未验证：尚未与权威源对账
    STALE = "stale"                 # 过期：上次对账超过新鲜度阈值
    INVALID = "invalid"             # 无效：代码格式错误或不存在
    DELISTED = "delisted"           # 已退市：股票已退市
    NOT_MEMBER = "not_member"       # 非成员：权威源中不存在该股票
    DUPLICATE = "duplicate"         # 重复：板块内重复代码


class ReconciliationMethod(Enum):
    """对账方法"""
    THS_IFIND = "ths_ifind"         # 同花顺 iFinD 概念板块成分股接口
    THS_WEB = "ths_web"             # 同花顺网页行情板块成分接口（自动获取）
    MANUAL = "manual"               # 人工维护（无自动对账源）
    NOT_RECONCILED = "not_reconciled"  # 未对账


class FreshnessLevel(Enum):
    """新鲜度等级 — 基于权威对账时间，不是本地文件刷新时间"""
    FRESH = "fresh"                 # 新鲜：权威对账 7天内
    AGING = "aging"                 # 老化：权威对账 7-30天
    STALE = "stale"                 # 过期：权威对账 30天以上
    NOT_RECONCILED = "not_reconciled"  # 未对账：从未进行过权威对账（绝不能视为 FRESH）
    UNKNOWN = "unknown"             # 未知：provenance 不存在


@dataclass
class SectorConstituent:
    """单只板块成分股

    状态流转：
      INVALID (格式错误/非股票)
        ↓ 离线格式验证通过
      UNVERIFIED (格式正确但是股票，尚未与权威源对账)
        ↓ 权威源对账 matched
      VERIFIED (权威源确认成员关系，写入 reconciled_at/source)
        ↓ 权威源对账 local-only
      NOT_MEMBER (本地有但权威源不存在)

    注意：离线验证通过 ≠ VERIFIED。VERIFIED 必须经过权威源对账。
    """
    code: str                              # 股票代码 6位数字
    name: str = ""                         # 股票名称（可选，验证时填充）
    status: MembershipStatus = MembershipStatus.UNVERIFIED  # 验证状态
    verified_at: Optional[str] = None     # 离线验证时间 ISO8601
    reconciled_at: Optional[str] = None   # 权威对账时间 ISO8601（仅 VERIFIED 时有值）
    reconciliation_source: str = ""       # 权威对账源（如 "ths_ifind"）
    source: str = ""                       # 数据来源（离线验证源）
    notes: str = ""                        # 备注（如"ST"、"停牌"等）

    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'name': self.name,
            'status': self.status.value,
            'verified_at': self.verified_at,
            'reconciled_at': self.reconciled_at,
            'reconciliation_source': self.reconciliation_source,
            'source': self.source,
            'notes': self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SectorConstituent':
        return cls(
            code=str(data['code']),
            name=str(data.get('name', '')),
            status=MembershipStatus(data.get('status', 'unverified')),
            verified_at=data.get('verified_at'),
            reconciled_at=data.get('reconciled_at'),
            reconciliation_source=str(data.get('reconciliation_source', '')),
            source=str(data.get('source', '')),
            notes=str(data.get('notes', '')),
        )


@dataclass
class SectorProvenance:
    """板块数据溯源 — 每个板块的 provenance 元数据

    关键语义：
    - local_refresh_date: 本地文件刷新时间（可由离线验证更新）
    - authoritative_reconciliation_date: 权威源对账时间（只有成功的权威对账才能更新）
    - 新鲜度基于 authoritative_reconciliation_date，未对账时返回 NOT_RECONCILED
    - 绝不能因为本地文件刷新就将未对账的数据集标记为 FRESH
    """
    sector_name: str                       # 板块名称
    sector_code: str = ""                  # 同花顺板块代码（如 886015）
    source: str = ""                       # 数据来源（如 "ths_ifind"）
    source_url: str = ""                   # 数据来源 URL
    fetched_at: str = ""                   # 拉取时间 ISO8601
    local_refresh_date: str = ""           # 本地文件刷新日期 YYYY-MM-DD
    authoritative_reconciliation_date: Optional[str] = None  # 权威对账日期（只有成功对账才有值）
    reconciliation_method: ReconciliationMethod = ReconciliationMethod.NOT_RECONCILED
    reconciliation_result: str = ""        # 对账结果摘要（如 "matched=95, local_only=3, source_only=2"）
    total_constituents: int = 0            # 成分股总数
    verified_count: int = 0                # 已验证数量（权威对账 matched 的数量）
    version: str = "2.0"                   # provenance 版本（v2.0: 区分本地/权威新鲜度）
    notes: str = ""                         # 备注

    def to_dict(self) -> Dict[str, Any]:
        return {
            'sector_name': self.sector_name,
            'sector_code': self.sector_code,
            'source': self.source,
            'source_url': self.source_url,
            'fetched_at': self.fetched_at,
            'local_refresh_date': self.local_refresh_date,
            'authoritative_reconciliation_date': self.authoritative_reconciliation_date,
            'reconciliation_method': self.reconciliation_method.value,
            'reconciliation_result': self.reconciliation_result,
            'total_constituents': self.total_constituents,
            'verified_count': self.verified_count,
            'version': self.version,
            'notes': self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SectorProvenance':
        # 向后兼容：v1.0 使用 refresh_date，映射到 local_refresh_date
        local_refresh = data.get('local_refresh_date', '')
        if not local_refresh and data.get('refresh_date'):
            local_refresh = data['refresh_date']

        return cls(
            sector_name=str(data['sector_name']),
            sector_code=str(data.get('sector_code', '')),
            source=str(data.get('source', '')),
            source_url=str(data.get('source_url', '')),
            fetched_at=str(data.get('fetched_at', '')),
            local_refresh_date=local_refresh,
            authoritative_reconciliation_date=data.get('authoritative_reconciliation_date'),
            reconciliation_method=ReconciliationMethod(data.get('reconciliation_method', 'not_reconciled')),
            reconciliation_result=str(data.get('reconciliation_result', '')),
            total_constituents=int(data.get('total_constituents', 0)),
            verified_count=int(data.get('verified_count', 0)),
            version=str(data.get('version', '2.0')),
            notes=str(data.get('notes', '')),
        )

    def get_freshness_level(self, current_date: str, max_fresh_days: int = 7, max_aging_days: int = 30) -> FreshnessLevel:
        """计算新鲜度等级 — 基于权威对账时间，不是本地文件刷新时间

        关键规则：
        - 从未进行过权威对账 → NOT_RECONCILED（绝不能视为 FRESH）
        - 权威对账 7天内 → FRESH
        - 权威对账 7-30天 → AGING
        - 权威对账 30天以上 → STALE
        """
        # 未进行过权威对账 → NOT_RECONCILED（fail-closed，不虚假标记 FRESH）
        if not self.authoritative_reconciliation_date:
            return FreshnessLevel.NOT_RECONCILED

        try:
            from datetime import datetime
            recon_date = datetime.strptime(self.authoritative_reconciliation_date, '%Y-%m-%d')
            current = datetime.strptime(current_date, '%Y-%m-%d')
            days = (current - recon_date).days
            if days <= max_fresh_days:
                return FreshnessLevel.FRESH
            elif days <= max_aging_days:
                return FreshnessLevel.AGING
            else:
                return FreshnessLevel.STALE
        except (ValueError, TypeError):
            return FreshnessLevel.UNKNOWN


@dataclass
class ReconciliationResult:
    """对账结果"""
    sector_name: str                       # 板块名称
    matched: List[str] = field(default_factory=list)      # 本地和源都存在的代码
    only_in_local: List[str] = field(default_factory=list)  # 仅本地存在的代码
    only_in_source: List[str] = field(default_factory=list)  # 仅源存在的代码
    source: str = ""                       # 对账源
    reconciled_at: str = ""                # 对账时间 ISO8601

    @property
    def match_rate(self) -> float:
        """匹配率 = matched / max(local_total, source_total)"""
        total = max(len(self.matched) + len(self.only_in_local),
                    len(self.matched) + len(self.only_in_source))
        if total == 0:
            return 0.0
        return len(self.matched) / total

    def to_dict(self) -> Dict[str, Any]:
        return {
            'sector_name': self.sector_name,
            'matched': self.matched,
            'only_in_local': self.only_in_local,
            'only_in_source': self.only_in_source,
            'source': self.source,
            'reconciled_at': self.reconciled_at,
            'match_rate': round(self.match_rate, 4),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ReconciliationResult':
        return cls(
            sector_name=str(data['sector_name']),
            matched=list(data.get('matched', [])),
            only_in_local=list(data.get('only_in_local', [])),
            only_in_source=list(data.get('only_in_source', [])),
            source=str(data.get('source', '')),
            reconciled_at=str(data.get('reconciled_at', '')),
        )


@dataclass
class SectorValidationReport:
    """单个板块的验证报告"""
    sector_name: str                       # 板块名称
    sector_code: str = ""                  # 板块代码
    total: int = 0                         # 成分股总数
    verified: int = 0                      # 已验证数量
    unverified: int = 0                    # 未验证数量
    stale: int = 0                         # 过期数量
    invalid: int = 0                       # 无效数量
    delisted: int = 0                      # 已退市数量
    duplicates: List[str] = field(default_factory=list)  # 重复代码列表
    constituents: List[SectorConstituent] = field(default_factory=list)  # 成分股详情
    provenance: Optional[SectorProvenance] = None  # provenance 元数据
    reconciliation: Optional[ReconciliationResult] = None  # 对账结果
    validated_at: str = ""                 # 验证时间 ISO8601

    @property
    def offline_valid_count(self) -> int:
        """离线验证通过数量 = verified + unverified（格式正确且是股票）"""
        return self.verified + self.unverified

    @property
    def offline_valid_rate(self) -> float:
        """离线验证通过率 = (verified + unverified) / total
        反映代码格式正确性，不代表权威成员关系已对账。
        """
        if self.total == 0:
            return 0.0
        return self.offline_valid_count / self.total

    @property
    def verification_rate(self) -> float:
        """权威对账验证率 = verified / total
        只有经过权威源对账 matched 的成分股才计入 verified。
        离线验证通过但未对账的计入 unverified，不计入此比率。
        """
        if self.total == 0:
            return 0.0
        return self.verified / self.total

    @property
    def reconciliation_coverage(self) -> float:
        """权威对账覆盖率 = verified / offline_valid_count
        在离线验证通过的成分股中，有多少已经过权威对账确认。
        """
        if self.offline_valid_count == 0:
            return 0.0
        return self.verified / self.offline_valid_count

    @property
    def has_issues(self) -> bool:
        """是否存在真正的问题（无效代码、过期、重复）

        注意：未对账（unverified 多）不是问题，是正常状态。
        只有 invalid > 0、duplicates > 0、stale > 0 才是真正的问题。
        """
        return (self.invalid > 0 or self.stale > 0 or
                len(self.duplicates) > 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'sector_name': self.sector_name,
            'sector_code': self.sector_code,
            'total': self.total,
            'verified': self.verified,
            'unverified': self.unverified,
            'stale': self.stale,
            'invalid': self.invalid,
            'delisted': self.delisted,
            'duplicates': self.duplicates,
            'constituents': [c.to_dict() for c in self.constituents],
            'provenance': self.provenance.to_dict() if self.provenance else None,
            'reconciliation': self.reconciliation.to_dict() if self.reconciliation else None,
            'validated_at': self.validated_at,
            'offline_valid_count': self.offline_valid_count,
            'offline_valid_rate': round(self.offline_valid_rate, 4),
            'verification_rate': round(self.verification_rate, 4),
            'reconciliation_coverage': round(self.reconciliation_coverage, 4),
            'has_issues': self.has_issues,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SectorValidationReport':
        return cls(
            sector_name=str(data['sector_name']),
            sector_code=str(data.get('sector_code', '')),
            total=int(data.get('total', 0)),
            verified=int(data.get('verified', 0)),
            unverified=int(data.get('unverified', 0)),
            stale=int(data.get('stale', 0)),
            invalid=int(data.get('invalid', 0)),
            delisted=int(data.get('delisted', 0)),
            duplicates=list(data.get('duplicates', [])),
            constituents=[SectorConstituent.from_dict(c) for c in data.get('constituents', [])],
            provenance=SectorProvenance.from_dict(data['provenance']) if data.get('provenance') else None,
            reconciliation=ReconciliationResult.from_dict(data['reconciliation']) if data.get('reconciliation') else None,
            validated_at=str(data.get('validated_at', '')),
        )
