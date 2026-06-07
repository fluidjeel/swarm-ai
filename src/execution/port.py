"""Execution port contract — broker I/O boundary (Phase 4.1)."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.orchestration.session_clock import IST

OrderStatus = Literal["ACCEPTED", "REJECTED", "DEFERRED"]
CancelStatus = Literal["ACCEPTED", "REJECTED"]
OrderSide = Literal["BUY", "SELL"]


@dataclass(frozen=True, slots=True)
class LegActionIntent:
    """Single-leg submit intent for the execution port."""

    leg_id: str
    symbol: str
    side: OrderSide
    qty: int
    tag: str


@dataclass(frozen=True, slots=True)
class OrderAck:
    leg_id: str
    order_id: str | None
    status: OrderStatus
    reason: str | None
    submitted_at: datetime


@dataclass(frozen=True, slots=True)
class CancelAck:
    order_id: str
    status: CancelStatus
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class OrderRow:
    order_id: str
    leg_id: str
    symbol: str
    side: OrderSide
    qty: int
    tag: str
    status: str


@dataclass(frozen=True, slots=True)
class PortHealth:
    ok: bool
    latency_ms: int
    last_error: str | None = None


def idem_key(
    *,
    tick_timestamp: str,
    leg_id: str,
    symbol: str,
    side: str,
) -> str:
    """sha256(tick_timestamp + leg_id + symbol + side), truncated to 16 hex chars."""
    payload = f"{tick_timestamp}{leg_id}{symbol}{side}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class ExecutionPort(ABC):
    """Async broker execution boundary. Implementations must not raise from public methods."""

    async def submit_legs(self, intent: LegActionIntent) -> OrderAck:
        try:
            return await self._submit_legs_impl(intent)
        except Exception as exc:
            return OrderAck(
                leg_id=intent.leg_id,
                order_id=None,
                status="REJECTED",
                reason=str(exc),
                submitted_at=datetime.now(IST),
            )

    @abstractmethod
    async def _submit_legs_impl(self, intent: LegActionIntent) -> OrderAck:
        """Subclass hook; may raise — wrapped fail-closed by submit_legs()."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> CancelAck:
        """Cancel a single broker order."""

    @abstractmethod
    async def get_orderbook(self) -> list[OrderRow]:
        """Return current broker orderbook snapshot."""

    @abstractmethod
    async def health_check(self) -> PortHealth:
        """Probe broker connectivity."""
