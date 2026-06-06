"""Deterministic risk controls for the A2A trading engine."""

from src.risk.exit_engine import (
    CreditSpreadPosition,
    ExitAction,
    ExitDecision,
    ExitEngine,
    FuturesPosition,
)
from src.risk.gatekeeper import (
    GatekeeperDecision,
    GatekeeperRule,
    GatekeeperVerdict,
    RiskGatekeeper,
    compute_allowed_lots,
    round_trip_slippage,
)

__all__ = [
    "CreditSpreadPosition",
    "ExitAction",
    "ExitDecision",
    "ExitEngine",
    "FuturesPosition",
    "GatekeeperDecision",
    "GatekeeperRule",
    "GatekeeperVerdict",
    "RiskGatekeeper",
    "compute_allowed_lots",
    "round_trip_slippage",
]
