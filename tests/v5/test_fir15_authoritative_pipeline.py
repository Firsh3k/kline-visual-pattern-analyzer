"""FIR-15 end-to-end authoritative reconciliation regressions."""

import json
import os

import pytest

from core.sector_validation import (
    AuthoritativeSnapshotError,
    FreshnessLevel,
    MembershipStatus,
    check_freshness,
    load_authoritative_snapshot,
    load_provenance,
    reconcile_snapshot_to_reports,
)
from scripts.refresh_sector_membership import load_sectors_from_js, run_validation


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SECTOR_FILE = os.path.join(ROOT, "sector_data", "new_sectors.js")
FIXTURE = os.path.join(ROOT, "tests", "v5", "fixtures", "fir15_innovation_drug_snapshot.json")


def test_real_innovation_drug_snapshot_flows_into_final_report(tmp_path):
    """Real production fixture must remain VERIFIED in the final report, not reset to UNVERIFIED."""
    sectors = load_sectors_from_js(SECTOR_FILE)
    snapshot = load_authoritative_snapshot(FIXTURE)
    provenance_dir = str(tmp_path / "provenance")
    evidence_dir = str(tmp_path / "evidence")

    reports, audit = reconcile_snapshot_to_reports(
        snapshot,
        sectors,
        sector_filter="创新药",
        sector_codes={"创新药": "886015"},
        provenance_dir=provenance_dir,
        evidence_dir=evidence_dir,
    )
    report = reports["创新药"]

    assert report.total == len(sectors["创新药"])
    assert report.verified == report.total
    assert report.unverified == 0
    assert report.verification_rate == 1.0
    assert all(c.status == MembershipStatus.VERIFIED for c in report.constituents)

    final = run_validation(
        sectors,
        sector_filter="创新药",
        authoritative_reports=reports,
        provenance_dir=provenance_dir,
    )
    assert final["summary"]["total_verified"] == report.total
    assert final["summary"]["overall_verification_rate"] == 1.0
    assert final["reports"]["创新药"]["verified"] == report.total
    assert final["reports"]["创新药"]["unverified"] == 0

    evidence_path = audit["创新药"]["evidence_path"]
    assert os.path.exists(evidence_path)
    with open(evidence_path, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    assert evidence["authoritative_source"] == "ths_ifind"
    assert evidence["source_as_of_date"] == "2026-10-02"
    assert evidence["snapshot_id"] == "ths-ifind-886015-2026-10-02-audit001"
    assert len(evidence["snapshot_sha256"]) == 64
    assert evidence["only_in_local"] == []
    assert evidence["only_in_source"] == []


def test_snapshot_as_of_date_drives_freshness_not_import_time(tmp_path):
    sectors = load_sectors_from_js(SECTOR_FILE)
    snapshot = load_authoritative_snapshot(FIXTURE)
    provenance_dir = str(tmp_path / "provenance")

    reconcile_snapshot_to_reports(
        snapshot,
        sectors,
        sector_filter="创新药",
        sector_codes={"创新药": "886015"},
        provenance_dir=provenance_dir,
        evidence_dir=str(tmp_path / "evidence"),
    )

    provenance = load_provenance("创新药", provenance_dir)
    assert provenance is not None
    assert provenance.authoritative_reconciliation_date == "2026-10-02"
    assert provenance.source == "ths_ifind"
    assert "snapshot_id=ths-ifind-886015-2026-10-02-audit001" in provenance.notes
    assert check_freshness(
        "创新药", current_date="2026-10-05", provenance_dir=provenance_dir
    ) == FreshnessLevel.FRESH
    assert check_freshness(
        "创新药", current_date="2026-11-15", provenance_dir=provenance_dir
    ) == FreshnessLevel.STALE


def test_mismatch_evidence_is_persisted_and_reported(tmp_path):
    sectors = load_sectors_from_js(SECTOR_FILE)
    snapshot = load_authoritative_snapshot(FIXTURE)
    local_codes = sectors["创新药"]
    dropped = local_codes[0]
    added = "688981"
    snapshot["sectors"]["创新药"]["codes"] = local_codes[1:] + [added]
    snapshot["snapshot_id"] = "mismatch-regression"
    snapshot["snapshot_sha256"] = "test-hash"

    reports, audit = reconcile_snapshot_to_reports(
        snapshot,
        sectors,
        sector_filter="创新药",
        sector_codes={"创新药": "886015"},
        provenance_dir=str(tmp_path / "provenance"),
        evidence_dir=str(tmp_path / "evidence"),
    )
    report = reports["创新药"]
    status_by_code = {c.code: c.status for c in report.constituents}
    assert status_by_code[dropped] == MembershipStatus.NOT_MEMBER
    assert audit["创新药"]["source_only_candidates"] == [
        {"code": added, "source": "ths_ifind:mismatch-regression"}
    ]

    with open(audit["创新药"]["evidence_path"], "r", encoding="utf-8") as f:
        evidence = json.load(f)
    assert evidence["only_in_local"] == [dropped]
    assert evidence["only_in_source"] == [added]
    assert evidence["matched"]


def test_snapshot_without_source_or_as_of_date_fails_closed(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"创新药": ["600276"]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AuthoritativeSnapshotError):
        load_authoritative_snapshot(str(path))


def test_snapshot_future_as_of_date_fails_closed(tmp_path):
    path = tmp_path / "future.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source": "ths_ifind",
                "as_of_date": "2099-01-01",
                "sectors": {"创新药": {"codes": ["600276"]}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(AuthoritativeSnapshotError):
        load_authoritative_snapshot(str(path))
