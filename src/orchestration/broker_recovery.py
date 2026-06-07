"""Broker state recovery on EC2 boot — Fyers GET /positions is source of truth."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Protocol

from src.core.context import AgentContext, OpenPosition
from src.data.base_provider import MarketDataError, MarketDataProvider

logger = logging.getLogger("a2a.broker_recovery")

EXPECTED_LEG_COUNT: dict[str, int] = {
    "iron_condor": 4,
    "short_strangle": 2,
    "short_straddle": 2,
    "nifty_futures_long": 1,
    "nifty_futures_short": 1,
}


class OrphanLegError(Exception):
    """Raised when a multi-leg strategy has fewer legs than expected at the broker."""


class BootLogger(Protocol):
    def log_boot_row(self, row: dict[str, Any]) -> None: ...


class _JsonBootLogger:
    def log_boot_row(self, row: dict[str, Any]) -> None:
        logger.info(json.dumps(row, default=str))


_DEFAULT_BOOT_LOGGER = _JsonBootLogger()


def _expected_legs(strategy: str) -> int:
    return EXPECTED_LEG_COUNT.get(strategy.strip().lower(), 1)


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
            "detail": detail,
        }
    )


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

    if count > 1:
        kept = positions[0]
        dropped = positions[1:]
        detail = (
            f"multi_position_alert: kept={kept.symbol}, "
            f"dropped={[p.symbol for p in dropped]}"
        )
        logger.warning(detail)
        _log_boot(
            boot_logger=writer,
            session_id=ctx.session_id,
            outcome="multi_position_kept_first",
            position_count=count,
            open_position=kept,
            detail=detail,
        )
        return ctx.update(open_position=kept)

    position = positions[0]
    expected = _expected_legs(position.strategy)
    if expected > 1:
        _log_boot(
            boot_logger=writer,
            session_id=ctx.session_id,
            outcome="orphan_leg_detected",
            position_count=1,
            open_position=position,
            detail=f"expected {expected} legs for {position.strategy}, found 1",
        )
        raise OrphanLegError(
            f"Orphan leg: strategy {position.strategy} expects {expected} legs, "
            f"broker returned 1 ({position.symbol})."
        )

    _log_boot(
        boot_logger=writer,
        session_id=ctx.session_id,
        outcome="position_recovered",
        position_count=1,
        open_position=position,
    )
    return ctx.update(open_position=position)
