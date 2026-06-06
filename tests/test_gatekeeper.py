"""Tests for Risk Gatekeeper hard rules."""

from __future__ import annotations

import unittest

from src.risk.gatekeeper import GatekeeperVerdict, RiskGatekeeper


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

    def test_rejects_expiry_day_iron_condor(self) -> None:
        payload = dict(self.base_payload, dte=1)
        decision = self.gatekeeper.evaluate(
            strategy="iron_condor",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(decision.rule_id, "gamma_dte_filter")

    def test_rejects_high_vix_short_vol(self) -> None:
        payload = dict(self.base_payload, vix=19.0)
        decision = self.gatekeeper.evaluate(
            strategy="short_strangle",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(decision.rule_id, "vix_ceiling")

    def test_rejects_daily_circuit_breaker(self) -> None:
        decision = self.gatekeeper.evaluate(
            strategy="bull_call_spread",
            feature_payload=self.base_payload,
            daily_realized_pnl=-8000.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(decision.rule_id, "daily_circuit_breaker")

    def test_allows_directional_on_expiry_day(self) -> None:
        payload = dict(self.base_payload, dte=1)
        decision = self.gatekeeper.evaluate(
            strategy="bull_call_spread",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.APPROVE)


if __name__ == "__main__":
    unittest.main()
