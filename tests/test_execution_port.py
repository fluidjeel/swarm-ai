"""Tests for ExecutionPort contract and MockExecutionPort."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from src.execution.mock_port import MockExecutionPort
from src.execution.port import LegActionIntent, idem_key
from src.orchestration.session_clock import IST


def _intent(*, tag: str = "abc123") -> LegActionIntent:
    return LegActionIntent(
        leg_id="leg-1",
        symbol="NSE:NIFTY26JUN25000CE",
        side="BUY",
        qty=50,
        tag=tag,
    )


class ExecutionPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_legs_idempotent(self) -> None:
        port = MockExecutionPort()
        intent = _intent(tag="idem-001")

        first = await port.submit_legs(intent)
        second = await port.submit_legs(intent)

        self.assertEqual(first.status, "ACCEPTED")
        self.assertEqual(second.status, "DEFERRED")
        self.assertEqual(second.reason, "DUPLICATE_TAG")
        self.assertEqual(len(port.calls), 1)

    async def test_submit_legs_fail_closed(self) -> None:
        port = MockExecutionPort()
        port.configure_failure_at(2)

        first = await port.submit_legs(_intent(tag="fail-1"))
        second = await port.submit_legs(_intent(tag="fail-2"))

        self.assertEqual(first.status, "ACCEPTED")
        self.assertEqual(second.status, "REJECTED")
        self.assertEqual(second.reason, "SIMULATED_BROKER_ERROR")

    async def test_health_check_reports_latency(self) -> None:
        port = MockExecutionPort()
        port.configure_health_sleep(0.02)

        with patch("src.execution.mock_port.time.sleep") as mock_sleep:
            mock_sleep.side_effect = lambda _s: None
            health = await port.health_check()

        self.assertTrue(health.ok)
        self.assertGreater(health.latency_ms, 0)

    def test_idem_key_is_deterministic(self) -> None:
        tick_ts = datetime.now(IST).isoformat()
        a = idem_key(
            tick_timestamp=tick_ts,
            leg_id="leg-a",
            symbol="NSE:NIFTY26JUN25000CE",
            side="BUY",
        )
        b = idem_key(
            tick_timestamp=tick_ts,
            leg_id="leg-a",
            symbol="NSE:NIFTY26JUN25000CE",
            side="BUY",
        )
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)


if __name__ == "__main__":
    unittest.main()
