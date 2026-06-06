"""Tests for Exit Engine hard exit rules."""

from __future__ import annotations

import unittest

from src.risk.exit_engine import (
    CreditSpreadPosition,
    ExitAction,
    ExitEngine,
    FuturesPosition,
    compute_atr_trailing_stop,
    compute_theta_capture_pct,
)


def _sample_bars() -> list[dict]:
    return [
        {"timestamp": 1, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        {"timestamp": 2, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1},
        {"timestamp": 3, "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1},
    ]


class ExitEngineFuturesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ExitEngine()
        self.payload = {"NIFTY_500_AD_Ratio": 1.2, "vix": 14.0}

    def test_holds_healthy_long(self) -> None:
        position = FuturesPosition(
            side="long",
            entry_price=100.0,
            current_price=102.0,
            extreme_price=102.0,
        )
        decision = self.engine.evaluate_futures(
            position,
            feature_payload=self.payload,
            nifty_bars=_sample_bars(),
        )
        self.assertEqual(decision.action, ExitAction.HOLD)
        self.assertIsNotNone(decision.trailing_stop)

    def test_exits_long_on_regime_flip(self) -> None:
        position = FuturesPosition(
            side="long",
            entry_price=100.0,
            current_price=102.0,
            extreme_price=102.0,
        )
        decision = self.engine.evaluate_futures(
            position,
            feature_payload={"NIFTY_500_AD_Ratio": 0.9, "vix": 14.0},
            nifty_bars=_sample_bars(),
        )
        self.assertEqual(decision.action, ExitAction.EXIT_MARKET)
        self.assertEqual(decision.rule_id, "regime_flip")

    def test_exits_long_on_atr_stop_breach(self) -> None:
        position = FuturesPosition(
            side="long",
            entry_price=100.0,
            current_price=90.0,
            extreme_price=102.0,
        )
        decision = self.engine.evaluate_futures(
            position,
            feature_payload=self.payload,
            nifty_bars=_sample_bars(),
        )
        self.assertEqual(decision.action, ExitAction.EXIT_MARKET)
        self.assertEqual(decision.rule_id, "atr_trailing_stop")

    def test_exits_short_on_atr_stop_breach(self) -> None:
        position = FuturesPosition(
            side="short",
            entry_price=100.0,
            current_price=115.0,
            extreme_price=98.0,
        )
        decision = self.engine.evaluate_futures(
            position,
            feature_payload=self.payload,
            nifty_bars=_sample_bars(),
        )
        self.assertEqual(decision.action, ExitAction.EXIT_MARKET)
        self.assertEqual(decision.rule_id, "atr_trailing_stop")

    def test_short_not_exited_on_regime_flip(self) -> None:
        position = FuturesPosition(
            side="short",
            entry_price=100.0,
            current_price=98.0,
            extreme_price=98.0,
        )
        decision = self.engine.evaluate_futures(
            position,
            feature_payload={"NIFTY_500_AD_Ratio": 0.8, "vix": 14.0},
            nifty_bars=_sample_bars(),
        )
        self.assertEqual(decision.action, ExitAction.HOLD)


class ExitEngineCreditSpreadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ExitEngine()

    def test_holds_before_theta_target(self) -> None:
        position = CreditSpreadPosition(entry_credit=100.0, current_close_cost=50.0)
        decision = self.engine.evaluate_credit_spread(
            position,
            feature_payload={"vix": 14.0},
            session_open_vix=14.0,
        )
        self.assertEqual(decision.action, ExitAction.HOLD)
        self.assertAlmostEqual(decision.theta_capture_pct, 0.5)

    def test_exits_on_theta_capture(self) -> None:
        position = CreditSpreadPosition(entry_credit=100.0, current_close_cost=35.0)
        decision = self.engine.evaluate_credit_spread(
            position,
            feature_payload={"vix": 14.0},
            session_open_vix=14.0,
        )
        self.assertEqual(decision.action, ExitAction.EXIT_MARKET)
        self.assertEqual(decision.rule_id, "theta_capture")

    def test_exits_on_vix_spike(self) -> None:
        position = CreditSpreadPosition(entry_credit=100.0, current_close_cost=80.0)
        decision = self.engine.evaluate_credit_spread(
            position,
            feature_payload={"vix": 16.0},
            session_open_vix=14.0,
        )
        self.assertEqual(decision.action, ExitAction.EXIT_MARKET)
        self.assertEqual(decision.rule_id, "vix_intraday_spike")


class ExitEngineHelperTests(unittest.TestCase):
    def test_compute_atr_trailing_stop_long(self) -> None:
        stop = compute_atr_trailing_stop(side="long", extreme_price=110.0, atr=2.0)
        self.assertEqual(stop, 106.0)

    def test_compute_theta_capture_pct(self) -> None:
        pct = compute_theta_capture_pct(
            CreditSpreadPosition(entry_credit=200.0, current_close_cost=60.0)
        )
        self.assertAlmostEqual(pct, 0.7)


if __name__ == "__main__":
    unittest.main()
