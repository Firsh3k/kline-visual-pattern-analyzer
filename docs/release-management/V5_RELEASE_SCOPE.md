# V5.0 Release Scope & Gate

> Product-scope authority is maintained in Linear; this file is the version-controlled GitHub mirror used for engineering/release execution.

Linear source document: `V5.0 Release Scope & Gate`

## Release Objective

V5.0 is intended to establish a trustworthy, testable and rollback-ready A-share technical-analysis foundation: canonical core semantics, canonical market-data contract, structural pivots, classic pattern detection, empirical calibration and authoritative sector scope.

Priority principle:

**Correctness > Decision Quality > Workflow Efficiency > UX > Feature Quantity**

Target final version: `v5.0.0`.

## V5 Must-have — Release Blockers

| Issue | Scope | Status |
|---|---|---|
| FIR-14 | Canonical Shared Core | Done |
| FIR-23 | Canonical Market Data Contract | Done |
| FIR-12 | Swing Pivot Engine + W/M/H&S Pattern Migration | Done |
| FIR-24 | H&S geometry guardrails | Backlog |
| FIR-13 | Empirical A-share Calibration | Backlog |
| FIR-15 | Authoritative Sector Membership Validation | Backlog |

A Must-have cannot be deferred to V6 without explicit Product Owner approval plus a documented reason, risk and alternative.

## V5 Candidate

| Issue | Scope | Decision |
|---|---|---|
| FIR-22 | Dynamic THS sector-index scan scope | Candidate — decide at Scope Freeze |

A Candidate may defer to V6 if it does not compromise correctness, data integrity or core decision quality.

## Post-V5 / Productization

Current non-blockers for `v5.0.0`:

- FIR-17 — Decision Explainability Layer
- FIR-18 — Signal Quality Feedback Loop
- FIR-16 — Authentication Architecture

These remain V5.1 Productization candidates or may be moved to V6 after Product Review.

## Scope Governance

Every new backlog item must be classified as one of:

1. `V5 Must-have`
2. `V5 Candidate`
3. `V6 Deferred`

No unclassified backlog item automatically becomes a V5 release blocker.

### Scope Freeze

After V5 Scope Freeze, new features do not enter V5 unless they are:

- P0 Correctness;
- a release-blocking regression; or
- a security/data-integrity blocker.

Other new work defaults to V6.

## Release Sequence

1. Complete all V5 Must-have issues.
2. Decide FIR-22: ship in V5 or defer to V6.
3. Run V5 Integration QA.
4. Create V5 Release Candidate (`v5.0.0-rc.1`).
5. Run V5 Release Gate.
6. Freeze exact production SHA.
7. Create tag `v5.0.0`.
8. Create GitHub Release `V5.0 — Stable Production Release`.
9. Run production validation.
10. Keep `v4.0.0` as emergency rollback baseline until V5 stability is confirmed.

## Release Gate

V5.0 cannot release unless all of the following are true:

- all V5 Must-have issues = Done;
- no open P0 correctness blocker;
- no unresolved look-ahead bias in pattern/calibration path;
- Canonical Market Data Contract enforced end-to-end;
- cross-runtime parity green;
- V4 regression suite green;
- V5 integration suite green;
- empirical calibration evidence documented with sample sizes and denominators;
- sector membership source/provenance verified;
- RC smoke validation completed on representative A-share symbols;
- rollback procedure verified against `v4.0.0`.

## Current Snapshot — 2026-10-04

Completed foundation:

- FIR-14 ✅
- FIR-23 ✅
- FIR-12 ✅

Remaining V5 Must-have:

- FIR-24
- FIR-13
- FIR-15

Candidate:

- FIR-22

Current formal V5.0 milestone progress: **50% (3/6 Must-have complete)**.

## Authority

Product Owner / final release authority: Hao Shen.

Independent AI Review Gate performs Code Review / QA before merge. PASS PRs may be merged directly under standing Product Owner authorization. Any Must-have downgrade still requires explicit Product Owner approval.
