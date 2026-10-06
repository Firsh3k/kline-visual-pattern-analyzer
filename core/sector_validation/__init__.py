"""
FIR-15 — Authoritative Sector Membership Validation
权威板块成员验证模块

验证板块成分股的代码格式、存在性和可交易性，
与权威数据源对账，管理 provenance 元数据。

FIR-15 contracts are Python-only/internal and intentionally live under
``core.sector_validation`` rather than the FIR-14 JS↔Python canonical surface.
"""

from .types import (
    MembershipStatus,
    ReconciliationMethod,
    FreshnessLevel,
    SectorConstituent,
    SectorProvenance,
    ReconciliationResult,
    SectorValidationReport,
)

from .validator import (
    validate_code_format,
    is_stock_code,
    detect_potential_delisting,
    validate_constituent,
    validate_sector,
    validate_all_sectors,
    get_validation_summary,
)

from .reconciler import (
    reconcile_codes,
    apply_reconciliation_to_constituents,
    get_source_only_candidates,
    reconcile_with_ths_ifind,
    reconcile_with_snapshot,
    AuthoritativeSourceUnavailableError,
    generate_reconciliation_report,
)

from .provenance_manager import (
    load_provenance,
    save_provenance,
    update_provenance_after_refresh,
    update_provenance_after_reconciliation,
    check_freshness,
    list_all_provenances,
    get_stale_sectors,
    get_provenance_path,
    DEFAULT_PROVENANCE_DIR,
)

from .pipeline import (
    AuthoritativeSnapshotError,
    DEFAULT_EVIDENCE_DIR,
    SNAPSHOT_SCHEMA_VERSION,
    load_authoritative_snapshot,
    finalize_authoritative_reconciliation,
    reconcile_snapshot_to_reports,
)

# FIR-22 — Dynamic THS Sector Index
from .dynamic_sector import (
    DynamicSector,
    ManualCustomSector,
    SectorCodeValidationError,
    SectorResolutionError,
    ConstituentUnavailableError,
    validate_ths_sector_code,
    normalize_sector_entry,
)
from .ths_sector_resolver import (
    THSSectorResolver,
    THSDataProvider,
    IFindMCPProvider,
    SnapshotProvider,
    load_sectors_from_config,
    merge_dynamic_sectors,
)

__all__ = [
    # 数据结构
    "MembershipStatus",
    "ReconciliationMethod",
    "FreshnessLevel",
    "SectorConstituent",
    "SectorProvenance",
    "ReconciliationResult",
    "SectorValidationReport",
    # 验证器
    "validate_code_format",
    "is_stock_code",
    "detect_potential_delisting",
    "validate_constituent",
    "validate_sector",
    "validate_all_sectors",
    "get_validation_summary",
    # 对账器
    "reconcile_codes",
    "apply_reconciliation_to_constituents",
    "get_source_only_candidates",
    "reconcile_with_ths_ifind",
    "reconcile_with_snapshot",
    "AuthoritativeSourceUnavailableError",
    "generate_reconciliation_report",
    # Provenance 管理器
    "load_provenance",
    "save_provenance",
    "update_provenance_after_refresh",
    "update_provenance_after_reconciliation",
    "check_freshness",
    "list_all_provenances",
    "get_stale_sectors",
    "get_provenance_path",
    "DEFAULT_PROVENANCE_DIR",
    # Shared authoritative pipeline
    "AuthoritativeSnapshotError",
    "DEFAULT_EVIDENCE_DIR",
    "SNAPSHOT_SCHEMA_VERSION",
    "load_authoritative_snapshot",
    "finalize_authoritative_reconciliation",
    "reconcile_snapshot_to_reports",
    # FIR-22 — Dynamic THS Sector Index
    "DynamicSector",
    "ManualCustomSector",
    "THSSectorResolver",
    "THSDataProvider",
    "IFindMCPProvider",
    "SnapshotProvider",
    "SectorCodeValidationError",
    "SectorResolutionError",
    "ConstituentUnavailableError",
    "validate_ths_sector_code",
    "normalize_sector_entry",
    "load_sectors_from_config",
    "merge_dynamic_sectors",
]
