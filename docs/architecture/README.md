# Architecture Documentation

This directory stores durable architecture decisions and canonical contracts that must evolve with the codebase.

## What belongs here

- Canonical Core architecture and runtime ownership
- Market Data Contract architecture
- Cross-runtime parity model
- Pattern / pivot domain model
- Storage / sync architecture
- Future authentication / deployment architecture
- Architecture Decision Records (ADRs)

## Rule

Architecture prose does not override executable contracts. When documentation and code disagree, treat the mismatch as a defect and reconcile it through Product/Architecture Review.

## Recommended ADR naming

`ADR-XXXX-short-decision-name.md`

Each ADR should capture:

1. Context / problem
2. Decision
3. Alternatives considered
4. Consequences / risks
5. Validation evidence
6. Related Linear issues / PRs
