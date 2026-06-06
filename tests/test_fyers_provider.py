"""Unit tests for Fyers market data parsing helpers."""

from __future__ import annotations

import unittest

from src.data.fyers_provider import (
    _parse_history_candles,
    _parse_option_chain_pcr,
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


if __name__ == "__main__":
    unittest.main()
