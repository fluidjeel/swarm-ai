"""Tests for FyersExecutionPort (mocked SDK)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.execution.fyers_port import FyersExecutionPort, _parse_orderbook_rows
from src.execution.port import LegActionIntent


def _intent(*, tag: str = "idem-tag-001") -> LegActionIntent:
    return LegActionIntent(
        leg_id="NSE:NIFTY26JUN25000CE",
        symbol="NSE:NIFTY26JUN25000CE",
        side="BUY",
        qty=50,
        tag=tag,
    )


class ParseOrderbookTests(unittest.TestCase):
    def test_parses_order_tag_field(self) -> None:
        rows = _parse_orderbook_rows(
            {
                "s": "ok",
                "orderBook": [
                    {
                        "id": "12345",
                        "symbol": "NSE:NIFTY26JUN25000CE",
                        "side": 1,
                        "qty": 50,
                        "orderTag": "idem-tag-001",
                        "status": 2,
                    }
                ],
            }
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].tag, "idem-tag-001")
        self.assertEqual(rows[0].order_id, "12345")


class FyersExecutionPortTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.port = FyersExecutionPort(app_id="TEST-APP", access_token="TEST-TOKEN")

    async def test_submit_skips_duplicate_tag(self) -> None:
        client = MagicMock()
        client.orderbook.return_value = {
            "s": "ok",
            "orderBook": [
                {
                    "id": "999",
                    "symbol": "NSE:NIFTY26JUN25000CE",
                    "side": 1,
                    "qty": 50,
                    "orderTag": "dup-tag",
                    "status": 2,
                }
            ],
        }
        self.port._client = client

        ack = await self.port.submit_legs(_intent(tag="dup-tag"))
        self.assertEqual(ack.status, "DEFERRED")
        self.assertEqual(ack.reason, "DUPLICATE_TAG")
        client.place_order.assert_not_called()

    async def test_submit_places_order_when_tag_absent(self) -> None:
        client = MagicMock()
        client.orderbook.return_value = {"s": "ok", "orderBook": []}
        client.place_order.return_value = {"s": "ok", "id": "555"}
        self.port._client = client

        ack = await self.port.submit_legs(_intent(tag="fresh-tag"))
        self.assertEqual(ack.status, "ACCEPTED")
        self.assertEqual(ack.order_id, "555")
        client.place_order.assert_called_once()

    async def test_transient_error_recovers_from_orderbook(self) -> None:
        client = MagicMock()
        client.orderbook.side_effect = [
            {"s": "ok", "orderBook": []},
            {
                "s": "ok",
                "orderBook": [
                    {
                        "id": "777",
                        "symbol": "NSE:NIFTY26JUN25000CE",
                        "side": 1,
                        "qty": 50,
                        "orderTag": "retry-tag",
                        "status": 2,
                    }
                ],
            },
        ]
        client.place_order.return_value = {
            "s": "error",
            "code": 504,
            "message": "gateway timeout",
        }
        self.port._client = client

        ack = await self.port.submit_legs(_intent(tag="retry-tag"))
        self.assertEqual(ack.status, "ACCEPTED")
        self.assertEqual(ack.order_id, "777")
        self.assertEqual(ack.reason, "RECOVERED_AFTER_TRANSIENT")

    async def test_health_check_ok(self) -> None:
        client = MagicMock()
        client.orderbook.return_value = {"s": "ok", "orderBook": []}
        self.port._client = client

        health = await self.port.health_check()
        self.assertTrue(health.ok)


class BrokerSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_clears_position_when_broker_flat(self) -> None:
        from src.core.context import AgentContext, OpenPosition
        from src.orchestration.broker_recovery import sync_position_from_broker

        class _Provider:
            async def get_positions(self):
                return []

        ctx = AgentContext(
            session_id="sync-test-01",
            open_position=OpenPosition(
                symbol="iron_condor_summary",
                strategy="iron_condor",
                lots=1,
                entry_price=100.0,
            ),
        )
        updated = await sync_position_from_broker(_Provider(), ctx)
        self.assertIsNone(updated.open_position)

    async def test_sync_keeps_position_on_broker_error(self) -> None:
        from src.core.context import AgentContext, OpenPosition
        from src.data.base_provider import MarketDataError
        from src.orchestration.broker_recovery import sync_position_from_broker

        class _Provider:
            async def get_positions(self):
                raise MarketDataError("broker down")

        position = OpenPosition(
            symbol="iron_condor_summary",
            strategy="iron_condor",
            lots=1,
            entry_price=100.0,
        )
        ctx = AgentContext(session_id="sync-test-02", open_position=position)
        updated = await sync_position_from_broker(_Provider(), ctx)
        self.assertEqual(updated.open_position, position)


if __name__ == "__main__":
    unittest.main()
