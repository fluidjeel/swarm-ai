"""Tests for Agent 3 pre-trade critic."""

from __future__ import annotations

import unittest

from src.agents.pre_trade_critic import validate_pre_trade
from src.config.risk_config import RiskConfig
from src.core.context import AgentContext, CriticStatus

CONFIG = RiskConfig()


def _ready_ctx(*, snapshot: float = 24850.0, baseline: bool = True) -> AgentContext:
    return AgentContext(
        session_id="critic-test-session",
        feature_snapshot_price=snapshot if baseline else None,
        baseline_initialized=baseline,
    )


class PreTradeCriticTests(unittest.TestCase):
    def test_approves_clean_inputs(self) -> None:
        result = validate_pre_trade(
            _ready_ctx(),
            live_underlying_ltp=24852.0,
            bid_ask_spread_pct=0.02,
            greeks_confidence="high",
            greeks_delta=0.25,
            greeks_gamma=0.01,
            config=CONFIG,
        )
        self.assertEqual(result.critic_decision.status, CriticStatus.APPROVE)

    def test_rejects_baseline_not_initialized(self) -> None:
        result = validate_pre_trade(
            _ready_ctx(baseline=False),
            live_underlying_ltp=24850.0,
            bid_ask_spread_pct=0.02,
            greeks_confidence="high",
            greeks_delta=0.25,
            greeks_gamma=0.01,
            config=CONFIG,
        )
        self.assertEqual(result.critic_decision.reason, "baseline_not_initialized")

    def test_rejects_missing_snapshot(self) -> None:
        ctx = AgentContext(session_id="critic-test-session", baseline_initialized=True)
        result = validate_pre_trade(
            ctx,
            live_underlying_ltp=24850.0,
            bid_ask_spread_pct=0.02,
            greeks_confidence="high",
            greeks_delta=0.25,
            greeks_gamma=0.01,
            config=CONFIG,
        )
        self.assertEqual(result.critic_decision.reason, "snapshot_price_missing")

    def test_rejects_stale_quote_above_threshold(self) -> None:
        result = validate_pre_trade(
            _ready_ctx(snapshot=24850.0),
            live_underlying_ltp=24861.0,
            bid_ask_spread_pct=0.02,
            greeks_confidence="high",
            greeks_delta=0.25,
            greeks_gamma=0.01,
            config=CONFIG,
        )
        self.assertEqual(result.critic_decision.reason, "stale_quote_abort")

    def test_allows_stale_quote_at_threshold(self) -> None:
        result = validate_pre_trade(
            _ready_ctx(snapshot=24850.0),
            live_underlying_ltp=24860.0,
            bid_ask_spread_pct=0.02,
            greeks_confidence="high",
            greeks_delta=0.25,
            greeks_gamma=0.01,
            config=CONFIG,
        )
        self.assertEqual(result.critic_decision.status, CriticStatus.APPROVE)

    def test_rejects_wide_spread(self) -> None:
        result = validate_pre_trade(
            _ready_ctx(),
            live_underlying_ltp=24850.0,
            bid_ask_spread_pct=0.06,
            greeks_confidence="high",
            greeks_delta=0.25,
            greeks_gamma=0.01,
            config=CONFIG,
        )
        self.assertEqual(result.critic_decision.reason, "spread_too_wide")

    def test_rejects_low_greeks_confidence(self) -> None:
        result = validate_pre_trade(
            _ready_ctx(),
            live_underlying_ltp=24850.0,
            bid_ask_spread_pct=0.02,
            greeks_confidence="low",
            greeks_delta=0.25,
            greeks_gamma=0.01,
            config=CONFIG,
        )
        self.assertEqual(result.critic_decision.reason, "greeks_low_confidence")

    def test_rejects_greeks_out_of_bounds(self) -> None:
        result = validate_pre_trade(
            _ready_ctx(),
            live_underlying_ltp=24850.0,
            bid_ask_spread_pct=0.02,
            greeks_confidence="high",
            greeks_delta=1.5,
            greeks_gamma=0.01,
            config=CONFIG,
        )
        self.assertEqual(result.critic_decision.reason, "greeks_out_of_bounds")

    def test_rejects_negative_gamma(self) -> None:
        result = validate_pre_trade(
            _ready_ctx(),
            live_underlying_ltp=24850.0,
            bid_ask_spread_pct=0.02,
            greeks_confidence="high",
            greeks_delta=0.25,
            greeks_gamma=-0.01,
            config=CONFIG,
        )
        self.assertEqual(result.critic_decision.reason, "greeks_out_of_bounds")


if __name__ == "__main__":
    unittest.main()
