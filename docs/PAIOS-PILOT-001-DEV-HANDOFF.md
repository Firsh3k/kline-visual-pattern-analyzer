# PAIOS PILOT-001 — Developer Handoff

## Task
Canonical swing-pivot detector foundation for KLINE V5.0.

## Baseline
- Source branch: `main`
- Baseline commit: `248ac0e0a042b89b1405986e7e7f521b2d25d730`
- Dev branch: `paios/pilot-001-v5-swing-pivots`

## Changed files
- `core/__init__.py`
- `core/pivots.py`
- `tests/test_pivots.py`
- `.github/workflows/ci.yml`
- `VERSION`
- `docs/V5.0_RELEASE.md`
- `docs/PAIOS-PILOT-001-DEV-HANDOFF.md`

## Implementation
`detect_swing_pivots()` provides one canonical Python primitive for confirmed swing highs/lows with configurable left/right confirmation windows.

Tie policy is conservative: strict comparisons reject equal-price plateaus. A candle qualifying as both high and low is rejected as ambiguous.

## Tests added
`tests/test_pivots.py` covers:
1. known ordered swing high + swing low;
2. plateau/tie rejection;
3. boundary exclusion;
4. monotonic up/down false-positive prevention;
5. `date` fallback schema;
6. ambiguous wide-range candle rejection;
7. insufficient-data behavior;
8. invalid window validation;
9. missing/non-numeric field failures.

All tests are deterministic and offline.

## Existing behavior
No existing W/M/H&S production function imports `core.pivots` in PILOT-001. Existing recommendation scores and trading outputs are intentionally unchanged.

## Assumptions
- Confirmed pivots require future/right-side candles; later live integration must account for confirmation lag.
- Strict tie rejection is preferred to arbitrary plateau selection for V5 foundation.
- `day`/`date` compatibility is required because current project paths use both schemas.

## Known limitations / deferred scope
- No production pattern migration in V5.0 PILOT-001.
- No JS pivot implementation yet; cross-runtime parity becomes required when the production Web path migrates.
- No minimum percentage prominence/ATR filter yet.
- No pivot compression/de-duplication beyond ambiguity rejection.
- BUG-010 remains partially open until W/M/H&S production paths consume ordered pivots and false-positive behavior is measured.

## Rollback
Additive rollback: revert the V5 foundation commits. Existing production algorithms were not replaced.

## DEV status
READY FOR INDEPENDENT QA.
