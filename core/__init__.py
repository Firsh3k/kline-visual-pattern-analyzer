"""Canonical algorithm core for Kline Visual Pattern Analyzer V5+.

PAIOS PILOT-001 introduces this package as the migration target for shared,
testable algorithm primitives. Production pattern scoring is not migrated in
this pilot slice.
"""

from .pivots import detect_swing_pivots

__all__ = ["detect_swing_pivots"]
