# Kline Visual Pattern Analyzer — Project Documentation

This directory is the version-controlled project knowledge base for product governance, engineering governance, architecture, QA evidence, and release management.

## Source-of-truth model

- **Linear** — product backlog, priority, acceptance criteria, milestone and release-scope classification.
- **GitHub Issues / PRs / CI** — engineering execution, review evidence, merge history and regression proof.
- **Git tags / GitHub Releases** — immutable product-version baselines and rollback targets.
- **`docs/`** — durable decisions, operating model, release scope, architecture and QA documentation.

## Documentation map

### Product management
- [`product-management/PRODUCT_MANAGEMENT.md`](product-management/PRODUCT_MANAGEMENT.md)

### Engineering management
- [`engineering-management/ENGINEERING_MANAGEMENT.md`](engineering-management/ENGINEERING_MANAGEMENT.md)

### Release management
- [`release-management/RELEASE_MANAGEMENT.md`](release-management/RELEASE_MANAGEMENT.md)
- [`release-management/V5_RELEASE_SCOPE.md`](release-management/V5_RELEASE_SCOPE.md)

### End-to-end governance
- [`governance/OPERATING_MODEL.md`](governance/OPERATING_MODEL.md)

### Architecture
- [`architecture/README.md`](architecture/README.md)

### QA & validation
- [`qa-validation/README.md`](qa-validation/README.md)

### Existing evidence / historical documents
- [`V5.0_RELEASE.md`](V5.0_RELEASE.md)
- [`v5_pattern_migration_validation.md`](v5_pattern_migration_validation.md)
- [`PAIOS-PILOT-001-DEV-HANDOFF.md`](PAIOS-PILOT-001-DEV-HANDOFF.md)

## Documentation rules

1. Product decisions live in Linear first and are mirrored here when they affect durable project governance.
2. Code-level truth is never replaced by prose; PRs, commits, CI and tags remain the evidence.
3. Release scope changes after Scope Freeze require an explicit Product Owner decision.
4. P0 Correctness issues are release blockers unless a documented safe alternative is approved.
5. Release documents must identify the exact production SHA and rollback target.
