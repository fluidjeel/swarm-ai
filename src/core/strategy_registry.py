"""Canonical strategy leg counts shared across recovery and risk modules."""

from __future__ import annotations

from src.core.context import StrategyName

LEG_COUNTS: dict[StrategyName, int] = {
    StrategyName.IRON_CONDOR: 4,
    # Other defined-risk spreads are 2 legs (bull_call_spread, bear_put_spread).
    # Vertical spread leg count is not used by the current ExitEngine multi-leg path
    # because they are entered as 2 separate OCO orders in Phase 4.
}
DEFAULT_LEG_COUNT = 1


def expected_leg_count(strategy: StrategyName | str) -> int:
    """Return expected broker leg count for a strategy; unknown strategies default to 1."""
    if isinstance(strategy, StrategyName):
        key = strategy
    else:
        try:
            key = StrategyName(strategy.strip().lower())
        except ValueError:
            return DEFAULT_LEG_COUNT
    return LEG_COUNTS.get(key, DEFAULT_LEG_COUNT)
