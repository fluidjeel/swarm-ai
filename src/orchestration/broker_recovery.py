"""Broker state recovery on EC2 boot — Fyers GET /positions is source of truth."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Protocol

from src.core.context import AgentContext, OpenPosition
from src.core.strategy_registry import expected_leg_count
from src.data.base_provider import MarketDataError, MarketDataProvider

logger = logging.getLogger("a2a.broker_recovery")


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
