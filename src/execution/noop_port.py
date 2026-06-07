"""No-op execution port for dry-run / unwired pipelines."""

from __future__ import annotations

import logging
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

logger = logging.getLogger("a2a.execution.noop")


class NoOpExecutionPort(ExecutionPort):
    """Logs EXEC_SKIPPED and returns DEFERRED acks — no upstream broker I/O."""

    async def _submit_legs_impl(self, intent: LegActionIntent) -> OrderAck:
        logger.info("EXEC_SKIPPED — no port wired: %s %s", intent.side, intent.symbol)
        return OrderAck(
            leg_id=intent.leg_id,
            order_id=None,
            status="DEFERRED",
            reason="EXEC_SKIPPED — no port wired",
            submitted_at=datetime.now(IST),
        )

    async def cancel_order(self, order_id: str) -> CancelAck:
        logger.info("EXEC_SKIPPED — cancel_order: %s", order_id)
        return CancelAck(order_id=order_id, status="ACCEPTED")

    async def get_orderbook(self) -> list[OrderRow]:
        return []

    async def health_check(self) -> PortHealth:
        return PortHealth(ok=True, latency_ms=0, last_error=None)
