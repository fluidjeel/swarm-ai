"""Broker state recovery on EC2 boot — Fyers GET /positions is source of truth."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from src.core.context import AgentContext, OpenPosition
from src.core.strategy_registry import expected_leg_count
from src.data.base_provider import (
    BrokerOrder,
    BrokerTrade,
    FundSnapshot,
    MarketDataError,
    MarketDataProvider,
)

logger = logging.getLogger("a2a.broker_recovery")

# Broker margin above this (INR) is treated as "capital is blocked". If the
# reconciler also believes we are flat, that contradiction forces a halt.
FUNDS_MARGIN_TOLERANCE_INR = 1.0


class OrphanLegError(Exception):
    """Raised when a multi-leg strategy has fewer legs than expected at the broker."""


class PartialFillError(Exception):
    """Raised when a multi-leg strategy has a partial but incomplete leg set."""


class BootLogger(Protocol):
    def log_boot_row(self, row: dict[str, Any]) -> None: ...


class _JsonBootLogger:
    def log_boot_row(self, row: dict[str, Any]) -> None:
        logger.info(json.dumps(row, default=str))


_DEFAULT_BOOT_LOGGER = _JsonBootLogger()


def _group_key(position: OpenPosition) -> str:
    """Group multi-leg clusters by strategy_id; single-leg rows by symbol."""
    expected = expected_leg_count(position.strategy)
    if expected > 1:
        return position.strategy_id or position.strategy
    return position.leg_id or position.symbol


def _build_summary(group: list[OpenPosition]) -> OpenPosition:
    strategy = group[0].strategy
    strategy_id = group[0].strategy_id or strategy
    legs = [leg.model_copy(update={"legs": None}) for leg in group]
    entry_price = sum(leg.entry_price for leg in group) / len(group)
    return OpenPosition(
        symbol=f"{strategy_id}_summary",
        strategy=strategy,
        lots=group[0].lots,
        entry_price=entry_price,
        strategy_id=strategy_id,
        leg_id=None,
        legs=legs,
    )


def _log_boot(
    *,
    boot_logger: BootLogger,
    session_id: str,
    outcome: str,
    position_count: int,
    open_position: OpenPosition | None,
    detail: str | None = None,
) -> None:
    boot_logger.log_boot_row(
        {
            "event": "broker_recovery",
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "outcome": outcome,
            "position_count": position_count,
            "open_position_symbol": open_position.symbol if open_position else None,
            "open_position_strategy": open_position.strategy if open_position else None,
            "leg_count": len(open_position.legs) if open_position and open_position.legs else None,
            "detail": detail,
        }
    )


def _resolve_position_group(
    positions: list[OpenPosition],
    *,
    session_id: str,
    boot_logger: BootLogger,
) -> OpenPosition:
    groups: dict[str, list[OpenPosition]] = defaultdict(list)
    for position in positions:
        groups[_group_key(position)].append(position)

    if len(groups) > 1:
        selected_key = max(groups, key=lambda key: len(groups[key]))
        dropped = {key: value for key, value in groups.items() if key != selected_key}
        detail = (
            f"multi_group_alert: kept={selected_key} ({len(groups[selected_key])} legs), "
            f"dropped={{{', '.join(f'{k}:{len(v)}' for k, v in dropped.items())}}}"
        )
        logger.warning(detail)
        _log_boot(
            boot_logger=boot_logger,
            session_id=session_id,
            outcome="multi_group_kept_largest",
            position_count=len(positions),
            open_position=groups[selected_key][0],
            detail=detail,
        )
        group = groups[selected_key]
    else:
        group = next(iter(groups.values()))

    strategy = group[0].strategy
    expected = expected_leg_count(strategy)
    actual = len(group)

    if expected == 1:
        if actual == 1:
            return group[0]
        raise PartialFillError(
            f"Single-leg strategy {strategy} has {actual} independent positions at broker."
        )

    if actual == 1:
        _log_boot(
            boot_logger=boot_logger,
            session_id=session_id,
            outcome="orphan_leg_detected",
            position_count=1,
            open_position=group[0],
            detail=f"expected {expected} legs for {strategy}, found 1",
        )
        raise OrphanLegError(
            f"Orphan leg: strategy {strategy} expects {expected} legs, "
            f"broker returned 1 ({group[0].symbol})."
        )

    if actual != expected:
        _log_boot(
            boot_logger=boot_logger,
            session_id=session_id,
            outcome="partial_fill_detected",
            position_count=actual,
            open_position=group[0],
            detail=f"expected {expected} legs for {strategy}, found {actual}",
        )
        raise PartialFillError(
            f"Partial fill: strategy {strategy} expects {expected} legs, broker returned {actual}."
        )

    return _build_summary(group)


async def rebuild_from_fyers(
    provider: MarketDataProvider,
    ctx: AgentContext,
    *,
    boot_logger: BootLogger | None = None,
) -> AgentContext:
    """
    Boot-time reconstruction from Fyers GET /positions.

    Distinguishes broker-down (MarketDataError) from zero positions (empty list).
    Never returns silently — every path logs a structured boot row.
    """
    writer = boot_logger or _DEFAULT_BOOT_LOGGER

    try:
        positions = await provider.get_positions()
    except MarketDataError as exc:
        _log_boot(
            boot_logger=writer,
            session_id=ctx.session_id,
            outcome="broker_error",
            position_count=-1,
            open_position=None,
            detail=str(exc),
        )
        raise

    count = len(positions)

    if count == 0:
        _log_boot(
            boot_logger=writer,
            session_id=ctx.session_id,
            outcome="no_positions",
            position_count=0,
            open_position=None,
        )
        return ctx.update(open_position=None)

    try:
        open_position = _resolve_position_group(
            positions,
            session_id=ctx.session_id,
            boot_logger=writer,
        )
    except (OrphanLegError, PartialFillError):
        raise

    _log_boot(
        boot_logger=writer,
        session_id=ctx.session_id,
        outcome="position_recovered",
        position_count=count,
        open_position=open_position,
    )
    return ctx.update(open_position=open_position)


async def sync_position_from_broker(
    provider: MarketDataProvider,
    ctx: AgentContext,
) -> AgentContext:
    """
    Per-tick reconcile in-memory open_position against Fyers GET /positions.

    Fail-soft on broker errors (keeps existing ctx). Fail-closed on orphan/partial
    multi-leg sets (sets execution_halted).
    """
    try:
        positions = await provider.get_positions()
    except MarketDataError:
        return ctx

    if not positions:
        if ctx.open_position is None:
            return ctx
        return ctx.update(open_position=None)

    try:
        open_position = _resolve_position_group(
            positions,
            session_id=ctx.session_id,
            boot_logger=_DEFAULT_BOOT_LOGGER,
        )
    except (OrphanLegError, PartialFillError):
        return ctx.update(execution_halted=True)

    if ctx.open_position == open_position:
        return ctx
    return ctx.update(open_position=open_position)


# --------------------------------------------------------------------------
# 4-way state reconciliation: positions + orders + trades + funds.
# Positions are mandatory; orders/trades/funds are optional dimensions that are
# skipped if the provider does not expose them. ANY detected contradiction
# between broker reality and in-memory belief forces reconciliation_halt — a
# hard, human-gated stop (Prime Directive #1: broker is the source of truth).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconciliationReport:
    """Structured outcome of a 4-way broker-state reconciliation."""

    open_position: OpenPosition | None
    mismatches: list[str] = field(default_factory=list)
    orders_checked: bool = False
    trades_checked: bool = False
    funds_checked: bool = False
    working_order_count: int = 0
    trade_count: int = 0
    funds: FundSnapshot | None = None

    @property
    def ok(self) -> bool:
        return not self.mismatches


def _position_symbols(position: OpenPosition | None) -> set[str]:
    if position is None:
        return set()
    if position.legs:
        return {leg.symbol for leg in position.legs}
    return {position.symbol}


def _log_reconciliation(
    *,
    boot_logger: BootLogger,
    session_id: str,
    report: ReconciliationReport,
) -> None:
    boot_logger.log_boot_row(
        {
            "event": "broker_reconciliation",
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "outcome": "ok" if report.ok else "reconciliation_halt",
            "mismatches": report.mismatches,
            "orders_checked": report.orders_checked,
            "trades_checked": report.trades_checked,
            "funds_checked": report.funds_checked,
            "working_order_count": report.working_order_count,
            "trade_count": report.trade_count,
            "open_position_symbol": (
                report.open_position.symbol if report.open_position else None
            ),
        }
    )


async def reconcile_broker_state(
    provider: MarketDataProvider,
    ctx: AgentContext,
    *,
    boot_logger: BootLogger | None = None,
) -> tuple[AgentContext, ReconciliationReport]:
    """Reconcile broker positions, orders, trades, and funds against context.

    Returns the (possibly updated) context and a structured report. On any
    mismatch the context is returned with ``reconciliation_halt=True``. Fails
    closed: a broker/transport error on the mandatory positions dimension also
    halts. Optional dimensions that raise ``NotImplementedError`` are skipped.
    """
    writer = boot_logger or _DEFAULT_BOOT_LOGGER
    mismatches: list[str] = []

    # 1) Positions (mandatory; source of truth).
    open_position: OpenPosition | None = None
    try:
        positions = await provider.get_positions()
    except MarketDataError as exc:
        mismatches.append(f"positions_query_failed:{exc}")
        report = ReconciliationReport(open_position=None, mismatches=mismatches)
        _log_reconciliation(boot_logger=writer, session_id=ctx.session_id, report=report)
        return ctx.update(reconciliation_halt=True), report

    if positions:
        try:
            open_position = _resolve_position_group(
                positions,
                session_id=ctx.session_id,
                boot_logger=writer,
            )
        except (OrphanLegError, PartialFillError) as exc:
            mismatches.append(f"position_leg_mismatch:{exc}")

    position_symbols = _position_symbols(open_position)
    flat = open_position is None

    # 2) Orders (optional): any working order is in flight; if it does not
    #    belong to the reconciled position it is dangling -> mismatch.
    orders_checked = False
    working_order_count = 0
    try:
        orders = await provider.get_orders()
    except (NotImplementedError, AttributeError):
        orders = []
    except MarketDataError as exc:
        mismatches.append(f"orders_query_failed:{exc}")
        orders = []
    else:
        orders_checked = True
        working = [order for order in orders if order.is_working]
        working_order_count = len(working)
        for order in working:
            if order.symbol not in position_symbols:
                mismatches.append(
                    f"dangling_working_order:{order.symbol}:{order.status}"
                )

    # 3) Trades (optional): audit trail. Recorded; not an independent halt
    #    trigger (positions + funds already cover the capital-risk cases).
    trades_checked = False
    trade_count = 0
    try:
        trades = await provider.get_trades()
    except (NotImplementedError, AttributeError):
        trades = []
    except MarketDataError as exc:
        mismatches.append(f"trades_query_failed:{exc}")
        trades = []
    else:
        trades_checked = True
        trade_count = len(trades)

    # 4) Funds (optional): margin blocked while we believe we are flat is a
    #    contradiction; negative balance is always a hard stop.
    funds_checked = False
    funds: FundSnapshot | None = None
    try:
        funds = await provider.get_funds()
    except (NotImplementedError, AttributeError):
        funds = None
    except MarketDataError as exc:
        mismatches.append(f"funds_query_failed:{exc}")
    else:
        funds_checked = True
        if funds.available_balance < 0:
            mismatches.append(f"negative_balance:{funds.available_balance}")
        if flat and funds.utilized_margin > FUNDS_MARGIN_TOLERANCE_INR:
            mismatches.append(
                f"margin_without_position:{funds.utilized_margin}"
            )

    report = ReconciliationReport(
        open_position=open_position,
        mismatches=mismatches,
        orders_checked=orders_checked,
        trades_checked=trades_checked,
        funds_checked=funds_checked,
        working_order_count=working_order_count,
        trade_count=trade_count,
        funds=funds,
    )
    _log_reconciliation(boot_logger=writer, session_id=ctx.session_id, report=report)

    if not report.ok:
        return ctx.update(reconciliation_halt=True), report

    if ctx.open_position == open_position:
        return ctx, report
    return ctx.update(open_position=open_position), report
