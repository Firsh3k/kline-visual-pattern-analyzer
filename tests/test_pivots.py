#!/usr/bin/env python3
"""Deterministic offline tests for the V5 canonical swing-pivot detector."""

import unittest

from core.pivots import detect_swing_pivots


def candle(high, low, day=None):
    item = {"high": high, "low": low}
    if day is not None:
        item["day"] = day
    return item


class SwingPivotTests(unittest.TestCase):
    def test_detects_ordered_high_and_low(self):
        data = [
            candle(10, 8, "d0"),
            candle(11, 9, "d1"),
            candle(15, 10, "d2"),  # swing high
            candle(12, 7, "d3"),
            candle(11, 5, "d4"),   # swing low
            candle(13, 8, "d5"),
            candle(14, 9, "d6"),
        ]
        pivots = detect_swing_pivots(data, left=2, right=2)
        self.assertEqual(
            pivots,
            [
                {"index": 2, "type": "swing_high", "price": 15.0, "date": "d2"},
                {"index": 4, "type": "swing_low", "price": 5.0, "date": "d4"},
            ],
        )

    def test_plateau_ties_are_rejected(self):
        data = [
            candle(10, 5),
            candle(12, 6),
            candle(15, 7),
            candle(15, 8),
            candle(12, 6),
            candle(10, 5),
        ]
        pivots = detect_swing_pivots(data, left=1, right=1)
        self.assertFalse(any(p["type"] == "swing_high" for p in pivots))

    def test_boundaries_without_confirmation_are_excluded(self):
        data = [
            candle(20, 1, "first"),
            candle(10, 5, "middle1"),
            candle(11, 4, "middle2"),
            candle(12, 3, "middle3"),
            candle(25, 0, "last"),
        ]
        pivots = detect_swing_pivots(data, left=1, right=1)
        self.assertTrue(all(p["index"] not in (0, len(data) - 1) for p in pivots))

    def test_monotonic_series_does_not_fabricate_structure(self):
        up = [candle(10 + i, 5 + i, f"u{i}") for i in range(12)]
        down = [candle(30 - i, 20 - i, f"d{i}") for i in range(12)]
        self.assertEqual(detect_swing_pivots(up, left=2, right=2), [])
        self.assertEqual(detect_swing_pivots(down, left=2, right=2), [])

    def test_date_falls_back_to_date_field(self):
        data = [
            {"high": 10, "low": 7, "date": "2026-01-01"},
            {"high": 15, "low": 8, "date": "2026-01-02"},
            {"high": 11, "low": 7, "date": "2026-01-03"},
        ]
        pivots = detect_swing_pivots(data, left=1, right=1)
        self.assertEqual(pivots[0]["date"], "2026-01-02")

    def test_ambiguous_wide_range_candle_is_rejected(self):
        data = [candle(10, 5), candle(20, 1), candle(11, 4)]
        self.assertEqual(detect_swing_pivots(data, left=1, right=1), [])

    def test_short_input_returns_empty(self):
        self.assertEqual(
            detect_swing_pivots([candle(1, 0), candle(2, 1)], left=1, right=1),
            [],
        )

    def test_invalid_window_rejected(self):
        data = [candle(1, 0), candle(2, 1), candle(1, 0)]
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    detect_swing_pivots(data, left=value, right=1)

    def test_missing_or_non_numeric_fields_fail_clearly(self):
        with self.assertRaisesRegex(ValueError, "missing required field: low"):
            detect_swing_pivots([{"high": 1}, {"high": 2}, {"high": 1}], left=1, right=1)
        with self.assertRaisesRegex(ValueError, "non-numeric high"):
            detect_swing_pivots(
                [candle(1, 0), {"high": "bad", "low": 0}, candle(1, 0)],
                left=1,
                right=1,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
