"""Integration-style tests for symbol resolver + critic wiring."""

from __future__ import annotations

import unittest

from src.agents.pre_trade_critic import validate_pre_trade
from src.agents.symbol_resolver import select_strategy_symbols_for_strategy
from src.config.risk_config import RiskConfig
from src.core.context import AgentContext


def _iron_condor_chain():
    from src.data.base_provider import OptionGreeks

    return [
        OptionGreeks(
            symbol="NSE:NIFTY24JUN24500PE",
            strike=24500.0,
            option_type="PE",
            delta=-0.15,
            gamma=0.01,
            confidence="high",
        ),
        OptionGreeks(
            symbol="NSE:NIFTY24JUN24700PE",
            strike=24700.0,
            option_type="PE",
            delta=-0.31,
            gamma=0.01,
            confidence="high",
        ),
        OptionGreeks(
            symbol="NSE:NIFTY24JUN25300CE",
            strike=25300.0,
            option_type="CE",
            delta=0.31,
            gamma=0.01,
            confidence="high",
        ),
        OptionGreeks(
            symbol="NSE:NIFTY24JUN25500CE",
            strike=25500.0,
            option_type="CE",
            delta=0.15,
            gamma=0.01,
            confidence="high",
        ),
    ]


class SymbolResolverCriticIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = RiskConfig()
        self.ctx = AgentContext(
            session_id="symbol-critic-01",
            baseline_initialized=True,
            feature_snapshot_price=25000.0,
        )

    def test_selected_option_symbols_are_not_index(self) -> None:
        legs = select_strategy_symbols_for_strategy(
            "iron_condor",
            greeks_list=_iron_condor_chain(),
            config=self.config,
        )
        self.assertTrue(all("NIFTY50-INDEX" not in leg.symbol for leg in legs))
        self.assertTrue(all(leg.symbol.endswith(("CE", "PE")) for leg in legs))

    def test_critic_uses_index_ltp_not_option_ltp(self) -> None:
        legs = select_strategy_symbols_for_strategy(
            "iron_condor",
            greeks_list=_iron_condor_chain(),
            config=self.config,
        )
        approved = validate_pre_trade(
            self.ctx,
            live_underlying_ltp=25000.0,
            bid_ask_spread_pct=0.01,
            greeks_confidence=min(leg.confidence for leg in legs),
            leg_deltas=[leg.delta for leg in legs],
            leg_gammas=[leg.gamma for leg in legs],
            config=self.config,
        )
        self.assertEqual(approved.critic_decision.status.value, "APPROVE")

    def test_critic_rejects_when_index_moved_beyond_stale_threshold(self) -> None:
        legs = select_strategy_symbols_for_strategy(
            "iron_condor",
            greeks_list=_iron_condor_chain(),
            config=self.config,
        )
        rejected = validate_pre_trade(
            self.ctx,
            live_underlying_ltp=25020.0,
            bid_ask_spread_pct=0.01,
            greeks_confidence=min(leg.confidence for leg in legs),
            leg_deltas=[leg.delta for leg in legs],
            leg_gammas=[leg.gamma for leg in legs],
            config=self.config,
        )
        self.assertEqual(rejected.critic_decision.reason, "stale_quote_abort")

    def test_net_delta_near_zero_for_iron_condor(self) -> None:
        legs = select_strategy_symbols_for_strategy(
            "iron_condor",
            greeks_list=_iron_condor_chain(),
            config=self.config,
        )
        net_delta = sum(leg.delta for leg in legs)
        self.assertLess(abs(net_delta), 0.20)

    def test_net_gamma_within_gatekeeper_ceiling(self) -> None:
        legs = select_strategy_symbols_for_strategy(
            "iron_condor",
            greeks_list=_iron_condor_chain(),
            config=self.config,
        )
        net_gamma = sum(leg.gamma for leg in legs)
        self.assertLessEqual(net_gamma, self.config.max_gamma)

    def test_disallowed_strategy_raises_from_symbol_resolver(self) -> None:
        with self.assertRaises(ValueError):
            select_strategy_symbols_for_strategy(
                "short_strangle",
                greeks_list=_iron_condor_chain(),
                config=self.config,
            )

    def test_bull_call_spread_returns_only_calls(self) -> None:
        from src.data.base_provider import OptionGreeks

        chain = [
            OptionGreeks(
                symbol="NSE:NIFTY24JUN24900CE",
                strike=24900.0,
                option_type="CE",
                delta=0.52,
                gamma=0.01,
                confidence="high",
            ),
            OptionGreeks(
                symbol="NSE:NIFTY24JUN25200CE",
                strike=25200.0,
                option_type="CE",
                delta=0.21,
                gamma=0.01,
                confidence="high",
            ),
        ]
        legs = select_strategy_symbols_for_strategy(
            "bull_call_spread",
            greeks_list=chain,
            config=self.config,
        )
        self.assertTrue(all(leg.option_type == "CE" for leg in legs))

    def test_bear_put_spread_returns_only_puts(self) -> None:
        from src.data.base_provider import OptionGreeks

        chain = [
            OptionGreeks(
                symbol="NSE:NIFTY24JUN24500PE",
                strike=24500.0,
                option_type="PE",
                delta=-0.52,
                gamma=0.01,
                confidence="high",
            ),
            OptionGreeks(
                symbol="NSE:NIFTY24JUN24800PE",
                strike=24800.0,
                option_type="PE",
                delta=-0.21,
                gamma=0.01,
                confidence="high",
            ),
        ]
        legs = select_strategy_symbols_for_strategy(
            "bear_put_spread",
            greeks_list=chain,
            config=self.config,
        )
        self.assertTrue(all(leg.option_type == "PE" for leg in legs))


if __name__ == "__main__":
    unittest.main()
