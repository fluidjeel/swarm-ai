"""Tests for Risk Gatekeeper hard rules."""

from __future__ import annotations

import unittest

from src.risk.gatekeeper import (
    GatekeeperRule,
    GatekeeperVerdict,
    RiskGatekeeper,
    compute_allowed_lots,
    round_trip_slippage,
)


class RiskGatekeeperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gatekeeper = RiskGatekeeper()
        self.base_payload = {
            "vix": 15.0,
            "dte": 7,
            "NIFTY_500_AD_Ratio": 1.1,
        }

    def test_approves_valid_iron_condor(self) -> None:
        decision = self.gatekeeper.evaluate(
            strategy="iron_condor",
            feature_payload=self.base_payload,
            daily_realized_pnl=-1000.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.APPROVE)
        self.assertEqual(decision.allowed_lots, 1)
        self.assertEqual(decision.expected_round_trip_cost, 40.0)

    def test_rejects_expiry_day_iron_condor(self) -> None:
        payload = dict(self.base_payload, dte=1)
        decision = self.gatekeeper.evaluate(
            strategy="iron_condor",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(decision.rule_id, GatekeeperRule.GAMMA_DTE_FILTER)

    def test_rejects_high_vix_short_vol(self) -> None:
        payload = dict(self.base_payload, vix=19.0)
        decision = self.gatekeeper.evaluate(
            strategy="short_strangle",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(decision.rule_id, GatekeeperRule.VIX_CEILING)

    def test_rejects_daily_circuit_breaker(self) -> None:
        decision = self.gatekeeper.evaluate(
            strategy="bull_call_spread",
            feature_payload=self.base_payload,
            daily_realized_pnl=-8000.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(decision.rule_id, GatekeeperRule.DAILY_CIRCUIT_BREAKER)

    def test_allows_directional_on_expiry_day(self) -> None:
        payload = dict(self.base_payload, dte=1)
        decision = self.gatekeeper.evaluate(
            strategy="bull_call_spread",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.APPROVE)

    def test_approves_at_dte_boundary(self) -> None:
        payload = dict(self.base_payload, dte=2)
        decision = self.gatekeeper.evaluate(
            strategy="short_straddle",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.APPROVE)

    def test_rejects_dte_zero(self) -> None:
        payload = dict(self.base_payload, dte=0)
        decision = self.gatekeeper.evaluate(
            strategy="iron_condor",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(decision.rule_id, GatekeeperRule.GAMMA_DTE_FILTER)

    def test_vix_at_exact_ceiling_passes(self) -> None:
        payload = dict(self.base_payload, vix=18.0)
        decision = self.gatekeeper.evaluate(
            strategy="short_strangle",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.APPROVE)

    def test_vix_just_above_ceiling_rejects(self) -> None:
        payload = dict(self.base_payload, vix=18.01)
        decision = self.gatekeeper.evaluate(
            strategy="short_strangle",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(decision.rule_id, GatekeeperRule.VIX_CEILING)

    def test_daily_pnl_above_breaker_passes(self) -> None:
        decision = self.gatekeeper.evaluate(
            strategy="bull_call_spread",
            feature_payload=self.base_payload,
            daily_realized_pnl=-7999.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.APPROVE)

    def test_daily_pnl_below_breaker_rejects(self) -> None:
        decision = self.gatekeeper.evaluate(
            strategy="bull_call_spread",
            feature_payload=self.base_payload,
            daily_realized_pnl=-8001.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(decision.rule_id, GatekeeperRule.DAILY_CIRCUIT_BREAKER)

    def test_bull_call_spread_at_high_vix_approves(self) -> None:
        payload = dict(self.base_payload, vix=22.0)
        decision = self.gatekeeper.evaluate(
            strategy="bull_call_spread",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.APPROVE)

    def test_allowed_lots_at_base_capital(self) -> None:
        self.assertEqual(compute_allowed_lots(600_000), 1)
        self.assertEqual(compute_allowed_lots(599_999), 1)

    def test_allowed_lots_scales_with_capital(self) -> None:
        self.assertEqual(compute_allowed_lots(1_000_000), 2)
        self.assertEqual(compute_allowed_lots(1_400_000), 3)

    def test_rejects_requested_lots_above_cap(self) -> None:
        decision = self.gatekeeper.evaluate(
            strategy="iron_condor",
            feature_payload=self.base_payload,
            daily_realized_pnl=0.0,
            current_capital=700_000.0,
            requested_lots=2,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(decision.rule_id, GatekeeperRule.LOT_SCALING)
        self.assertEqual(decision.allowed_lots, 1)

    def test_approves_requested_lots_within_cap(self) -> None:
        decision = self.gatekeeper.evaluate(
            strategy="iron_condor",
            feature_payload=self.base_payload,
            daily_realized_pnl=0.0,
            current_capital=1_000_000.0,
            requested_lots=2,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.APPROVE)
        self.assertEqual(decision.allowed_lots, 2)

    def test_futures_slippage_cost(self) -> None:
        self.assertEqual(round_trip_slippage("nifty_futures_long"), 150.0)
        decision = self.gatekeeper.evaluate(
            strategy="nifty_futures_long",
            feature_payload=self.base_payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.expected_round_trip_cost, 150.0)

    def test_options_slippage_cost(self) -> None:
        self.assertEqual(round_trip_slippage("bull_call_spread"), 40.0)


if __name__ == "__main__":
    unittest.main()
