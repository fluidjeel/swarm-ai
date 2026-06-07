"""Resolve broker symbols for Agent 3 quote/greeks fetch (v4.1 simplified)."""

from __future__ import annotations

from src.core.context import AgentContext

NIFTY_INDEX_SYMBOL = "NSE:NIFTY50-INDEX"


def quote_symbol_for_strategy(ctx: AgentContext) -> str:
    """Return the index symbol used for underlying LTP and option chain refresh."""
    _ = ctx.strategy_decision
    return NIFTY_INDEX_SYMBOL


def expiry_ts_for_context(ctx: AgentContext) -> int:
    """Return option-chain expiry timestamp; 0 requests current expiry from broker."""
    _ = ctx.dte
    return 0
