"""Chaos Monkey: fault injection against FyersExecutionPort idempotency.

These tests deliberately inject the failure modes that break naive execution
ports — gateway timeouts that may or may not have actually placed the order,
duplicate order tags, and partial multi-leg fills — and assert the port never
double-submits and always fails closed.
"""

from __future__ import annotations

import unittest
from typing import Any, Callable

from src.core.context import OpenPosition
from src.execution.fyers_port import FyersExecutionPort
from src.execution.port import ExecutionFailedError, LegActionIntent

PlaceEffect = Callable[["_FakeFyersClient", dict[str, Any]], dict[str, Any]]


def _ok(order_id: str) -> PlaceEffect:
    def _effect(_client: "_FakeFyersClient", _payload: dict[str, Any]) -> dict[str, Any]:
        return {"s": "ok", "id": order_id}

    return _effect


def _err_504(_client: "_FakeFyersClient", _payload: dict[str, Any]) -> dict[str, Any]:
    """Gateway timeout, order NOT placed at the broker."""
    return {"s": "error", "code": 504, "message": "Gateway timeout"}


def _err_504_but_placed(order_id: str) -> PlaceEffect:
    """The hazardous case: broker placed the order but returned a 504."""

    def _effect(client: "_FakeFyersClient", payload: dict[str, Any]) -> dict[str, Any]:
        client.add_order(order_id, payload)
        return {"s": "error", "code": 504, "message": "Gateway timeout"}

    return _effect


def _reject_margin(_client: "_FakeFyersClient", _payload: dict[str, Any]) -> dict[str, Any]:
    return {"s": "error", "code": -99, "message": "insufficient margin"}


class _FakeFyersClient:
    def __init__(
        self,
        *,
        place_effects: list[PlaceEffect] | None = None,
        orderbook_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._place_effects = list(place_effects or [])
        self._orderbook_rows = list(orderbook_rows or [])
        self.place_calls = 0
        self.orderbook_calls = 0

    def add_order(self, order_id: str, payload: dict[str, Any]) -> None:
        self._orderbook_rows.append(
            {
                "id": order_id,
                "symbol": payload["symbol"],
                "side": payload["side"],
                "qty": payload["qty"],
                "orderTag": payload["orderTag"],
                "status": 2,
            }
        )

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.place_calls += 1
        if self._place_effects:
            effect = self._place_effects.pop(0)
            return effect(self, payload)
        return {"s": "ok", "id": f"AUTO{self.place_calls}"}

    def orderbook(self) -> dict[str, Any]:
        self.orderbook_calls += 1
        return {"s": "ok", "orderBook": list(self._orderbook_rows)}


def _port(client: _FakeFyersClient) -> FyersExecutionPort:
    port = FyersExecutionPort(app_id="dummy", access_token="dummy")
    port._client = client
    return port


def _intent(tag: str = "TAG123", symbol: str = "NSE:NIFTY24JUN24000CE") -> LegActionIntent:
    return LegActionIntent(leg_id="L1", symbol=symbol, side="BUY", qty=50, tag=tag)


def _existing_row(tag: str, symbol: str, order_id: str = "PRE1") -> dict[str, Any]:
    return {
        "id": order_id,
        "symbol": symbol,
        "side": 1,
        "qty": 50,
        "orderTag": tag,
        "status": 2,
    }


class ChaosIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_tag_is_idempotent_skip(self) -> None:
        intent = _intent()
        client = _FakeFyersClient(orderbook_rows=[_existing_row(intent.tag, intent.symbol)])
        ack = await _port(client)._submit_legs_impl(intent)
        self.assertEqual(ack.status, "DEFERRED")
        self.assertEqual(ack.reason, "DUPLICATE_TAG")
        self.assertEqual(client.place_calls, 0)  # never double-submitted

    async def test_504_but_order_placed_recovers_without_duplicate(self) -> None:
        intent = _intent()
        client = _FakeFyersClient(place_effects=[_err_504_but_placed("ORD1")])
        ack = await _port(client)._submit_legs_impl(intent)
        self.assertEqual(ack.status, "ACCEPTED")
        self.assertEqual(ack.reason, "RECOVERED_AFTER_TRANSIENT")
        self.assertEqual(ack.order_id, "ORD1")
        self.assertEqual(client.place_calls, 1)  # no second submission

    async def test_504_not_placed_then_retry_succeeds(self) -> None:
        intent = _intent()
        client = _FakeFyersClient(place_effects=[_err_504, _ok("ORD2")])
        ack = await _port(client)._submit_legs_impl(intent)
        self.assertEqual(ack.status, "ACCEPTED")
        self.assertEqual(ack.order_id, "ORD2")
        self.assertEqual(client.place_calls, 2)

    async def test_504_retry_also_places_recovers_after_retry(self) -> None:
        intent = _intent()
        client = _FakeFyersClient(place_effects=[_err_504, _err_504_but_placed("ORD3")])
        ack = await _port(client)._submit_legs_impl(intent)
        self.assertEqual(ack.status, "ACCEPTED")
        self.assertEqual(ack.reason, "RECOVERED_AFTER_RETRY")
        self.assertEqual(ack.order_id, "ORD3")

    async def test_persistent_504_fails_closed(self) -> None:
        intent = _intent()
        client = _FakeFyersClient(place_effects=[_err_504, _err_504])
        ack = await _port(client)._submit_legs_impl(intent)
        self.assertEqual(ack.status, "REJECTED")
        self.assertIn("504", ack.reason or "")

    async def test_non_transient_rejection_is_not_retried(self) -> None:
        intent = _intent()
        client = _FakeFyersClient(place_effects=[_reject_margin])
        ack = await _port(client)._submit_legs_impl(intent)
        self.assertEqual(ack.status, "REJECTED")
        self.assertEqual(client.place_calls, 1)  # no retry on a hard reject

    async def test_submit_legs_wrapper_escalates_rejection(self) -> None:
        intent = _intent()
        client = _FakeFyersClient(place_effects=[_reject_margin])
        with self.assertRaises(ExecutionFailedError):
            await _port(client).submit_legs(intent)


def _iron_condor_position() -> OpenPosition:
    symbols = [
        "NSE:NIFTY24JUN24000PE",
        "NSE:NIFTY24JUN24100PE",
        "NSE:NIFTY24JUN25000CE",
        "NSE:NIFTY24JUN24900CE",
    ]
    legs = [
        OpenPosition(
            symbol=sym,
            strategy="iron_condor",
            lots=1,
            entry_price=80.0,
            leg_id=f"L{i}",
            strategy_id="ic1",
        )
        for i, sym in enumerate(symbols)
    ]
    return OpenPosition(
        symbol="ic1_summary",
        strategy="iron_condor",
        lots=1,
        entry_price=80.0,
        strategy_id="ic1",
        legs=legs,
    )


class ChaosFlattenTests(unittest.IsolatedAsyncioTestCase):
    async def test_flatten_all_legs_ok(self) -> None:
        client = _FakeFyersClient(
            place_effects=[_ok("F1"), _ok("F2"), _ok("F3"), _ok("F4")]
        )
        await _port(client)._flatten_position_impl(_iron_condor_position())
        self.assertEqual(client.place_calls, 4)

    async def test_partial_fill_on_flatten_fails_closed(self) -> None:
        # Two legs flatten, the third hard-rejects -> the whole flatten must raise.
        client = _FakeFyersClient(
            place_effects=[_ok("F1"), _ok("F2"), _reject_margin]
        )
        with self.assertRaises(ExecutionFailedError):
            await _port(client)._flatten_position_impl(_iron_condor_position())

    async def test_flatten_recovers_leg_after_transient_504(self) -> None:
        # First leg 504s but was actually placed -> recovered, no duplicate;
        # remaining legs flatten normally.
        client = _FakeFyersClient(
            place_effects=[
                _err_504_but_placed("F1"),
                _ok("F2"),
                _ok("F3"),
                _ok("F4"),
            ]
        )
        await _port(client)._flatten_position_impl(_iron_condor_position())
        self.assertEqual(client.place_calls, 4)


if __name__ == "__main__":
    unittest.main()
