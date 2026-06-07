"""Tests for risk config loader and absolute limits clamping."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.config.absolute_limits import ABSOLUTE_LIMITS, clamp_to_absolute
from src.config.risk_config import RiskConfig, load_risk_config
from src.core.context import STALE_QUOTE_POINTS, StrategyName
from src.core.strategy_registry import DEFAULT_LEG_COUNT, LEG_COUNTS


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

    def test_load_wing_width_from_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "risk_config.json"
            path.write_text(json.dumps({"wing_width_points": 300}), encoding="utf-8")
            config = load_risk_config(path)
        self.assertEqual(config.wing_width_points, 300)

    def test_load_missing_file_uses_defaults(self) -> None:
        config = load_risk_config(Path("/nonexistent/risk_config.json"))
        self.assertEqual(config.vix_choppy_threshold, 18.0)

    def test_clamp_to_absolute_lower_bound(self) -> None:
        self.assertEqual(clamp_to_absolute("stale_quote_points", 0.5), 1.0)

    def test_clamp_to_absolute_upper_bound(self) -> None:
        self.assertEqual(clamp_to_absolute("stale_quote_points", 100.0), 50.0)

    def test_clamp_to_absolute_no_change(self) -> None:
        self.assertEqual(clamp_to_absolute("max_spread_pct", 0.05), 0.05)

    def test_absolute_limits_has_bounds_for_numeric_keys(self) -> None:
        enum_only = {"greeks_price_side"}
        for key in RiskConfig.model_fields:
            if key in enum_only:
                continue
            bounds = getattr(ABSOLUTE_LIMITS, key)
            self.assertEqual(len(bounds), 3)

    def test_greeks_config_defaults(self) -> None:
        config = RiskConfig()
        self.assertEqual(config.risk_free_rate, 0.065)
        self.assertEqual(config.dividend_yield, 0.0)
        self.assertEqual(config.greeks_price_side, "mid")
        self.assertEqual(config.iv_solver_max_iter, 50)
        self.assertEqual(config.iv_tolerance, 1e-5)

    def test_load_greeks_fields_from_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "risk_config.json"
            path.write_text(
                json.dumps(
                    {
                        "risk_free_rate": 0.07,
                        "dividend_yield": 0.01,
                        "greeks_price_side": "ask",
                        "iv_solver_max_iter": 40,
                        "iv_tolerance": 1e-4,
                    }
                ),
                encoding="utf-8",
            )
            config = load_risk_config(path)
        self.assertEqual(config.risk_free_rate, 0.07)
        self.assertEqual(config.dividend_yield, 0.01)
        self.assertEqual(config.greeks_price_side, "ask")
        self.assertEqual(config.iv_solver_max_iter, 40)
        self.assertEqual(config.iv_tolerance, 1e-4)

    def test_clamp_greeks_numeric_fields(self) -> None:
        self.assertEqual(clamp_to_absolute("risk_free_rate", 0.50), 0.20)
        self.assertEqual(clamp_to_absolute("dividend_yield", 0.15), 0.10)
        self.assertEqual(clamp_to_absolute("iv_solver_max_iter", 500), 200)
        self.assertEqual(clamp_to_absolute("iv_tolerance", 1e-12), 1e-9)

    def test_strike_selection_defaults(self) -> None:
        config = RiskConfig()
        self.assertEqual(config.delta_target_short_put, -0.30)
        self.assertEqual(config.delta_target_short_call, 0.30)
        self.assertEqual(config.delta_tolerance, 0.10)
        self.assertEqual(config.min_dte_for_entry, 1)
        self.assertEqual(config.max_dte_for_entry, 7)
        self.assertEqual(config.wing_width_points, 200)

    def test_wing_width_clamped_to_absolute_bounds(self) -> None:
        self.assertEqual(clamp_to_absolute("wing_width_points", 50), 100)
        self.assertEqual(clamp_to_absolute("wing_width_points", 600), 500)
        self.assertEqual(clamp_to_absolute("wing_width_points", 300), 300)

    def test_clamp_delta_targets(self) -> None:
        self.assertEqual(clamp_to_absolute("delta_target_short_put", -0.90), -0.60)
        self.assertEqual(clamp_to_absolute("delta_target_short_call", 0.90), 0.60)
        self.assertEqual(clamp_to_absolute("delta_tolerance", 0.50), 0.30)
        self.assertEqual(clamp_to_absolute("max_dte_for_entry", 30), 21)

    def test_strategy_registry_contains_only_iron_condor_multi_leg(self) -> None:
        self.assertEqual(LEG_COUNTS, {StrategyName.IRON_CONDOR: 4})
        self.assertEqual(DEFAULT_LEG_COUNT, 1)


if __name__ == "__main__":
    unittest.main()
