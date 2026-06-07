"""In-memory execution port for tests."""

from __future__ import annotations

import time
from datetime import datetime

from src.execution.port import (
    CancelAck,
    ExecutionPort,
    LegActionIntent,
    OrderAck,
    OrderRow,
    PortHealth,
)
from src.orchestration.session_clock import IST


class MockExecutionPort(ExecutionPort):
    """Records submits; supports simulated failures and duplicate-tag detection."""

    def __init__(self) -> None:
        self.calls: list[LegActionIntent] = []
        self._submit_count = 0
        self._failure_at: int | None = None
        self._seen_tags: set[str] = set()
        self._order_seq = 0
        self._health_sleep_sec = 0.0

    def configure_failure_at(self, n: int) -> None:
        """On the Nth submit_legs call, return REJECTED with SIMULATED_BROKER_ERROR."""
        self._failure_at = n

    def configure_health_sleep(self, seconds: float) -> None:
        """Inject latency into health_check (for latency tests)."""
        self._health_sleep_sec = seconds

    async def _submit_legs_impl(self, intent: LegActionIntent) -> OrderAck:
        self._submit_count += 1
        if intent.tag in self._seen_tags:
            return OrderAck(
                leg_id=intent.leg_id,
                order_id=None,
                status="DEFERRED",
                reason="DUPLICATE_TAG",
                submitted_at=datetime.now(IST),
            )

        if self._failure_at is not None and self._submit_count == self._failure_at:
            return OrderAck(
                leg_id=intent.leg_id,
                order_id=None,
                status="REJECTED",
                reason="SIMULATED_BROKER_ERROR",
                submitted_at=datetime.now(IST),
            )

        self._seen_tags.add(intent.tag)
        self.calls.append(intent)
        self._order_seq += 1
        return OrderAck(
            leg_id=intent.leg_id,
            order_id=f"MOCK-{self._order_seq:04d}",
            status="ACCEPTED",
            reason=None,
            submitted_at=datetime.now(IST),
        )

    async def cancel_order(self, order_id: str) -> CancelAck:
        return CancelAck(order_id=order_id, status="ACCEPTED")

    async def get_orderbook(self) -> list[OrderRow]:
        rows: list[OrderRow] = []
        for idx, intent in enumerate(self.calls, start=1):
            rows.append(
                OrderRow(
                    order_id=f"MOCK-{idx:04d}",
                    leg_id=intent.leg_id,
                    symbol=intent.symbol,
                    side=intent.side,
                    qty=intent.qty,
                    tag=intent.tag,
                    status="ACCEPTED",
                )
            )
        return rows

    async def health_check(self) -> PortHealth:
        start = time.perf_counter()
        if self._health_sleep_sec > 0:
            time.sleep(self._health_sleep_sec)
        latency_ms = max(1, int((time.perf_counter() - start) * 1000))
        return PortHealth(ok=True, latency_ms=latency_ms, last_error=None)
