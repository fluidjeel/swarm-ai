"""Tests for risk config loader and absolute limits clamping."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.config.absolute_limits import ABSOLUTE_LIMITS, clamp_to_absolute
from src.config.risk_config import RiskConfig, load_risk_config
from src.core.context import STALE_QUOTE_POINTS


class RiskConfigTests(unittest.TestCase):
    def test_defaults_match_stale_quote_constant(self) -> None:
        config = RiskConfig()
        self.assertEqual(config.stale_quote_points, STALE_QUOTE_POINTS)

    def test_load_from_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "risk_config.json"
            path.write_text(
                json.dumps({"stale_quote_points": 12.0, "max_spread_pct": 0.04}),
                encoding="utf-8",
            )
            config = load_risk_config(path)
        self.assertEqual(config.stale_quote_points, 12.0)
        self.assertEqual(config.max_spread_pct, 0.04)

    def test_load_missing_file_uses_defaults(self) -> None:
        config = load_risk_config(Path("/nonexistent/risk_config.json"))
        self.assertEqual(config.vix_choppy_threshold, 18.0)

    def test_clamp_to_absolute_lower_bound(self) -> None:
        self.assertEqual(clamp_to_absolute("stale_quote_points", 0.5), 1.0)

    def test_clamp_to_absolute_upper_bound(self) -> None:
        self.assertEqual(clamp_to_absolute("stale_quote_points", 100.0), 50.0)

    def test_clamp_to_absolute_no_change(self) -> None:
        self.assertEqual(clamp_to_absolute("max_spread_pct", 0.05), 0.05)

    def test_absolute_limits_has_bounds_for_all_keys(self) -> None:
        for key in RiskConfig.model_fields:
            bounds = getattr(ABSOLUTE_LIMITS, key)
            self.assertEqual(len(bounds), 3)


if __name__ == "__main__":
    unittest.main()
