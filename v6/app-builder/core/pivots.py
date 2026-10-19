"""Deterministic swing-pivot detection primitives for V5.

This module is intentionally independent from the current production pattern
scoring paths. PILOT-001 establishes a canonical, testable primitive that later
V5 increments can use to migrate W/M/H&S recognition from broad-window extrema
to explicit ordered pivots.

Detection rule
--------------
For a candle at index ``i`` with ``left`` and ``right`` confirmation windows:

* swing high: its high is strictly greater than every high in both neighboring
  windows;
* swing low: its low is strictly lower than every low in both neighboring
  windows.

Strict comparison means equal-price plateaus are rejected rather than choosing
an arbitrary candle. Boundary candles without a complete confirmation window
are excluded. If one unusually wide candle qualifies simultaneously as both a
swing high and a swing low, it is conservatively rejected as ambiguous.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence


Pivot = Dict[str, Any]


def _as_float(candle: Mapping[str, Any], field: str, index: int) -> float:
    if field not in candle:
        raise ValueError(f"candle at index {index} missing required field: {field}")
    try:
        return float(candle[field])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"candle at index {index} has non-numeric {field}: {candle[field]!r}"
        ) from exc


def _date_value(candle: Mapping[str, Any]) -> Any:
    """Return the source date without inventing one.

    The project currently uses both ``day`` and ``date`` across execution paths,
    so the canonical pivot schema accepts either and returns ``None`` if neither
    exists.
    """

    if "day" in candle:
        return candle["day"]
    return candle.get("date")


def detect_swing_pivots(
    data: Sequence[Mapping[str, Any]],
    *,
    left: int = 2,
    right: int = 2,
) -> List[Pivot]:
    """Detect confirmed swing highs and lows in chronological order.

    Parameters
    ----------
    data:
        Sequence of OHLC-like mappings. ``high`` and ``low`` are required.
    left / right:
        Number of completed neighboring candles required on each side. Both
        values must be positive integers.

    Returns
    -------
    list[dict]
        Stable dictionaries with keys ``index``, ``type``, ``price`` and
        ``date``. ``type`` is ``swing_high`` or ``swing_low``.

    Notes
    -----
    This function deliberately performs *confirmed* pivot detection. The final
    ``right`` candles can therefore never be pivots at the time of evaluation,
    which prevents look-ahead ambiguity in later live/rolling integrations.
    """

    if isinstance(left, bool) or not isinstance(left, int) or left < 1:
        raise ValueError("left must be a positive integer")
    if isinstance(right, bool) or not isinstance(right, int) or right < 1:
        raise ValueError("right must be a positive integer")
    if data is None:
        raise ValueError("data must be a sequence, not None")

    n = len(data)
    if n < left + right + 1:
        return []

    highs = [_as_float(candle, "high", i) for i, candle in enumerate(data)]
    lows = [_as_float(candle, "low", i) for i, candle in enumerate(data)]

    pivots: List[Pivot] = []

    for i in range(left, n - right):
        high = highs[i]
        low = lows[i]

        left_highs = highs[i - left : i]
        right_highs = highs[i + 1 : i + right + 1]
        left_lows = lows[i - left : i]
        right_lows = lows[i + 1 : i + right + 1]

        is_swing_high = high > max(left_highs) and high > max(right_highs)
        is_swing_low = low < min(left_lows) and low < min(right_lows)

        # A single wide-range candle can mathematically satisfy both rules.
        # It is ambiguous for ordered-pattern construction, so reject it.
        if is_swing_high and is_swing_low:
            continue

        if is_swing_high:
            pivots.append(
                {
                    "index": i,
                    "type": "swing_high",
                    "price": high,
                    "date": _date_value(data[i]),
                }
            )
        elif is_swing_low:
            pivots.append(
                {
                    "index": i,
                    "type": "swing_low",
                    "price": low,
                    "date": _date_value(data[i]),
                }
            )

    return pivots

