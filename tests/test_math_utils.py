"""Tests for feature math helpers."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from src.features.math_utils import (
    compute_ad_ratio,
    compute_atr,
    compute_dte_from_expiry_timestamp,
    compute_expiry_weighted_pcr_momentum,
    compute_vix_atr_divergence,
)


class MathUtilsTests(unittest.TestCase):
    def test_compute_atr(self) -> None:
        bars = [
            {"timestamp": 1, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1},
            {"timestamp": 2, "open": 101, "high": 104, "low": 100, "close": 103, "volume": 1},
            {"timestamp": 3, "open": 103, "high": 105, "low": 101, "close": 102, "volume": 1},
        ]
        atr = compute_atr(bars, period=2)
        self.assertGreater(atr, 0)

    def test_pcr_momentum(self) -> None:
        value = compute_expiry_weighted_pcr_momentum(current_pcr=1.1, prior_pcr=1.0, dte=5)
        self.assertAlmostEqual(value, 0.05)

    def test_pcr_momentum_none_without_history(self) -> None:
        value = compute_expiry_weighted_pcr_momentum(current_pcr=1.1, prior_pcr=None, dte=5)
        self.assertIsNone(value)

    def test_pcr_momentum_zero_when_unchanged(self) -> None:
        value = compute_expiry_weighted_pcr_momentum(current_pcr=1.0, prior_pcr=1.0, dte=5)
        self.assertEqual(value, 0.0)

    def test_vix_atr_divergence(self) -> None:
        bars = [
            {"timestamp": 1, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            {"timestamp": 2, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1},
        ]
        divergence = compute_vix_atr_divergence(
            current_vix=16.0,
            previous_vix=15.0,
            nifty_bars=bars,
        )
        self.assertIsInstance(divergence, float)

    def test_dte_from_expiry_timestamp(self) -> None:
        expiry = int(datetime(2026, 6, 12, tzinfo=timezone.utc).timestamp())
        now = datetime(2026, 6, 6, tzinfo=timezone.utc)
        self.assertEqual(compute_dte_from_expiry_timestamp(expiry, now=now), 6)

    def test_ad_ratio(self) -> None:
        self.assertEqual(compute_ad_ratio(20, 10), 2.0)
        self.assertEqual(compute_ad_ratio(5, 0), 5.0)


if __name__ == "__main__":
    unittest.main()
