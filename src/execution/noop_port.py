"""No-op execution port for dry-run / unwired pipelines."""

from __future__ import annotations

import logging
from datetime import datetime

from src.core.context import OpenPosition
from src.data.base_provider import Quote
from src.execution.port import (
    CancelAck,
    ExecutionPort,
    LegActionIntent,
    OrderAck,
    OrderRow,
    PortHealth,
)
from src.orchestration.session_clock import IST
from src.risk.friction import (
    compute_entry_credit_inr,
    compute_exit_close_cost_inr,
    compute_gross_pnl_inr,
    compute_paper_exit_net_pnl,
    round_trip_friction,
)

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

    async def _flatten_position_impl(self, position: OpenPosition) -> None:
        logger.info("EXEC_SKIPPED — flatten_position: %s", position.symbol)

    async def cancel_order(self, order_id: str) -> CancelAck:
        logger.info("EXEC_SKIPPED — cancel_order: %s", order_id)
        return CancelAck(order_id=order_id, status="ACCEPTED")

    async def get_orderbook(self) -> list[OrderRow]:
        return []

    async def health_check(self) -> PortHealth:
        return PortHealth(ok=True, latency_ms=0, last_error=None)


def paper_exit_net_pnl(
    gross_pnl_inr: float,
    position: OpenPosition,
) -> tuple[float, float]:
    """
    Net paper PnL after per-leg round-trip friction.

    Returns (net_pnl_inr, friction_inr).
    """
    leg_count = len(position.legs) if position.legs else None
    return compute_paper_exit_net_pnl(
        gross_pnl_inr,
        strategy=position.strategy,
        leg_count=leg_count,
    )


def expected_friction_for_position(position: OpenPosition) -> float:
    leg_count = len(position.legs) if position.legs else None
    return round_trip_friction(position.strategy, leg_count=leg_count)


def compute_paper_mtm(
    position: OpenPosition,
    *,
    per_leg_quotes: dict[str, Quote],
    lot_size: int,
) -> dict[str, float]:
    """
    Mark-to-market PnL for an open paper position using live leg quotes.

    Returns entry_credit_inr, exit_cost_inr, gross_pnl_inr, friction_inr, net_pnl_inr.
    """
    leg_symbols = _position_leg_symbols(position)
    entry_credit_inr = position.entry_cash_flow_inr
    if entry_credit_inr is None:
        entry_credit_inr = compute_entry_credit_inr(
            position.strategy,
            leg_symbols=leg_symbols,
            per_leg_quotes=_entry_quotes_from_position(position),
            lot_size=lot_size,
            lots=position.lots,
        )
    exit_cost_inr = compute_exit_close_cost_inr(
        position.strategy,
        leg_symbols=leg_symbols,
        per_leg_quotes=per_leg_quotes,
        lot_size=lot_size,
        lots=position.lots,
    )
    gross_pnl_inr = compute_gross_pnl_inr(entry_credit_inr, exit_cost_inr)
    net_pnl_inr, friction_inr = paper_exit_net_pnl(gross_pnl_inr, position)
    return {
        "entry_credit_inr": entry_credit_inr,
        "exit_cost_inr": exit_cost_inr,
        "gross_pnl_inr": gross_pnl_inr,
        "friction_inr": friction_inr,
        "net_pnl_inr": net_pnl_inr,
    }


def _position_leg_symbols(position: OpenPosition) -> list[str]:
    if position.legs:
        return [leg.symbol for leg in position.legs]
    return [position.symbol]


def _entry_quotes_from_position(position: OpenPosition) -> dict[str, Quote]:
    legs = position.legs or [position]
    return {
        leg.symbol: Quote(
            symbol=leg.symbol,
            bid=leg.entry_price,
            ask=leg.entry_price,
            ltp=leg.entry_price,
            spread_pct=0.0,
        )
        for leg in legs
    }
