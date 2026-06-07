"""Unit tests for Fyers market data parsing helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.data.base_provider import UntaggedPositionError
from src.data.fyers_provider import (
    FyersMarketDataProvider,
    _parse_breadth_from_quotes,
    _parse_history_candles,
    _parse_option_chain_pcr,
    _parse_positions,
    _sum_option_oi,
)


class FyersProviderParsingTests(unittest.TestCase):
    def test_parse_history_candles(self) -> None:
        response = {
            "s": "ok",
            "candles": [
                [1_700_000_000, 100.0, 101.0, 99.0, 100.5, 1000],
                [1_700_000_300, 100.5, 102.0, 100.0, 101.0, 1200],
            ],
        }
        bars = _parse_history_candles(response)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1]["close"], 101.0)

    def test_sum_option_oi_and_pcr(self) -> None:
        chain = [
            {"option_type": "CE", "oi": 100, "expiry": 1_810_000_000},
            {"option_type": "PE", "oi": 150, "expiry": 1_810_000_000},
            {"option_type": "CE", "oi": 50, "expiry": 1_810_000_000},
            {"symbol": "NSE:NIFTY50-INDEX"},
        ]
        call_oi, put_oi, expiry = _sum_option_oi(chain)
        self.assertEqual(call_oi, 150)
        self.assertEqual(put_oi, 150)
        self.assertEqual(expiry, 1_810_000_000)

        pcr = _parse_option_chain_pcr(
            {"data": {"optionsChain": chain}},
            symbol="NSE:NIFTY50-INDEX",
        )
        self.assertEqual(pcr.pcr, 1.0)
        self.assertEqual(pcr.call_oi, 150)
        self.assertEqual(pcr.put_oi, 150)

    def test_parse_breadth_from_quotes(self) -> None:
        response = {
            "s": "ok",
            "d": [
                {"v": {"lp": 101.0, "prev_close_price": 100.0}},
                {"v": {"lp": 99.0, "prev_close_price": 100.0}},
                {"v": {"lp": 100.0, "prev_close_price": 100.0}},
            ],
        }
        breadth = _parse_breadth_from_quotes(response)
        self.assertEqual(breadth.advancers, 1)
        self.assertEqual(breadth.decliners, 1)
        self.assertEqual(breadth.unchanged, 1)


class FyersPositionInferenceTests(unittest.TestCase):
    def test_get_positions_infers_iron_condor_from_4_untagged_legs(self) -> None:
        response = {
            "s": "ok",
            "netPositions": [
                {
                    "symbol": "NSE:NIFTY24JUN24000PE",
                    "netQty": 50,
                    "avgPrice": 80.0,
                    "option_type": "PE",
                    "strike_price": 24000,
                    "expiry": "24JUN24",
                },
                {
                    "symbol": "NSE:NIFTY24JUN24100PE",
                    "netQty": -50,
                    "avgPrice": 120.0,
                    "option_type": "PE",
                    "strike_price": 24100,
                    "expiry": "24JUN24",
                },
                {
                    "symbol": "NSE:NIFTY24JUN25000CE",
                    "netQty": -50,
                    "avgPrice": 90.0,
                    "option_type": "CE",
                    "strike_price": 25000,
                    "expiry": "24JUN24",
                },
                {
                    "symbol": "NSE:NIFTY24JUN25100CE",
                    "netQty": 50,
                    "avgPrice": 60.0,
                    "option_type": "CE",
                    "strike_price": 25100,
                    "expiry": "24JUN24",
                },
            ],
        }
        positions = _parse_positions(response)
        self.assertEqual(len(positions), 4)
        self.assertTrue(all(pos.strategy == "iron_condor" for pos in positions))
        self.assertTrue(all(pos.strategy_id == "iron_condor" for pos in positions))
        self.assertEqual(len({pos.strategy_id for pos in positions}), 1)

    def test_get_positions_raises_for_2_untagged_strangle_legs(self) -> None:
        response = {
            "s": "ok",
            "netPositions": [
                {
                    "symbol": "NSE:NIFTY24JUN25000CE",
                    "netQty": -50,
                    "avgPrice": 90.0,
                    "option_type": "CE",
                    "strike_price": 25000,
                    "expiry": "24JUN24",
                },
                {
                    "symbol": "NSE:NIFTY24JUN24000PE",
                    "netQty": -50,
                    "avgPrice": 80.0,
                    "option_type": "PE",
                    "strike_price": 24000,
                    "expiry": "24JUN24",
                },
            ],
        }
        with self.assertRaises(UntaggedPositionError):
            _parse_positions(response)

    def test_get_positions_raises_untagged_for_3_legs(self) -> None:
        response = {
            "s": "ok",
            "netPositions": [
                {
                    "symbol": "NSE:NIFTY24JUN25000CE",
                    "netQty": -50,
                    "avgPrice": 90.0,
                    "option_type": "CE",
                    "strike_price": 25000,
                    "expiry": "24JUN24",
                },
                {
                    "symbol": "NSE:NIFTY24JUN25100CE",
                    "netQty": 50,
                    "avgPrice": 60.0,
                    "option_type": "CE",
                    "strike_price": 25100,
                    "expiry": "24JUN24",
                },
                {
                    "symbol": "NSE:NIFTY24JUN24000PE",
                    "netQty": -50,
                    "avgPrice": 80.0,
                    "option_type": "PE",
                    "strike_price": 24000,
                    "expiry": "24JUN24",
                },
            ],
        }
        with self.assertRaises(UntaggedPositionError):
            _parse_positions(response)

    def test_get_positions_raises_untagged_for_mixed_underlyings(self) -> None:
        response = {
            "s": "ok",
            "netPositions": [
                {
                    "symbol": "NSE:NIFTY24JUN25000CE",
                    "netQty": -50,
                    "avgPrice": 90.0,
                    "option_type": "CE",
                    "strike_price": 25000,
                    "expiry": "24JUN24",
                },
                {
                    "symbol": "NSE:BANKNIFTY24JUN45000CE",
                    "netQty": -25,
                    "avgPrice": 120.0,
                    "option_type": "CE",
                    "strike_price": 45000,
                    "expiry": "24JUN24",
                },
            ],
        }
        with self.assertRaises(UntaggedPositionError):
            _parse_positions(response)


class FyersOptionChainGreeksTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertIsNotNone(greeks[0].delta)
        self.assertIsNotNone(greeks[0].gamma)


if __name__ == "__main__":
    unittest.main()
