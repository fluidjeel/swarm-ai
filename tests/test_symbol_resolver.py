"""Tests for strike and expiry selection."""

from __future__ import annotations

import time
import unittest
from datetime import date, datetime

from src.agents.symbol_resolver import (
    ExpirySelectionError,
    StrikeSelectionError,
    select_expiry,
    select_strike,
    select_strategy_symbols,
    select_strategy_symbols_for_strategy,
)
from src.config.risk_config import RiskConfig
from src.core.context import AgentContext, RegimeLabel, StrategyDecision
from src.data.base_provider import OptionGreeks
from src.orchestration.session_clock import IST


def _make_greeks(
    *,
    strike: float,
    delta: float,
    option_type: str,
    symbol: str | None = None,
    gamma: float = 0.01,
) -> OptionGreeks:
    suffix = "CE" if option_type.upper() == "CE" else "PE"
    return OptionGreeks(
        symbol=symbol or f"NSE:NIFTY24JUN{int(strike)}{suffix}",
        strike=strike,
        option_type=option_type.upper(),
        delta=delta,
        gamma=gamma,
        confidence="high",
    )


def _iron_condor_greeks_chain() -> list[OptionGreeks]:
    return [
        _make_greeks(strike=24500.0, delta=-0.15, option_type="PE"),
        _make_greeks(strike=24700.0, delta=-0.31, option_type="PE"),
        _make_greeks(strike=24900.0, delta=-0.45, option_type="PE"),
        _make_greeks(strike=25100.0, delta=0.15, option_type="CE"),
        _make_greeks(strike=25300.0, delta=0.31, option_type="CE"),
        _make_greeks(strike=25500.0, delta=0.45, option_type="CE"),
    ]


def _ctx(*, strategy: str, dte: int = 3) -> AgentContext:
    return AgentContext(
        session_id="symbol-resolver-01",
        dte=dte,
        regime_decision=RegimeLabel.RANGE,
        strategy_decision=StrategyDecision(
            strategy=strategy,
            supporting_signals=["ad_ratio=1.05", "vix=14.00"],
        ),
    )


class WeeklyExpiryTests(unittest.TestCase):
    def test_weekly_expiry_timestamps_returns_two_values(self) -> None:
        from src.agents.symbol_resolver import _weekly_expiry_timestamps

        monday_ist = datetime(2025, 6, 2, 10, 0, tzinfo=IST)
        expiries = _weekly_expiry_timestamps(weekday=3, now=monday_ist, count=2)
        self.assertEqual(len(expiries), 2)
        self.assertLess(expiries[0], expiries[1])

    def test_nifty_tuesday_weekly_after_sep_2025(self) -> None:
        from src.agents.symbol_resolver import select_expiry

        tuesday_expiry_day = datetime(2026, 6, 9, 10, 27, tzinfo=IST)
        ctx = AgentContext(session_id="symbol-resolver-exp-00", dte=0)
        expiry_ts = select_expiry(ctx, RiskConfig(), now=tuesday_expiry_day)
        expiry_date = datetime.fromtimestamp(expiry_ts, tz=IST).date()
        self.assertEqual(expiry_date, date(2026, 6, 16))

    def test_is_valid_expiry_date_rejects_holiday_and_weekend(self) -> None:
        from src.agents.symbol_resolver import _is_valid_expiry_date

        self.assertFalse(_is_valid_expiry_date(date(2026, 3, 26)))
        self.assertFalse(_is_valid_expiry_date(date(2026, 3, 28)))
        self.assertTrue(_is_valid_expiry_date(date(2026, 4, 2)))

    def test_weekly_expiry_timestamps_skips_nse_holiday_on_thursday(self) -> None:
        from src.agents.symbol_resolver import _weekly_expiry_timestamps

        # 2026-03-26 (Good Friday) is a Thursday NSE holiday.
        wednesday_before = datetime(2026, 3, 25, 10, 0, tzinfo=IST)
        expiries = _weekly_expiry_timestamps(weekday=3, now=wednesday_before, count=1)
        self.assertEqual(len(expiries), 1)
        expiry_date = datetime.fromtimestamp(expiries[0], tz=IST).date()
        self.assertNotEqual(expiry_date, date(2026, 3, 26))
        self.assertEqual(expiry_date, date(2026, 4, 2))


class SelectExpiryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = RiskConfig()
        self.monday_ist = datetime(2025, 6, 2, 10, 0, tzinfo=IST)

    def test_select_expiry_returns_current_week_when_dte_in_band(self) -> None:
        ctx = AgentContext(session_id="symbol-resolver-exp-01", dte=3)
        expiry_ts = select_expiry(ctx, self.config, now=self.monday_ist)
        self.assertGreater(expiry_ts, int(self.monday_ist.timestamp()))

    def test_select_expiry_rolls_forward_when_pcr_dte_is_zero(self) -> None:
        ctx = AgentContext(session_id="symbol-resolver-exp-02", dte=0)
        expiry_ts = select_expiry(ctx, self.config, now=self.monday_ist)
        self.assertGreater(expiry_ts, int(self.monday_ist.timestamp()))

    def test_select_expiry_sensex_uses_thursday_weekly(self) -> None:
        nifty_expiry_tuesday = datetime(2026, 6, 9, 10, 0, tzinfo=IST)
        ctx = AgentContext(session_id="symbol-resolver-exp-03", dte=0)
        expiry_ts = select_expiry(
            ctx,
            self.config,
            index_symbol="BSE:SENSEX-INDEX",
            now=nifty_expiry_tuesday,
        )
        expiry_date = datetime.fromtimestamp(expiry_ts, tz=IST).date()
        self.assertEqual(expiry_date.weekday(), 3)
        self.assertEqual(expiry_date, date(2026, 6, 11))

    def test_select_expiry_rejects_when_no_weekly_in_band(self) -> None:
        config = RiskConfig(min_dte_for_entry=25, max_dte_for_entry=30)
        ctx = AgentContext(session_id="symbol-resolver-exp-04", dte=25)
        with self.assertRaises(ExpirySelectionError):
            select_expiry(ctx, config, now=self.monday_ist)


class SelectStrikeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = RiskConfig()

    def test_select_strike_iron_condor_picks_4_laddered_legs(self) -> None:
        legs = select_strategy_symbols_for_strategy(
            "iron_condor",
            greeks_list=_iron_condor_greeks_chain(),
            config=self.config,
        )
        self.assertEqual(len(legs), 4)
        strikes = [leg.strike for leg in legs]
        self.assertEqual(strikes[0], 24500.0)
        self.assertEqual(strikes[1], 24700.0)
        self.assertEqual(strikes[2], 25300.0)
        self.assertEqual(strikes[3], 25500.0)
        self.assertEqual(strikes[1] - strikes[0], self.config.wing_width_points)
        self.assertEqual(strikes[3] - strikes[2], self.config.wing_width_points)

    def test_wing_width_uses_config_not_module_constant(self) -> None:
        config = RiskConfig(wing_width_points=300)
        chain = [
            _make_greeks(strike=24400.0, delta=-0.15, option_type="PE"),
            _make_greeks(strike=24700.0, delta=-0.31, option_type="PE"),
            _make_greeks(strike=25300.0, delta=0.31, option_type="CE"),
            _make_greeks(strike=25600.0, delta=0.15, option_type="CE"),
        ]
        legs = select_strategy_symbols_for_strategy(
            "iron_condor",
            greeks_list=chain,
            config=config,
        )
        strikes = [leg.strike for leg in legs]
        self.assertEqual(strikes[1] - strikes[0], 300)
        self.assertEqual(strikes[3] - strikes[2], 300)

    def test_select_strike_rejects_disallowed_strategy(self) -> None:
        with self.assertRaises(ValueError):
            select_strategy_symbols_for_strategy(
                "short_strangle",
                greeks_list=_iron_condor_greeks_chain(),
                config=self.config,
            )

    def test_select_strike_bull_call_spread_picks_2_calls(self) -> None:
        chain = [
            _make_greeks(strike=24900.0, delta=0.52, option_type="CE"),
            _make_greeks(strike=25200.0, delta=0.21, option_type="CE"),
            _make_greeks(strike=25500.0, delta=0.08, option_type="CE"),
        ]
        legs = select_strategy_symbols_for_strategy(
            "bull_call_spread",
            greeks_list=chain,
            config=self.config,
        )
        self.assertEqual(len(legs), 2)
        self.assertTrue(all(leg.option_type == "CE" for leg in legs))

    def test_select_strike_bear_put_spread_picks_2_puts(self) -> None:
        chain = [
            _make_greeks(strike=24500.0, delta=-0.52, option_type="PE"),
            _make_greeks(strike=24800.0, delta=-0.21, option_type="PE"),
            _make_greeks(strike=25100.0, delta=-0.08, option_type="PE"),
        ]
        legs = select_strategy_symbols_for_strategy(
            "bear_put_spread",
            greeks_list=chain,
            config=self.config,
        )
        self.assertEqual(len(legs), 2)
        self.assertTrue(all(leg.option_type == "PE" for leg in legs))

    def test_select_strike_returns_primary_leg_for_multi_leg_strategy(self) -> None:
        primary = select_strike(
            _iron_condor_greeks_chain(),
            strategy="iron_condor",
            config=self.config,
        )
        self.assertEqual(primary.strike, 24500.0)
        self.assertEqual(primary.option_type, "PE")

    def test_select_strike_raises_for_cash_no_trade(self) -> None:
        with self.assertRaises(ValueError):
            select_strike(_iron_condor_greeks_chain(), strategy="cash_no_trade", config=self.config)

    def test_select_strike_raises_when_no_strike_within_tolerance(self) -> None:
        chain = [_make_greeks(strike=25000.0, delta=-0.05, option_type="PE")]
        with self.assertRaises(StrikeSelectionError):
            select_strategy_symbols_for_strategy(
                "bear_put_spread",
                greeks_list=chain,
                config=self.config,
            )


class SelectStrategySymbolsTests(unittest.TestCase):
    def test_select_strategy_symbols_returns_correct_count_per_strategy(self) -> None:
        config = RiskConfig()
        chains = {
            "iron_condor": _iron_condor_greeks_chain(),
            "bull_call_spread": [
                _make_greeks(strike=24900.0, delta=0.52, option_type="CE"),
                _make_greeks(strike=25200.0, delta=0.21, option_type="CE"),
            ],
            "bear_put_spread": [
                _make_greeks(strike=24500.0, delta=-0.52, option_type="PE"),
                _make_greeks(strike=24800.0, delta=-0.21, option_type="PE"),
            ],
        }
        for strategy, expected_count in {
            "iron_condor": 4,
            "bull_call_spread": 2,
            "bear_put_spread": 2,
        }.items():
            with self.subTest(strategy=strategy):
                ctx = _ctx(strategy=strategy)
                legs = select_strategy_symbols(
                    ctx,
                    greeks_list=chains[strategy],
                    config=config,
                )
                self.assertEqual(len(legs), expected_count)
                self.assertTrue(all(leg.symbol.startswith("NSE:NIFTY") for leg in legs))

    def test_strike_selection_under_5ms(self) -> None:
        greeks: list[OptionGreeks] = []
        for i in range(10):
            strike = 24000.0 + i * 50.0
            greeks.append(_make_greeks(strike=strike, delta=-0.30, option_type="PE"))
            greeks.append(_make_greeks(strike=strike + 100.0, delta=0.30, option_type="CE"))
        ctx = _ctx(strategy="iron_condor")
        config = RiskConfig()
        start = time.perf_counter()
        legs = select_strategy_symbols(ctx, greeks_list=greeks, config=config)
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.assertEqual(len(legs), 4)
        self.assertLess(elapsed_ms, 5.0)


if __name__ == "__main__":
    unittest.main()
