# End-to-End Product × Engineering × Release Operating Model

## Visual overview

![Kline Product × Engineering × Release Management Flow](product-engineering-release-flow.svg)

The SVG above is the durable GitHub-native version of the interactive Visualize flow created during governance design. It is intended for quick orientation; the Mermaid diagrams below remain the editable structural source.

## System ownership

- **Linear** — Product scope, backlog, priorities, acceptance criteria, milestones and V5/V6 classification.
- **GitHub** — Branches, implementation, PR review, CI, merge history, release candidates, tags and releases.
- **Product Owner** — Final scope, deferral and release authority.
- **Independent AI Review Gate** — Code Review / QA / merge gate.

## End-to-end flow

```mermaid
flowchart TD
    A[Idea / Bug / Feature] --> B[Linear Backlog]
    B --> C[Product Review]
    C --> D[Define WHAT / WHY / Acceptance Criteria]
    D --> E{Release Scope}
    E -->|Must-have| F[V5 Must-have]
    E -->|Candidate| G[V5 Candidate]
    E -->|Defer| H[V6 Deferred]

    F --> I[Ready for Dev]
    G --> I
    I --> J[Dedicated Git Branch]
    J --> K[Implementation]
    K --> L[READY FOR CODE REVIEW]
    L --> M[Independent Code Review + QA]
    M --> N{Gate Result}
    N -->|PARTIAL / FAIL| O[Fix Blockers]
    O --> L
    N -->|PASS| P[Merge to main]
    P --> Q[Post-merge main CI / Smoke]
    Q --> R{Main Green?}
    R -->|No| S[Reopen / Hotfix / Rollback]
    R -->|Yes| T[Linear Done]

    T --> U{All V5 Must-have Done?}
    U -->|No| B
    U -->|Yes| V[Scope Freeze]
    V --> W[Decide V5 Candidate: Ship / V6]
    W --> X[V5 Integration QA]
    X --> Y[v5.0.0-rc.1 Prerelease]
    Y --> Z[RC Smoke / Representative A-share Validation]
    Z --> AA{Release Gate}
    AA -->|Fail| AB[Fix Release Blockers]
    AB --> AC[v5.0.0-rc.N]
    AC --> Z
    AA -->|PASS| AD[Freeze Exact Production SHA]
    AD --> AE[Git Tag v5.0.0]
    AE --> AF[GitHub Final Release]
    AF --> AG[Production Validation]
    AG --> AH{Stable?}
    AH -->|Yes| AI[New Production Baseline]
    AH -->|Minor Fix| AJ[v5.0.1 Hotfix]
    AH -->|Severe P0/P1| AK[Rollback v4.0.0]
    AI --> AL[Feedback / Evidence]
    AJ --> AL
    AK --> AL
    AL --> B
```

## Decision boundaries

### Product Gate
Work cannot become `Ready for Dev` until the problem, value, acceptance criteria, dependencies, priority and release scope are sufficiently defined.

### Code / QA Gate
Developer completion is not certification. Merge requires independent review evidence and PASS.

### Main Smoke Gate
A green PR is not enough. The exact merge commit on `main` must remain green.

### Release Gate
`v5.0.0` is allowed only when scope, correctness, decision quality, integration QA, release-candidate validation and rollback readiness are all proven.

## Current V5 path

```mermaid
flowchart LR
    A[FIR-14 Canonical Core ✅] --> B[FIR-23 Market Data Contract ✅]
    B --> C[FIR-12 Pivot + Pattern Migration ✅]
    C --> D[FIR-24 H&S Guardrails]
    D --> E[FIR-13 Empirical Calibration]
    E --> F[FIR-15 Sector Validation]
    F --> G{FIR-22 Candidate}
    G -->|Ship| H[Include in V5]
    G -->|Defer| I[V6 Deferred]
    H --> J[Integration QA]
    I --> J
    J --> K[RC]
    K --> L[Release Gate]
    L --> M[v5.0.0]
```

## Governance principle

Every important product decision should be traceable from:

`Linear Issue → PR → Review Evidence → Merge SHA → CI → Release Candidate → Final Tag / Release → Production Validation / Rollback`.

This traceability is the project's audit chain and is more important than feature count.
