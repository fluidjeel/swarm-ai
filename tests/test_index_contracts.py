"""Tests for index contract registry."""

from __future__ import annotations

import unittest

from datetime import datetime

from src.config.index_contracts import (
    SOAK_INDEX_KEYS,
    resolve_index_contract,
    resolve_soak_index_keys,
    risk_config_for_contract,
)
from src.config.risk_config import RiskConfig
from src.orchestration.session_clock import IST


class IndexContractTests(unittest.TestCase):
    def test_resolve_soak_all_returns_three_indices(self) -> None:
        self.assertEqual(resolve_soak_index_keys("all"), SOAK_INDEX_KEYS)

    def test_resolve_soak_comma_list(self) -> None:
        self.assertEqual(
            resolve_soak_index_keys("nifty,sensex"),
            ("nifty", "sensex"),
        )

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

    def test_banknifty_risk_overrides_monthly_soak_profile(self) -> None:
        contract = resolve_index_contract("banknifty")
        risk = risk_config_for_contract(contract, RiskConfig())
        self.assertEqual(risk.min_dte_for_entry, 7)
        self.assertEqual(risk.max_dte_for_entry, 21)
        self.assertEqual(risk.wing_width_points, 500)
        self.assertEqual(risk.stale_quote_points, 50.0)

    def test_banknifty_monthly_expiry_in_soak_band(self) -> None:
        from src.agents.symbol_resolver import select_expiry
        from src.core.context import AgentContext

        june_ist = datetime(2026, 6, 9, 11, 0, tzinfo=IST)
        ctx = AgentContext(session_id="banknifty-exp-01", dte=0)
        risk = risk_config_for_contract(resolve_index_contract("banknifty"), RiskConfig())
        expiry_ts = select_expiry(
            ctx,
            risk,
            index_symbol="NSE:NIFTYBANK-INDEX",
            now=june_ist,
        )
        expiry_date = datetime.fromtimestamp(expiry_ts, tz=IST).date()
        self.assertEqual(expiry_date.weekday(), 1)
        self.assertGreaterEqual(expiry_date.day, 24)


if __name__ == "__main__":
    unittest.main()
