"""
FIR-15 — Authoritative Sector Membership Validation
权威板块成员验证模块

验证板块成分股的代码格式、存在性和可交易性，
与权威数据源对账，管理 provenance 元数据。

## 模块结构

- types.py — 数据结构定义（SectorConstituent, SectorProvenance, ReconciliationResult, SectorValidationReport）
- validator.py — 离线验证器（代码格式、存在性、可交易性）
- reconciler.py — 对账器（与权威数据源对账）
- provenance_manager.py — Provenance 管理器（来源、刷新日期、新鲜度）

## 使用示例

```python
from core.sector_validation import validate_sector, load_provenance, reconcile_codes

# 离线验证
report = validate_sector("创新药", ["000538", "600276", "invalid"])
print(f"验证率: {report.verification_rate:.2%}")
print(f"无效代码: {report.invalid}")

# 加载 provenance
provenance = load_provenance("创新药")
if provenance:
    print(f"刷新日期: {provenance.refresh_date}")

# 对账
result = reconcile_codes("创新药", local_codes, source_codes, source="ths_ifind")
print(f"匹配率: {result.match_rate:.2%}")
```
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
    reconcile_with_ths_ifind,
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
    "reconcile_with_ths_ifind",
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
]
