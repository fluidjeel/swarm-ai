"""Canonical strategy leg counts shared across recovery and risk modules."""

from __future__ import annotations

from src.core.context import StrategyName

LEG_COUNTS: dict[StrategyName, int] = {
    StrategyName.IRON_CONDOR: 4,
    StrategyName.BULL_CALL_SPREAD: 2,
    StrategyName.BEAR_PUT_SPREAD: 2,
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
