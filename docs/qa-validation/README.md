# QA & Validation Documentation

This directory stores durable validation methodology and release-quality evidence that is too important to leave only in PR comments.

## What belongs here

- Before/after validation methodology
- Representative A-share sample audits
- False-positive / recall analysis
- Look-ahead and point-in-time validation
- Cross-runtime parity evidence
- Regression-suite definitions
- Release Candidate smoke plans
- Release Gate evidence summaries

## Evidence standard

Prefer reproducible evidence over narrative claims:

- exact commit SHA;
- deterministic fixtures;
- CI workflow/run identifiers;
- sample sizes and denominators;
- explicit N/A when evidence is insufficient;
- known limitations and residual risks.

## Existing validation document

The current FIR-12 migration validation remains at:

[`../v5_pattern_migration_validation.md`](../v5_pattern_migration_validation.md)

It can be moved into this directory later through a dedicated documentation-cleanup PR if we want to preserve clean history and update inbound links safely.
