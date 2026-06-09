"""Tests for index contract registry."""

from __future__ import annotations

import unittest

from src.config.index_contracts import resolve_index_contract


class IndexContractTests(unittest.TestCase):
    def test_resolve_shorthand_keys(self) -> None:
        nifty = resolve_index_contract("nifty")
        sensex = resolve_index_contract("sensex")
        self.assertEqual(nifty.symbol, "NSE:NIFTY50-INDEX")
        self.assertEqual(sensex.symbol, "BSE:SENSEX-INDEX")

    def test_resolve_full_symbol(self) -> None:
        contract = resolve_index_contract("NSE:NIFTYBANK-INDEX")
        self.assertEqual(contract.key, "banknifty")

    def test_unknown_contract_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_index_contract("midcpnifty")


if __name__ == "__main__":
    unittest.main()
