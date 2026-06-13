"""Tests for Risk Gatekeeper hard rules."""

from __future__ import annotations

import unittest
from unittest import mock

from src.config.risk_config import RiskConfig
from src.core.context import AgentContext, CriticDecision, CriticStatus, OpeningRegime, StrategyDecision
from src.risk.gatekeeper import (
    GatekeeperRule,
    GatekeeperVerdict,
    RiskGatekeeper,
    compute_allowed_lots,
    evaluate_from_context,
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
        self.assertEqual(decision.expected_round_trip_cost, 160.0)

    def test_rejects_expiry_day_iron_condor(self) -> None:
        payload = dict(self.base_payload, dte=1)
        decision = self.gatekeeper.evaluate(
            strategy="iron_condor",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(decision.rule_id, GatekeeperRule.GAMMA_DTE_FILTER)

    def test_rejects_high_vix_iron_condor(self) -> None:
        payload = dict(self.base_payload, vix=19.0)
        decision = self.gatekeeper.evaluate(
            strategy="iron_condor",
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

    def test_approves_iron_condor_at_dte_boundary(self) -> None:
        payload = dict(self.base_payload, dte=2)
        decision = self.gatekeeper.evaluate(
            strategy="iron_condor",
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

    def test_vix_at_exact_ceiling_passes_for_iron_condor(self) -> None:
        payload = dict(self.base_payload, vix=18.0)
        decision = self.gatekeeper.evaluate(
            strategy="iron_condor",
            feature_payload=payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.APPROVE)

    def test_vix_just_above_ceiling_rejects_iron_condor(self) -> None:
        payload = dict(self.base_payload, vix=18.01)
        decision = self.gatekeeper.evaluate(
            strategy="iron_condor",
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

    def test_options_slippage_cost_for_all_strategies(self) -> None:
        self.assertEqual(round_trip_slippage("bull_call_spread"), 80.0)
        self.assertEqual(round_trip_slippage("iron_condor"), 160.0)
        self.assertEqual(round_trip_slippage("bear_put_spread"), 80.0)
        decision = self.gatekeeper.evaluate(
            strategy="bull_call_spread",
            feature_payload=self.base_payload,
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.expected_round_trip_cost, 80.0)


class EvaluateFromContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = RiskConfig()
        self.base_ctx = AgentContext(
            session_id="gatekeeper-ctx-session",
            opening_regime=OpeningRegime(vix=15.0, nifty_ad_ratio=1.1),
            dte=7,
            strategy_decision=StrategyDecision(
                strategy="iron_condor",
                supporting_signals=["ad_ratio=1.10", "vix=15.00"],
            ),
            critic_decision=CriticDecision(
                status=CriticStatus.APPROVE,
                reason="math_checks_passed",
            ),
        )

    def test_approves_when_critic_passes(self) -> None:
        result = evaluate_from_context(self.base_ctx, config=self.config)
        self.assertEqual(result.gatekeeper_decision.verdict, GatekeeperVerdict.APPROVE)

    def test_rejects_on_critic_block(self) -> None:
        ctx = self.base_ctx.update(
            critic_decision=CriticDecision(status=CriticStatus.REJECT, reason="spread_too_wide")
        )
        result = evaluate_from_context(ctx, config=self.config)
        self.assertEqual(result.gatekeeper_decision.rule_id, GatekeeperRule.CRITIC_BLOCK)

    def test_stale_quote_block_rule(self) -> None:
        ctx = self.base_ctx.update(
            critic_decision=CriticDecision(status=CriticStatus.REJECT, reason="stale_quote_abort")
        )
        result = evaluate_from_context(ctx, config=self.config)
        self.assertEqual(result.gatekeeper_decision.rule_id, GatekeeperRule.STALE_QUOTE_BLOCK)

    def test_max_loss_day_block(self) -> None:
        ctx = self.base_ctx.update(daily_pnl=-8001.0, circuit_status=True)
        result = evaluate_from_context(ctx, config=self.config)
        self.assertEqual(result.gatekeeper_decision.rule_id, GatekeeperRule.MAX_LOSS_DAY_BLOCK)

    def test_max_lots_block(self) -> None:
        result = evaluate_from_context(self.base_ctx, config=self.config, requested_lots=10)
        self.assertEqual(result.gatekeeper_decision.rule_id, GatekeeperRule.MAX_LOTS_BLOCK)

    def test_cash_no_trade_rejects(self) -> None:
        ctx = self.base_ctx.update(
            strategy_decision=StrategyDecision(
                strategy="cash_no_trade",
                supporting_signals=["no_trade", "choppy"],
            )
        )
        result = evaluate_from_context(ctx, config=self.config)
        self.assertEqual(result.gatekeeper_decision.rule_id, GatekeeperRule.CASH_NO_TRADE)

    def test_missing_vix_rejects_iron_condor_without_keyerror(self) -> None:
        ctx = self.base_ctx.update(opening_regime=OpeningRegime(nifty_ad_ratio=1.1))
        result = evaluate_from_context(ctx, config=self.config)
        self.assertEqual(result.gatekeeper_decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(result.gatekeeper_decision.rule_id, GatekeeperRule.MISSING_DATA)

    def test_missing_vix_does_not_break_directional_strategy(self) -> None:
        ctx = self.base_ctx.update(
            opening_regime=OpeningRegime(nifty_ad_ratio=1.1),
            strategy_decision=StrategyDecision(
                strategy="bull_call_spread",
                supporting_signals=["ad_ratio=1.10", "trend_up"],
            ),
        )
        result = evaluate_from_context(ctx, config=self.config)
        self.assertEqual(result.gatekeeper_decision.verdict, GatekeeperVerdict.APPROVE)

    def test_friction_ev_block_when_max_profit_below_threshold(self) -> None:
        result = evaluate_from_context(
            self.base_ctx,
            config=self.config,
            estimated_max_profit_inr=300.0,
        )
        self.assertEqual(result.gatekeeper_decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(result.gatekeeper_decision.rule_id, GatekeeperRule.FRICTION_EV_BLOCK)

    def test_friction_ev_passes_at_threshold(self) -> None:
        result = evaluate_from_context(
            self.base_ctx,
            config=self.config,
            estimated_max_profit_inr=320.0,
        )
        self.assertEqual(result.gatekeeper_decision.verdict, GatekeeperVerdict.APPROVE)

    def test_rejects_iron_condor_in_low_vix_environment(self) -> None:
        ctx = self.base_ctx.update(opening_regime=OpeningRegime(vix=12.5, nifty_ad_ratio=1.1))
        result = evaluate_from_context(ctx, config=self.config)
        self.assertEqual(result.gatekeeper_decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(
            result.gatekeeper_decision.rule_id,
            GatekeeperRule.LOW_VOLATILITY_ENVIRONMENT,
        )

    def test_iv_percentile_gate_rejects_when_below_min(self) -> None:
        ctx = AgentContext(
            session_id="gatekeeper-ivp-session",
            opening_regime=OpeningRegime(vix=16.0, nifty_ad_ratio=1.1),
            dte=7,
            strategy_decision=StrategyDecision(
                strategy="iron_condor",
                supporting_signals=["ad_ratio=1.10", "vix=16.00"],
            ),
            critic_decision=CriticDecision(
                status=CriticStatus.APPROVE,
                reason="math_checks_passed",
            ),
        )
        with mock.patch(
            "src.risk.gatekeeper._feature_payload_from_ctx",
            return_value={"vix": 16.0, "dte": 7, "iv_percentile": 18.0},
        ):
            result = evaluate_from_context(ctx, config=self.config)
        self.assertEqual(result.gatekeeper_decision.verdict, GatekeeperVerdict.REJECT)
        self.assertEqual(
            result.gatekeeper_decision.rule_id,
            GatekeeperRule.LOW_VOLATILITY_ENVIRONMENT,
        )


class RiskGatekeeperMissingDataTests(unittest.TestCase):
    def test_evaluate_rejects_iron_condor_when_vix_missing(self) -> None:
        gatekeeper = RiskGatekeeper()
        decision = gatekeeper.evaluate(
            strategy="iron_condor",
            feature_payload={"dte": 7},
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.rule_id, GatekeeperRule.MISSING_DATA)

    def test_evaluate_approves_bull_spread_without_vix(self) -> None:
        gatekeeper = RiskGatekeeper()
        decision = gatekeeper.evaluate(
            strategy="bull_call_spread",
            feature_payload={"dte": 7},
            daily_realized_pnl=0.0,
        )
        self.assertEqual(decision.verdict, GatekeeperVerdict.APPROVE)


if __name__ == "__main__":
    unittest.main()
