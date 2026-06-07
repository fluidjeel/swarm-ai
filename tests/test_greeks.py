"""Tests for local Black-Scholes Greeks engine and Fyers enrichment."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agents.pre_trade_critic import validate_pre_trade
from src.config.risk_config import RiskConfig
from src.core.context import AgentContext, CriticStatus
from src.data.base_provider import OptionQuote
from src.data.fyers_provider import (
    FyersMarketDataProvider,
    _enrich_with_local_greeks,
    _parse_option_chain_quotes,
)
from src.features.greeks_engine import (
    bsm_call_price,
    bsm_greeks,
    bsm_put_price,
    compute_greeks_from_market,
    implied_volatility,
    _mid_or_ltp,
)


class GreeksEngineTests(unittest.TestCase):
    def test_bsm_call_put_parity(self) -> None:
        spot, strike, t_years, r, q, sigma = 10000.0, 10000.0, 0.05, 0.065, 0.0, 0.18
        call = bsm_call_price(spot, strike, t_years, r, q, sigma)
        put = bsm_put_price(spot, strike, t_years, r, q, sigma)
        forward = spot * math.exp(-q * t_years) - strike * math.exp(-r * t_years)
        self.assertLess(abs(call - put - forward), 0.05)

    def test_bsm_greeks_known_values(self) -> None:
        greeks = bsm_greeks(100.0, 100.0, 0.25, 0.05, 0.0, 0.20, is_call=True)
        self.assertAlmostEqual(greeks.delta, 0.5693, delta=0.01)
        self.assertAlmostEqual(greeks.gamma, 0.0392, delta=0.005)

    def test_put_delta_negative(self) -> None:
        greeks = bsm_greeks(100.0, 90.0, 0.25, 0.05, 0.0, 0.20, is_call=False)
        self.assertLess(greeks.delta, 0.0)

    def test_iv_recovers_known_sigma(self) -> None:
        t_years = 3 / 365.25
        price = bsm_call_price(18000.0, 18100.0, t_years, 0.065, 0.0, 0.18)
        result = implied_volatility(
            price, 18000.0, 18100.0, t_years, 0.065, 0.0, is_call=True
        )
        self.assertIsNotNone(result.iv)
        assert result.iv is not None
        self.assertAlmostEqual(result.iv, 0.18, delta=1e-3)

    def test_iv_returns_none_on_non_convergence(self) -> None:
        result = implied_volatility(-1.0, 100.0, 0.0, 0.25, 0.05, 0.0, is_call=True)
        self.assertIsNone(result.iv)

    def test_theta_is_per_day(self) -> None:
        greeks = bsm_greeks(100.0, 100.0, 0.25, 0.05, 0.0, 0.20, is_call=True)
        # A long ATM call decays in value as time passes -> theta is negative
        # (time is the seller's friend, the buyer's enemy).  Per-day theta
        # for sigma=0.20, T=0.25 should be roughly -0.03; the absolute
        # bound catches gross formula errors (annualized / 1 would be ~-10).
        self.assertLess(greeks.theta, 0.0)
        self.assertGreater(greeks.theta, -1.0)

    def test_theta_put_signs(self) -> None:
        # Long put: theta is also negative (puts decay too, just less for
        # deep ITM and more for OTM).  For an OTM put (S>K), per-day theta
        # should be negative but small in magnitude.
        otm_put = bsm_greeks(100.0, 110.0, 0.25, 0.05, 0.0, 0.20, is_call=False)
        self.assertLess(otm_put.theta, 0.0)
        # For an ITM put (S<K) with low rates, the +r*K*exp(-rT)*N(-d2)
        # term is positive, so theta is LESS negative (or even positive
        # for deep ITM short-dated puts).  Sanity-check that an ITM put
        # has theta > an OTM put's theta (less time decay).
        itm_put = bsm_greeks(100.0, 80.0, 0.25, 0.05, 0.0, 0.20, is_call=False)
        self.assertGreater(itm_put.theta, otm_put.theta)

    def test_vega_is_per_percentage_point(self) -> None:
        # vega is per 1 percentage point (0.01) IV move.  For ATM
        # S=K=100, T=0.25, r=0.05, sigma=0.20:
        #   d1 = 0.175, N'(d1) ≈ 0.3928
        #   raw vega = S * exp(-qT) * N'(d1) * sqrt(T) = 100 * 0.3928 * 0.5 = 19.64
        #   per-1% vega = 19.64 / 100 = 0.1964
        greeks = bsm_greeks(100.0, 100.0, 0.25, 0.05, 0.0, 0.20, is_call=True)
        self.assertAlmostEqual(greeks.vega, 0.1964, delta=0.005)
        # Sanity: vega must be strictly positive for long options.
        self.assertGreater(greeks.vega, 0.0)
        # Cross-check: per-1.0 vega (raw) is 100x larger.  This documents
        # the convention; if someone "fixes" the divide-by-100, this fails.
        raw_vega = greeks.vega * 100.0
        self.assertAlmostEqual(raw_vega, 19.64, delta=0.5)

    def test_atm_expiry_day_no_nan(self) -> None:
        market = compute_greeks_from_market(
            spot=100.0,
            strike=100.0,
            dte_days=0,
            r=0.05,
            option_ltp=2.0,
            bid=1.9,
            ask=2.1,
            oi=500,
            is_call=True,
        )
        self.assertTrue(math.isfinite(market.delta))
        self.assertTrue(math.isfinite(market.gamma))
        self.assertNotEqual(market.confidence, "high")

    def test_confidence_low_for_wide_spread(self) -> None:
        market = compute_greeks_from_market(
            spot=100.0,
            strike=100.0,
            dte_days=30,
            r=0.05,
            option_ltp=100.0,
            bid=95.0,
            ask=110.0,
            oi=5000,
            is_call=True,
        )
        self.assertGreater(market.spread_pct, 0.10)
        self.assertEqual(market.confidence, "low")

    def test_confidence_low_for_thin_oi(self) -> None:
        market = compute_greeks_from_market(
            spot=18250.5,
            strike=18200.0,
            dte_days=2,
            r=0.065,
            option_ltp=125.30,
            bid=124.80,
            ask=125.80,
            oi=10,
            is_call=True,
        )
        self.assertEqual(market.confidence, "low")

    def test_confidence_high_for_liquid_quote(self) -> None:
        market = compute_greeks_from_market(
            spot=18250.5,
            strike=18200.0,
            dte_days=2,
            r=0.065,
            option_ltp=125.30,
            bid=124.80,
            ask=125.80,
            oi=5000,
            is_call=True,
        )
        self.assertEqual(market.confidence, "high")
        self.assertIsNotNone(market.iv)

    def test_mid_or_ltp_fallback(self) -> None:
        self.assertEqual(_mid_or_ltp(None, None, 120.0), 120.0)
        self.assertEqual(_mid_or_ltp(0.0, 0.0, 120.0), 120.0)
        self.assertEqual(_mid_or_ltp(118.0, 122.0, 130.0), 120.0)

    def test_greeks_price_side_uses_ask(self) -> None:
        mid_result = compute_greeks_from_market(
            spot=18250.5,
            strike=18200.0,
            dte_days=2,
            r=0.065,
            option_ltp=125.30,
            bid=124.80,
            ask=125.80,
            oi=5000,
            is_call=True,
            price_side="mid",
        )
        ask_result = compute_greeks_from_market(
            spot=18250.5,
            strike=18200.0,
            dte_days=2,
            r=0.065,
            option_ltp=125.30,
            bid=124.80,
            ask=125.80,
            oi=5000,
            is_call=True,
            price_side="ask",
        )
        self.assertNotEqual(mid_result.iv, ask_result.iv)


class GreeksIntegrationTests(unittest.TestCase):
    @pytest.mark.golden
    def test_golden_nifty_strike_matches_nse(self) -> None:
        """Update with a real NSE row before relying on tight tolerance."""
        fixture_path = Path(__file__).parent / "fixtures" / "golden_nifty_chain.json"
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        market = compute_greeks_from_market(
            spot=payload["spot"],
            strike=payload["strike"],
            dte_days=payload["dte_days"],
            r=payload["r"],
            q=payload.get("q", 0.0),
            option_ltp=payload["ltp"],
            bid=payload["bid"],
            ask=payload["ask"],
            oi=payload["oi"],
            is_call=payload["option_type"] == "CE",
        )
        nse_delta = payload.get("nse_delta")
        if nse_delta is not None:
            self.assertAlmostEqual(market.delta, nse_delta, delta=0.02)
        else:
            self.assertGreaterEqual(market.delta, 0.45)
            self.assertLessEqual(market.delta, 0.65)

    def test_get_option_chain_quotes_preserves_rows_without_broker_greeks(self) -> None:
        expiry_ts = 1_810_000_000
        response = {
            "data": {
                "optionsChain": [
                    {
                        "symbol": "NSE:NIFTY24JUN25000CE",
                        "option_type": "CE",
                        "strike_price": 25000,
                        "ltp": 120.0,
                        "bid": 118.0,
                        "ask": 122.0,
                        "oi": 5000,
                    },
                    {
                        "symbol": "NSE:NIFTY24JUN25100CE",
                        "option_type": "CE",
                        "strike_price": 25100,
                        "ltp": 80.0,
                        "bid": 79.0,
                        "ask": 81.0,
                        "oi": 3000,
                    },
                    {
                        "symbol": "NSE:NIFTY24JUN24900PE",
                        "option_type": "PE",
                        "strike_price": 24900,
                        "ltp": 90.0,
                        "bid": 88.0,
                        "ask": 92.0,
                        "oi": 2500,
                    },
                ]
            }
        }
        quotes = _parse_option_chain_quotes(
            response, symbol="NSE:NIFTY50-INDEX", expiry_ts=expiry_ts
        )
        self.assertEqual(len(quotes), 3)

        config = RiskConfig()
        greeks = _enrich_with_local_greeks(
            quotes, spot=25050.0, expiry_ts=expiry_ts, config=config
        )
        self.assertEqual(len(greeks), 3)
        for leg in greeks:
            self.assertTrue(math.isfinite(leg.delta))
            self.assertTrue(math.isfinite(leg.gamma))

    def test_pipeline_greeks_low_confidence_rejects(self) -> None:
        expiry_ts = 1_810_000_000
        quote = OptionQuote(
            symbol="NSE:NIFTY24JUN25000CE",
            strike=25000.0,
            option_type="CE",
            bid=1.0,
            ask=2.0,
            ltp=5000.0,
            oi=5000,
        )
        config = RiskConfig(iv_solver_max_iter=1, iv_tolerance=1e-9)
        greeks = _enrich_with_local_greeks(
            [quote], spot=25050.0, expiry_ts=expiry_ts, config=config
        )
        self.assertEqual(greeks[0].confidence, "low")

        ctx = AgentContext(
            session_id="greeks-pipeline-test",
            feature_snapshot_price=25050.0,
            baseline_initialized=True,
        )
        result = validate_pre_trade(
            ctx,
            live_underlying_ltp=25050.0,
            bid_ask_spread_pct=0.02,
            greeks_confidence=greeks[0].confidence,
            leg_deltas=[greeks[0].delta],
            leg_gammas=[greeks[0].gamma],
            config=config,
        )
        self.assertEqual(result.critic_decision.status, CriticStatus.REJECT)
        self.assertEqual(result.critic_decision.reason, "greeks_low_confidence")


class FyersGreeksProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_option_chain_greeks_with_expiry_ts(self) -> None:
        provider = FyersMarketDataProvider(app_id="test-app", access_token="token")
        captured_payload: dict[str, str] = {}
        expiry_ts = 1_810_000_000

        def _optionchain(payload: dict[str, str]) -> dict[str, object]:
            captured_payload.update(payload)
            return {
                "s": "ok",
                "data": {
                    "optionsChain": [
                        {
                            "symbol": "NSE:NIFTY24JUN25000CE",
                            "option_type": "CE",
                            "strike_price": 25000,
                            "ltp": 120.0,
                            "bid": 118.0,
                            "ask": 122.0,
                            "oi": 5000,
                            "delta": 0.31,
                            "gamma": 0.01,
                        }
                    ]
                },
            }

        mock_client = MagicMock()
        mock_client.optionchain.side_effect = _optionchain
        with (
            patch.object(provider, "_get_client", return_value=mock_client),
            patch.object(provider, "get_index_ltp", return_value=25050.0),
        ):
            greeks = await provider.get_option_chain_greeks(
                "NSE:NIFTY50-INDEX",
                expiry_ts,
            )

        self.assertEqual(captured_payload["timestamp"], str(expiry_ts))
        self.assertEqual(len(greeks), 1)
        self.assertEqual(greeks[0].symbol, "NSE:NIFTY24JUN25000CE")
        self.assertTrue(math.isfinite(greeks[0].delta))
        self.assertTrue(math.isfinite(greeks[0].gamma))


if __name__ == "__main__":
    unittest.main()
