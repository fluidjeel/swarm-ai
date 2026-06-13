"""Tests for per-leg friction model."""

from __future__ import annotations

import unittest

from src.core.context import OpenPosition, StrategyName
from src.data.base_provider import Quote
from src.execution.noop_port import compute_paper_mtm, paper_exit_net_pnl
from src.risk.friction import (
    FRICTION_PER_LEG_ROUND_TRIP_INR,
    compute_entry_credit_inr,
    compute_exit_close_cost_inr,
    compute_gross_pnl_inr,
    compute_paper_exit_net_pnl,
    friction_ev_threshold_inr,
    passes_friction_ev_gate,
    round_trip_friction,
)


def _iron_condor_quotes() -> dict[str, Quote]:
    symbols = [
        "NSE:NIFTY24JUN24000PE",
        "NSE:NIFTY24JUN24100PE",
        "NSE:NIFTY24JUN25000CE",
        "NSE:NIFTY24JUN25100CE",
    ]
    prices = [80.0, 120.0, 90.0, 60.0]
    return {
        symbol: Quote(
            symbol=symbol,
            bid=price,
            ask=price,
            ltp=price,
            spread_pct=0.02,
        )
        for symbol, price in zip(symbols, prices, strict=True)
    }


class FrictionModelTests(unittest.TestCase):
    def test_per_strategy_round_trip_costs(self) -> None:
        self.assertEqual(round_trip_friction(StrategyName.IRON_CONDOR), 160.0)
        self.assertEqual(round_trip_friction(StrategyName.BULL_CALL_SPREAD), 80.0)
        self.assertEqual(round_trip_friction(StrategyName.BEAR_PUT_SPREAD), 80.0)
        self.assertEqual(round_trip_friction("unknown_strategy"), 40.0)

    def test_explicit_leg_count_override(self) -> None:
        self.assertEqual(
            round_trip_friction(StrategyName.IRON_CONDOR, leg_count=2),
            2 * FRICTION_PER_LEG_ROUND_TRIP_INR,
        )

    def test_friction_ev_gate(self) -> None:
        friction = round_trip_friction(StrategyName.IRON_CONDOR)
        threshold = friction_ev_threshold_inr(friction)
        self.assertEqual(threshold, 320.0)
        self.assertFalse(passes_friction_ev_gate(300.0, friction))
        self.assertTrue(passes_friction_ev_gate(320.0, friction))

    def test_paper_exit_net_pnl_deducts_friction(self) -> None:
        net, friction = compute_paper_exit_net_pnl(
            150.0,
            strategy=StrategyName.IRON_CONDOR,
        )
        self.assertEqual(friction, 160.0)
        self.assertEqual(net, -10.0)

    def test_paper_exit_net_pnl_uses_position_leg_count(self) -> None:
        from src.core.context import OpenPosition

        legs = [
            OpenPosition(
                symbol=f"NSE:NIFTY26JUN2500{i}CE",
                strategy=StrategyName.IRON_CONDOR,
                lots=1,
                entry_price=100.0,
                leg_id=f"leg-{i}",
            )
            for i in range(4)
        ]
        position = OpenPosition(
            symbol="NSE:NIFTY26JUN25000CE",
            strategy=StrategyName.IRON_CONDOR,
            lots=1,
            entry_price=100.0,
            legs=legs,
        )
        net, friction = paper_exit_net_pnl(200.0, position)
        self.assertEqual(friction, 160.0)
        self.assertEqual(net, 40.0)

    def test_entry_and_exit_mtm_for_iron_condor(self) -> None:
        quotes = _iron_condor_quotes()
        leg_symbols = list(quotes.keys())
        lot_size = 50
        entry_credit = compute_entry_credit_inr(
            StrategyName.IRON_CONDOR,
            leg_symbols=leg_symbols,
            per_leg_quotes=quotes,
            lot_size=lot_size,
            lots=1,
        )
        self.assertEqual(entry_credit, 3500.0)
        exit_cost = compute_exit_close_cost_inr(
            StrategyName.IRON_CONDOR,
            leg_symbols=leg_symbols,
            per_leg_quotes=quotes,
            lot_size=lot_size,
            lots=1,
        )
        self.assertEqual(exit_cost, 3500.0)
        self.assertEqual(compute_gross_pnl_inr(entry_credit, exit_cost), 0.0)

    def test_paper_mtm_applies_friction_on_profit(self) -> None:
        quotes = _iron_condor_quotes()
        short_leg = "NSE:NIFTY24JUN24100PE"
        exit_quotes = dict(quotes)
        exit_quotes[short_leg] = Quote(
            symbol=short_leg,
            bid=40.0,
            ask=40.0,
            ltp=40.0,
            spread_pct=0.02,
        )
        position = OpenPosition(
            symbol="iron_condor_summary",
            strategy=StrategyName.IRON_CONDOR,
            lots=1,
            entry_price=70.0,
            entry_cash_flow_inr=3500.0,
            legs=[
                OpenPosition(
                    symbol=symbol,
                    strategy=StrategyName.IRON_CONDOR,
                    lots=1,
                    entry_price=quotes[symbol].ltp,
                    leg_id=symbol,
                )
                for symbol in quotes
            ],
        )
        mtm = compute_paper_mtm(position, per_leg_quotes=exit_quotes, lot_size=50)
        self.assertGreater(mtm["gross_pnl_inr"], 0.0)
        self.assertEqual(mtm["friction_inr"], 160.0)
        self.assertEqual(
            mtm["net_pnl_inr"],
            mtm["gross_pnl_inr"] - mtm["friction_inr"],
        )


if __name__ == "__main__":
    unittest.main()
