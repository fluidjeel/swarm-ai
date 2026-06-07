"""Tests for core AgentContext models and StrategyName enum fence."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from src.core.context import OpenPosition, StrategyDecision, StrategyName


class StrategyNameEnumTests(unittest.TestCase):
    def test_strategy_name_enum_is_frozen(self) -> None:
        with self.assertRaises(ValueError):
            StrategyName("short_strangle")

        members = {member.value for member in StrategyName}
        self.assertEqual(
            members,
            {
                "iron_condor",
                "bull_call_spread",
                "bear_put_spread",
                "cash_no_trade",
            },
        )

    def test_strategy_decision_validates_strategy_is_in_enum(self) -> None:
        decision = StrategyDecision(
            strategy=StrategyName.IRON_CONDOR,
            supporting_signals=["ad_ratio=1.10", "vix=15.00"],
        )
        self.assertEqual(decision.strategy, StrategyName.IRON_CONDOR)

        coerced = StrategyDecision(
            strategy="bull_call_spread",
            supporting_signals=["ad_ratio=1.10", "vix=15.00"],
        )
        self.assertEqual(coerced.strategy, StrategyName.BULL_CALL_SPREAD)

        with self.assertRaises(ValidationError):
            StrategyDecision(
                strategy="short_strangle",
                supporting_signals=["ad_ratio=1.10", "vix=15.00"],
            )


class OpenPositionStrategyTests(unittest.TestCase):
    def test_open_position_rejects_disallowed_strategy(self) -> None:
        with self.assertRaises(ValidationError):
            OpenPosition(
                symbol="NSE:NIFTY24JUN25000CE",
                strategy="short_strangle",
                lots=1,
                entry_price=90.0,
            )


if __name__ == "__main__":
    unittest.main()
