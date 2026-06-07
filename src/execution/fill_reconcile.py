"""Post-submit fill reconciliation against broker orderbook (Phase 4.2)."""

from __future__ import annotations

from src.execution.port import ExecutionFailedError, ExecutionPort, LegActionIntent


async def verify_entry_fills(
    port: ExecutionPort,
    intents: list[LegActionIntent],
) -> None:
    """
    Confirm every submitted leg tag appears in the broker orderbook.

    Raises ExecutionFailedError when any leg is missing (partial submit / timeout).
    """
    if not intents:
        return

    book = await port.get_orderbook()
    known_tags = {row.tag for row in book if row.tag}
    missing = [intent for intent in intents if intent.tag not in known_tags]
    if missing:
        symbols = ", ".join(intent.symbol for intent in missing)
        raise ExecutionFailedError(
            f"Fill reconcile failed: {len(missing)} leg(s) missing from orderbook ({symbols})"
        )
