"""Execution port contract — broker I/O boundary (Phase 4.1)."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.core.context import OpenPosition
from src.orchestration.session_clock import IST

OrderStatus = Literal["ACCEPTED", "REJECTED", "DEFERRED"]


class ExecutionFailedError(RuntimeError):
    """Raised when broker leg submission or flatten hard-fails."""


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
    """Async broker execution boundary."""

    async def submit_legs(self, intent: LegActionIntent) -> OrderAck:
        try:
            ack = await self._submit_legs_impl(intent)
        except ExecutionFailedError:
            raise
        except Exception as exc:
            raise ExecutionFailedError(
                f"submit_legs failed for {intent.symbol}: {exc}"
            ) from exc
        if ack.status == "REJECTED":
            raise ExecutionFailedError(
                f"submit_legs rejected for {intent.symbol}: {ack.reason or 'unknown'}"
            )
        return ack

    @abstractmethod
    async def _submit_legs_impl(self, intent: LegActionIntent) -> OrderAck:
        """Subclass hook; return REJECTED ack or raise — submit_legs() escalates both."""

    async def flatten_position(self, position: OpenPosition) -> None:
        try:
            await self._flatten_position_impl(position)
        except ExecutionFailedError:
            raise
        except Exception as exc:
            raise ExecutionFailedError(
                f"flatten_position failed for {position.symbol}: {exc}"
            ) from exc

    @abstractmethod
    async def _flatten_position_impl(self, position: OpenPosition) -> None:
        """Close all legs at market; raise ExecutionFailedError on broker failure."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> CancelAck:
        """Cancel a single broker order."""

    @abstractmethod
    async def get_orderbook(self) -> list[OrderRow]:
        """Return current broker orderbook snapshot."""

    @abstractmethod
    async def health_check(self) -> PortHealth:
        """Probe broker connectivity."""
