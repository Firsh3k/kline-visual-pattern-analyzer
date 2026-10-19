"""
FIR-15 — Shared authoritative sector reconciliation pipeline.

This module is the single apply/persist path for both controlled snapshots and
future live authoritative providers. It prevents provenance and final reports
from drifting into contradictory states.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .provenance_manager import (
    DEFAULT_PROVENANCE_DIR,
    load_provenance,
    save_provenance,
    update_provenance_after_refresh,
    update_provenance_after_reconciliation,
)
from .reconciler import (
    apply_reconciliation_to_constituents,
    get_source_only_candidates,
    reconcile_with_snapshot,
)
from .types import (
    MembershipStatus,
    ReconciliationMethod,
    ReconciliationResult,
    SectorValidationReport,
)
from .validator import validate_sector


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_EVIDENCE_DIR = os.path.join(_PROJECT_ROOT, "sector_data", "reconciliation_evidence")
SNAPSHOT_SCHEMA_VERSION = "1.0"
# A 股快照日期是北京时间的交易日；判断“未来日期”也按北京时间，否则 UTC 服务器在北京时间 0–8 点会误判当天快照为未来。
_CN_TZ = timezone(timedelta(hours=8))


class AuthoritativeSnapshotError(ValueError):
    """Raised when a controlled authoritative snapshot is missing audit metadata."""


def _safe_filename(value: str) -> str:
    return re.sub(r'[^0-9A-Za-z._\-\u4e00-\u9fff]+', '_', value).strip('_') or "snapshot"


def _validate_date(value: str, field_name: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise AuthoritativeSnapshotError(
            f"{field_name} must be YYYY-MM-DD, got {value!r}"
        ) from exc
    today = datetime.now(_CN_TZ).date()
    if parsed > today:
        raise AuthoritativeSnapshotError(
            f"{field_name} cannot be in the future: {value} > {today.isoformat()}"
        )
    return parsed.isoformat()


def _codes_hash(codes: List[str]) -> str:
    canonical = "\n".join(sorted(str(code).strip() for code in codes if str(code).strip()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_authoritative_snapshot(path: str) -> Dict[str, Any]:
    """Load and validate an auditable authoritative membership snapshot.

    Required top-level fields:
      schema_version, source, as_of_date, sectors

    Optional fields:
      snapshot_id, source_url

    ``sectors`` maps sector name to either a list of codes or an object with
    ``codes`` and optional ``sector_code``.
    """
    with open(path, "rb") as f:
        raw = f.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthoritativeSnapshotError(f"Invalid snapshot JSON: {path}") from exc

    if data.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise AuthoritativeSnapshotError(
            f"snapshot schema_version must be {SNAPSHOT_SCHEMA_VERSION!r}"
        )
    source = str(data.get("source", "")).strip()
    if not source:
        raise AuthoritativeSnapshotError("snapshot source is required")
    as_of_date = _validate_date(data.get("as_of_date"), "as_of_date")
    sectors = data.get("sectors")
    if not isinstance(sectors, dict) or not sectors:
        raise AuthoritativeSnapshotError("snapshot sectors must be a non-empty object")

    snapshot_sha256 = hashlib.sha256(raw).hexdigest()
    snapshot_id = str(data.get("snapshot_id", "")).strip() or f"sha256:{snapshot_sha256[:16]}"
    source_url = str(data.get("source_url", "")).strip()

    normalized_sectors: Dict[str, Dict[str, Any]] = {}
    for sector_name, payload in sectors.items():
        if isinstance(payload, list):
            codes = payload
            sector_code = ""
        elif isinstance(payload, dict):
            codes = payload.get("codes")
            sector_code = str(payload.get("sector_code", "")).strip()
        else:
            raise AuthoritativeSnapshotError(
                f"snapshot sector {sector_name!r} must be a code list or object"
            )
        if not isinstance(codes, list) or not codes:
            raise AuthoritativeSnapshotError(
                f"snapshot sector {sector_name!r} must contain a non-empty codes list"
            )
        normalized = [str(code).strip() for code in codes if str(code).strip()]
        if len(normalized) != len(codes):
            raise AuthoritativeSnapshotError(
                f"snapshot sector {sector_name!r} contains blank codes"
            )
        normalized_sectors[str(sector_name)] = {
            "sector_code": sector_code,
            "codes": normalized,
        }

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source": source,
        "source_url": source_url,
        "as_of_date": as_of_date,
        "snapshot_id": snapshot_id,
        "snapshot_sha256": snapshot_sha256,
        "sectors": normalized_sectors,
        "snapshot_path": os.path.abspath(path),
    }


def _build_reconciled_report(
    sector_name: str,
    sector_code: str,
    local_codes: List[str],
    reconciliation: ReconciliationResult,
    provenance,
) -> SectorValidationReport:
    base = validate_sector(
        sector_name=sector_name,
        codes=local_codes,
        sector_code=sector_code,
        provenance=provenance,
    )
    constituents = apply_reconciliation_to_constituents(base.constituents, reconciliation)

    verified = sum(c.status == MembershipStatus.VERIFIED for c in constituents)
    unverified = sum(c.status == MembershipStatus.UNVERIFIED for c in constituents)
    stale = sum(c.status == MembershipStatus.STALE for c in constituents)
    invalid = sum(c.status == MembershipStatus.INVALID for c in constituents)
    delisted = sum(c.status == MembershipStatus.DELISTED for c in constituents)

    return SectorValidationReport(
        sector_name=sector_name,
        sector_code=sector_code,
        total=len(constituents),
        verified=verified,
        unverified=unverified,
        stale=stale,
        invalid=invalid,
        delisted=delisted,
        duplicates=base.duplicates,
        constituents=constituents,
        provenance=provenance,
        reconciliation=reconciliation,
        validated_at=datetime.now(timezone.utc).isoformat(),
    )


def _persist_evidence(
    sector_name: str,
    sector_code: str,
    local_codes: List[str],
    source_codes: List[str],
    reconciliation: ReconciliationResult,
    authoritative_source: str,
    source_as_of_date: str,
    snapshot_id: str,
    snapshot_sha256: str,
    source_url: str,
    evidence_dir: Optional[str],
) -> Tuple[str, Dict[str, Any]]:
    directory = evidence_dir or DEFAULT_EVIDENCE_DIR
    os.makedirs(directory, exist_ok=True)
    filename = (
        f"{_safe_filename(sector_name)}_{source_as_of_date}_"
        f"{_safe_filename(snapshot_id)[:48]}.json"
    )
    path = os.path.join(directory, filename)
    evidence = {
        "schema_version": "1.0",
        "sector_name": sector_name,
        "sector_code": sector_code,
        "authoritative_source": authoritative_source,
        "source_url": source_url,
        "source_as_of_date": source_as_of_date,
        "snapshot_id": snapshot_id,
        "snapshot_sha256": snapshot_sha256,
        "reconciled_at": reconciliation.reconciled_at,
        "local_codes_sha256": _codes_hash(local_codes),
        "source_codes_sha256": _codes_hash(source_codes),
        "matched": reconciliation.matched,
        "only_in_local": reconciliation.only_in_local,
        "only_in_source": reconciliation.only_in_source,
        "match_rate": round(reconciliation.match_rate, 6),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=2)
    return path, evidence


def finalize_authoritative_reconciliation(
    *,
    sector_name: str,
    sector_code: str,
    local_codes: List[str],
    source_codes: List[str],
    reconciliation: ReconciliationResult,
    authoritative_source: str,
    source_as_of_date: str,
    snapshot_id: str,
    snapshot_sha256: str = "",
    source_url: str = "",
    reconciliation_method: ReconciliationMethod = ReconciliationMethod.THS_IFIND,
    provenance_dir: Optional[str] = None,
    evidence_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply, persist and report one successful authoritative reconciliation.

    This is the only success path used by controlled snapshots and future live
    providers. Freshness is based on the source's as-of date, never import time.
    """
    source_as_of_date = _validate_date(source_as_of_date, "source_as_of_date")

    if load_provenance(sector_name, provenance_dir) is None:
        update_provenance_after_refresh(
            sector_name=sector_name,
            sector_code=sector_code,
            source="local_membership_file",
            total_constituents=len(local_codes),
            provenance_dir=provenance_dir,
        )

    evidence_path, evidence = _persist_evidence(
        sector_name=sector_name,
        sector_code=sector_code,
        local_codes=local_codes,
        source_codes=source_codes,
        reconciliation=reconciliation,
        authoritative_source=authoritative_source,
        source_as_of_date=source_as_of_date,
        snapshot_id=snapshot_id,
        snapshot_sha256=snapshot_sha256,
        source_url=source_url,
        evidence_dir=evidence_dir,
    )

    result_summary = (
        f"matched={len(reconciliation.matched)}, "
        f"local_only={len(reconciliation.only_in_local)}, "
        f"source_only={len(reconciliation.only_in_source)}, "
        f"evidence={evidence_path}"
    )
    provenance = update_provenance_after_reconciliation(
        sector_name=sector_name,
        method=reconciliation_method,
        verified_count=len(reconciliation.matched),
        reconciliation_result=result_summary,
        authoritative_reconciliation_date=source_as_of_date,
        provenance_dir=provenance_dir,
    )
    if provenance is None:
        raise RuntimeError(f"Failed to persist provenance for {sector_name}")

    provenance.source = authoritative_source
    provenance.source_url = source_url
    provenance.notes = (
        f"authoritative_source_as_of={source_as_of_date}; "
        f"snapshot_id={snapshot_id}; snapshot_sha256={snapshot_sha256 or 'n/a'}; "
        f"evidence={evidence_path}"
    )
    save_provenance(provenance, provenance_dir)

    report = _build_reconciled_report(
        sector_name=sector_name,
        sector_code=sector_code,
        local_codes=local_codes,
        reconciliation=reconciliation,
        provenance=provenance,
    )
    return {
        "report": report,
        "source_only_candidates": get_source_only_candidates(reconciliation),
        "evidence": evidence,
        "evidence_path": evidence_path,
    }


def reconcile_snapshot_to_reports(
    snapshot: Dict[str, Any],
    local_sectors: Dict[str, List[str]],
    *,
    sector_filter: Optional[str] = None,
    sector_codes: Optional[Dict[str, str]] = None,
    provenance_dir: Optional[str] = None,
    evidence_dir: Optional[str] = None,
) -> Tuple[Dict[str, SectorValidationReport], Dict[str, Any]]:
    """Run controlled authoritative snapshot reconciliation through the real pipeline."""
    snapshot_sectors = snapshot["sectors"]
    if sector_filter:
        if sector_filter not in snapshot_sectors:
            raise AuthoritativeSnapshotError(
                f"requested sector {sector_filter!r} is missing from snapshot"
            )
        names = [sector_filter]
    else:
        names = [name for name in snapshot_sectors if name in local_sectors]

    if not names:
        raise AuthoritativeSnapshotError("snapshot has no sectors present in local membership data")

    reports: Dict[str, SectorValidationReport] = {}
    audit: Dict[str, Any] = {}
    for sector_name in names:
        if sector_name not in local_sectors:
            raise AuthoritativeSnapshotError(
                f"snapshot sector {sector_name!r} does not exist in local membership data"
            )
        entry = snapshot_sectors[sector_name]
        source_codes = entry["codes"]
        local_codes = local_sectors[sector_name]
        sector_code = entry.get("sector_code") or (sector_codes or {}).get(sector_name, "")
        recon_source = f"{snapshot['source']}:{snapshot['snapshot_id']}"
        reconciliation = reconcile_with_snapshot(
            sector_name,
            local_codes,
            source_codes,
            source=recon_source,
        )
        method = (
            ReconciliationMethod.THS_IFIND
            if snapshot["source"].lower() in {"ths", "ths_ifind", "ifind", "tonghuashun"}
            else ReconciliationMethod.MANUAL
        )
        finalized = finalize_authoritative_reconciliation(
            sector_name=sector_name,
            sector_code=sector_code,
            local_codes=local_codes,
            source_codes=source_codes,
            reconciliation=reconciliation,
            authoritative_source=snapshot["source"],
            source_as_of_date=snapshot["as_of_date"],
            snapshot_id=snapshot["snapshot_id"],
            snapshot_sha256=snapshot["snapshot_sha256"],
            source_url=snapshot.get("source_url", ""),
            reconciliation_method=method,
            provenance_dir=provenance_dir,
            evidence_dir=evidence_dir,
        )
        reports[sector_name] = finalized["report"]
        audit[sector_name] = {
            "source_only_candidates": finalized["source_only_candidates"],
            "evidence_path": finalized["evidence_path"],
        }

    return reports, audit
