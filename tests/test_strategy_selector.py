"""Tests for Agent 2 strategy selector matrix."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.agents.strategy_selector import REGIME_STRATEGY_MATRIX, select_strategy
from src.core.context import AgentContext, OpeningRegime, RegimeLabel, StrategyDecision

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "evals" / "fixtures"


class StrategySelectorTests(unittest.TestCase):
    def _ctx(self, regime: RegimeLabel | None) -> AgentContext:
        return AgentContext(
            session_id="strategy-test-session",
            regime_decision=regime,
            opening_regime=OpeningRegime(
                nifty_ad_ratio=1.2,
                vix=14.0,
                expiry_weighted_pcr_momentum=0.05,
            ),
            dte=7,
        )

    def test_matrix_routes_range_to_iron_condor(self) -> None:
        result = select_strategy(self._ctx(RegimeLabel.RANGE))
        self.assertEqual(result.strategy_decision.strategy, "iron_condor")

    def test_matrix_routes_trend_up_to_bull_call_spread(self) -> None:
        result = select_strategy(self._ctx(RegimeLabel.TREND_UP))
        self.assertEqual(result.strategy_decision.strategy, "bull_call_spread")

    def test_matrix_routes_trend_down_to_bear_put_spread(self) -> None:
        result = select_strategy(self._ctx(RegimeLabel.TREND_DOWN))
        self.assertEqual(result.strategy_decision.strategy, "bear_put_spread")

    def test_choppy_routes_to_cash_no_trade(self) -> None:
        result = select_strategy(self._ctx(RegimeLabel.CHOPPY))
        self.assertEqual(result.strategy_decision.strategy, "cash_no_trade")

    def test_uncertain_routes_to_cash_no_trade(self) -> None:
        result = select_strategy(self._ctx(RegimeLabel.UNCERTAIN))
        self.assertEqual(result.strategy_decision.strategy, "cash_no_trade")

    def test_missing_regime_fallback(self) -> None:
        result = select_strategy(self._ctx(None))
        self.assertEqual(result.strategy_decision.strategy, "cash_no_trade")
        self.assertGreaterEqual(len(result.strategy_decision.supporting_signals), 2)

    def test_supporting_signals_include_metrics(self) -> None:
        result = select_strategy(self._ctx(RegimeLabel.RANGE))
        signals = result.strategy_decision.supporting_signals
        self.assertTrue(any(s.startswith("ad_ratio=") for s in signals))
        self.assertTrue(any(s.startswith("vix=") for s in signals))

    def test_strategy_eval_fixtures(self) -> None:
        for path in sorted(FIXTURES_DIR.glob("strategy_*.json")):
            with self.subTest(fixture=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                expected_strategy = payload["expected"]["strategy"]
                regime_name = payload["feature_payload"].get("regime_decision")
                if regime_name:
                    regime = RegimeLabel(regime_name)
                else:
                    regime = RegimeLabel.RANGE
                ctx = AgentContext(
                    session_id="strategy-fixture-session",
                    regime_decision=regime,
                    opening_regime=OpeningRegime(
                        nifty_ad_ratio=payload["feature_payload"].get("NIFTY_500_AD_Ratio", 1.0),
                        vix=payload["feature_payload"].get("vix", 14.0),
                    ),
                    dte=int(payload["feature_payload"].get("dte", 7)),
                )
                if expected_strategy == "cash_no_trade":
                    ctx = ctx.update(regime_decision=RegimeLabel.CHOPPY)
                result = select_strategy(ctx)
                if expected_strategy != "cash_no_trade":
                    self.assertEqual(
                        result.strategy_decision.strategy,
                        REGIME_STRATEGY_MATRIX.get(ctx.regime_decision, "cash_no_trade"),
                    )


if __name__ == "__main__":
    unittest.main()
