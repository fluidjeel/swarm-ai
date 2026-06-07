"""Canonical strategy leg counts shared across recovery and risk modules."""

from __future__ import annotations

STRATEGY_LEG_COUNTS: dict[str, int] = {
    "iron_condor": 4,
    "short_strangle": 2,
    "short_straddle": 2,
    "nifty_futures_long": 1,
    "nifty_futures_short": 1,
}


def expected_leg_count(strategy: str) -> int:
    """Return expected broker leg count for a strategy; unknown strategies default to 1."""
    return STRATEGY_LEG_COUNTS.get(strategy.strip().lower(), 1)
