"""Tests for Exit Engine hard exit rules."""

from __future__ import annotations

import unittest

from src.core.context import OpenPosition
from src.data.base_provider import Quote
from src.risk.exit_engine import (
    CreditSpreadPosition,
    ExitAction,
    ExitEngine,
    ExitEngineError,
    FuturesPosition,
    compute_atr_trailing_stop,
    compute_theta_capture_pct,
)


def _iron_condor_legs() -> list[OpenPosition]:
    return [
        OpenPosition(
            symbol="NSE:NIFTY24JUN24000PE",
            strategy="iron_condor",
            lots=1,
            entry_price=80.0,
            leg_id="NSE:NIFTY24JUN24000PE",
            strategy_id="iron_condor",
        ),
        OpenPosition(
            symbol="NSE:NIFTY24JUN24100PE",
            strategy="iron_condor",
            lots=1,
            entry_price=120.0,
            leg_id="NSE:NIFTY24JUN24100PE",
            strategy_id="iron_condor",
        ),
        OpenPosition(
            symbol="NSE:NIFTY24JUN25000CE",
            strategy="iron_condor",
            lots=1,
            entry_price=90.0,
            leg_id="NSE:NIFTY24JUN25000CE",
            strategy_id="iron_condor",
        ),
        OpenPosition(
            symbol="NSE:NIFTY24JUN25100CE",
            strategy="iron_condor",
            lots=1,
            entry_price=60.0,
            leg_id="NSE:NIFTY24JUN25100CE",
            strategy_id="iron_condor",
        ),
    ]


def _iron_condor_summary(*, entry_cash_flow_inr: float | None = None) -> OpenPosition:
    legs = _iron_condor_legs()
    entry_per_unit = 70.0
    return OpenPosition(
        symbol="iron_condor_summary",
        strategy="iron_condor",
        lots=1,
        entry_price=entry_per_unit,
        entry_cash_flow_inr=entry_cash_flow_inr if entry_cash_flow_inr is not None else entry_per_unit,
        strategy_id="iron_condor",
        legs=legs,
    )


def _leg_quotes(
    *,
    ask_by_symbol: dict[str, float] | None = None,
    default_ask: float | None = None,
) -> dict[str, Quote]:
    quotes: dict[str, Quote] = {}
    for leg in _iron_condor_legs():
        ask = (ask_by_symbol or {}).get(leg.symbol, default_ask or leg.entry_price)
        quotes[leg.symbol] = Quote(
            symbol=leg.symbol,
            bid=ask,
            ask=ask,
            ltp=ask,
            spread_pct=0.02,
        )
    return quotes


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

    def test_exits_on_credit_stop_loss(self) -> None:
        position = CreditSpreadPosition(entry_credit=100.0, current_close_cost=160.0)
        decision = self.engine.evaluate_credit_spread(
            position,
            feature_payload={"vix": 14.0},
            session_open_vix=14.0,
        )
        self.assertEqual(decision.action, ExitAction.EXIT_MARKET)
        self.assertEqual(decision.rule_id, "credit_stop_loss")

    def test_holds_below_credit_stop_threshold(self) -> None:
        position = CreditSpreadPosition(entry_credit=100.0, current_close_cost=149.0)
        decision = self.engine.evaluate_credit_spread(
            position,
            feature_payload={"vix": 14.0},
            session_open_vix=14.0,
        )
        self.assertEqual(decision.action, ExitAction.HOLD)


class ExitEngineMultiLegTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ExitEngine()

    def test_evaluate_position_holds_all_legs_when_healthy(self) -> None:
        decision = self.engine.evaluate_position(
            _iron_condor_summary(),
            feature_payload={"vix": 14.0},
            session_open_vix=14.0,
            per_leg_quotes=_leg_quotes(),
        )

        self.assertEqual(decision.action, ExitAction.HOLD)
        self.assertEqual(len(decision.leg_action_intents), 4)
        self.assertTrue(
            all(intent.action == "HOLD" for intent in decision.leg_action_intents)
        )

    def test_evaluate_position_exits_all_legs_on_theta_capture(self) -> None:
        short_leg = "NSE:NIFTY24JUN24100PE"
        decision = self.engine.evaluate_position(
            _iron_condor_summary(),
            feature_payload={"vix": 14.0},
            session_open_vix=14.0,
            per_leg_quotes=_leg_quotes(
                ask_by_symbol={short_leg: 40.0},
            ),
        )

        self.assertEqual(decision.action, ExitAction.EXIT_MARKET)
        self.assertEqual(decision.rule_id, "theta_capture")
        self.assertEqual(len(decision.leg_action_intents), 4)
        self.assertTrue(
            all(intent.action == "EXIT_MARKET" for intent in decision.leg_action_intents)
        )

    def test_evaluate_position_exits_all_legs_on_credit_stop(self) -> None:
        short_pe = "NSE:NIFTY24JUN24100PE"
        short_ce = "NSE:NIFTY24JUN25000CE"
        decision = self.engine.evaluate_position(
            _iron_condor_summary(),
            feature_payload={"vix": 14.0},
            session_open_vix=14.0,
            per_leg_quotes=_leg_quotes(
                ask_by_symbol={
                    short_pe: 200.0,
                    short_ce: 200.0,
                },
            ),
        )

        self.assertEqual(decision.action, ExitAction.EXIT_MARKET)
        self.assertEqual(decision.rule_id, "credit_stop_loss")

    def test_evaluate_position_uses_per_leg_quotes(self) -> None:
        short_leg = "NSE:NIFTY24JUN24100PE"
        healthy_quotes = _leg_quotes()
        stopped_quotes = _leg_quotes(ask_by_symbol={short_leg: 40.0})

        healthy = self.engine.evaluate_position(
            _iron_condor_summary(),
            feature_payload={"vix": 14.0},
            session_open_vix=14.0,
            per_leg_quotes=healthy_quotes,
        )
        stopped = self.engine.evaluate_position(
            _iron_condor_summary(),
            feature_payload={"vix": 14.0},
            session_open_vix=14.0,
            per_leg_quotes=stopped_quotes,
        )

        self.assertEqual(healthy.action, ExitAction.HOLD)
        self.assertEqual(stopped.action, ExitAction.EXIT_MARKET)

    def test_evaluate_position_raises_without_per_leg_quotes(self) -> None:
        with self.assertRaises(ExitEngineError) as exc:
            self.engine.evaluate_position(
                _iron_condor_summary(),
                feature_payload={"vix": 14.0},
                session_open_vix=14.0,
            )

        self.assertIn("per_leg_quotes required", str(exc.exception))

    def test_evaluate_position_emergency_flattens_on_broker_error(self) -> None:
        decision = self.engine.build_emergency_flatten_decision(_iron_condor_summary())

        self.assertEqual(decision.action, ExitAction.EXIT_MARKET)
        self.assertEqual(decision.rule_id, "broker_error_emergency_flatten")
        self.assertEqual(len(decision.leg_action_intents), 4)
        self.assertTrue(
            all(intent.action == "EXIT_MARKET" for intent in decision.leg_action_intents)
        )

    def test_evaluate_position_single_leg_delegates_to_existing_evaluate(self) -> None:
        single = OpenPosition(
            symbol="NSE:NIFTY-OPT",
            strategy="iron_condor",
            lots=1,
            entry_price=100.0,
        )
        payload = {"vix": 14.0}
        direct = self.engine.evaluate(
            strategy="iron_condor",
            position=CreditSpreadPosition(entry_credit=100.0, current_close_cost=35.0),
            feature_payload=payload,
            session_open_vix=14.0,
        )
        via_position = self.engine.evaluate_position(
            single,
            feature_payload=payload,
            session_open_vix=14.0,
            per_leg_quotes={
                single.symbol: Quote(
                    symbol=single.symbol,
                    bid=34.0,
                    ask=35.0,
                    ltp=34.5,
                    spread_pct=0.02,
                )
            },
        )

        self.assertEqual(via_position.action, direct.action)
        self.assertEqual(via_position.rule_id, direct.rule_id)
        self.assertEqual(via_position.leg_action_intents, [])

    def test_evaluate_position_vix_spike_exits_all_legs(self) -> None:
        decision = self.engine.evaluate_position(
            _iron_condor_summary(),
            feature_payload={"vix": 16.0},
            session_open_vix=14.0,
            per_leg_quotes=_leg_quotes(),
        )

        self.assertEqual(decision.action, ExitAction.EXIT_MARKET)
        self.assertEqual(decision.rule_id, "vix_intraday_spike")
        self.assertTrue(
            all(intent.action == "EXIT_MARKET" for intent in decision.leg_action_intents)
        )

    def test_evaluate_position_spread_theta_capture(self) -> None:
        short_leg = "NSE:NIFTY24JUN24100PE"
        decision = self.engine.evaluate_position(
            _iron_condor_summary(),
            feature_payload={"vix": 14.0},
            session_open_vix=14.0,
            per_leg_quotes=_leg_quotes(ask_by_symbol={short_leg: 45.0}),
        )

        entry_credit = 70.0
        close_cost = 45.0 + 90.0 - 80.0 - 60.0
        expected_theta = (entry_credit - close_cost) / entry_credit
        self.assertAlmostEqual(decision.theta_capture_pct, expected_theta)


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
