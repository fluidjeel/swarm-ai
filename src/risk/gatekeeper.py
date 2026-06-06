"""Hard mathematical risk rules before broker execution."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.core.context import SESSION_CIRCUIT_BREAKER_PNL

RANGE_SHORT_VOL_STRATEGIES = frozenset(
    {
        "iron_condor",
        "short_strangle",
        "short_straddle",
    }
)

FUTURES_STRATEGIES = frozenset({"nifty_futures_long", "nifty_futures_short"})

VIX_CEILING = 18.0
EXPIRY_DTE_BLOCK = 1
BASE_CAPITAL_INR = 600_000.0
LOT_SCALING_STEP_INR = 400_000.0
FUTURES_ROUND_TRIP_SLIPPAGE_INR = 150.0
OPTIONS_ROUND_TRIP_SLIPPAGE_INR = 40.0


class GatekeeperVerdict(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class GatekeeperRule(StrEnum):
    DAILY_CIRCUIT_BREAKER = "daily_circuit_breaker"
    LOT_SCALING = "lot_scaling"
    VIX_CEILING = "vix_ceiling"
    GAMMA_DTE_FILTER = "gamma_dte_filter"


@dataclass(frozen=True, slots=True)
class GatekeeperDecision:
    verdict: GatekeeperVerdict
    reason: str
    rule_id: GatekeeperRule | None = None
    allowed_lots: int = 1
    expected_round_trip_cost: float = 0.0


def compute_allowed_lots(
    current_capital: float,
    *,
    base_capital: float = BASE_CAPITAL_INR,
    step: float = LOT_SCALING_STEP_INR,
) -> int:
    """HLDD §2.2: allowed_lots = 1 + floor(max(0, capital - base) / step)."""
    excess = max(0.0, current_capital - base_capital)
    return 1 + int(math.floor(excess / step))


def round_trip_slippage(strategy: str) -> float:
    """HLDD §2.2: ₹150 futures / ₹40 options round-trip."""
    if strategy.strip().lower() in FUTURES_STRATEGIES:
        return FUTURES_ROUND_TRIP_SLIPPAGE_INR
    return OPTIONS_ROUND_TRIP_SLIPPAGE_INR


class RiskGatekeeper:
    """Absolute final authority before Fyers API execution."""

    def __init__(
        self,
        *,
        max_daily_loss: float = SESSION_CIRCUIT_BREAKER_PNL,
        vix_ceiling: float = VIX_CEILING,
        expiry_dte_block: int = EXPIRY_DTE_BLOCK,
        base_capital: float = BASE_CAPITAL_INR,
        lot_scaling_step: float = LOT_SCALING_STEP_INR,
    ) -> None:
        self.max_daily_loss = max_daily_loss
        self.vix_ceiling = vix_ceiling
        self.expiry_dte_block = expiry_dte_block
        self.base_capital = base_capital
        self.lot_scaling_step = lot_scaling_step

    def evaluate(
        self,
        *,
        strategy: str,
        feature_payload: dict[str, Any],
        daily_realized_pnl: float,
        current_capital: float = BASE_CAPITAL_INR,
        requested_lots: int = 1,
    ) -> GatekeeperDecision:
        vix = _read_float(feature_payload, "vix", "VIX")
        dte = _read_int(feature_payload, "dte", "DTE")
        strategy_key = strategy.strip().lower()
        allowed_lots = compute_allowed_lots(
            current_capital,
            base_capital=self.base_capital,
            step=self.lot_scaling_step,
        )
        expected_cost = round_trip_slippage(strategy_key)

        if daily_realized_pnl <= self.max_daily_loss:
            return GatekeeperDecision(
                verdict=GatekeeperVerdict.REJECT,
                reason=(
                    f"Daily circuit breaker hit: realized PnL {daily_realized_pnl:.2f} "
                    f"<= {self.max_daily_loss:.2f}"
                ),
                rule_id=GatekeeperRule.DAILY_CIRCUIT_BREAKER,
                allowed_lots=allowed_lots,
                expected_round_trip_cost=expected_cost,
            )

        if requested_lots > allowed_lots:
            return GatekeeperDecision(
                verdict=GatekeeperVerdict.REJECT,
                reason=(
                    f"Lot scaling cap exceeded: requested {requested_lots} > "
                    f"allowed {allowed_lots} at capital {current_capital:.2f}"
                ),
                rule_id=GatekeeperRule.LOT_SCALING,
                allowed_lots=allowed_lots,
                expected_round_trip_cost=expected_cost,
            )

        if strategy_key in RANGE_SHORT_VOL_STRATEGIES and vix > self.vix_ceiling:
            return GatekeeperDecision(
                verdict=GatekeeperVerdict.REJECT,
                reason=f"VIX ceiling breached for short-vol RANGE strategy: {vix:.2f} > {self.vix_ceiling:.2f}",
                rule_id=GatekeeperRule.VIX_CEILING,
                allowed_lots=allowed_lots,
                expected_round_trip_cost=expected_cost,
            )

        if strategy_key in RANGE_SHORT_VOL_STRATEGIES and dte <= self.expiry_dte_block:
            return GatekeeperDecision(
                verdict=GatekeeperVerdict.REJECT,
                reason=f"Gamma/DTE filter: DTE {dte} <= {self.expiry_dte_block} for RANGE strategy",
                rule_id=GatekeeperRule.GAMMA_DTE_FILTER,
                allowed_lots=allowed_lots,
                expected_round_trip_cost=expected_cost,
            )

        return GatekeeperDecision(
            verdict=GatekeeperVerdict.APPROVE,
            reason="All gatekeeper checks passed.",
            rule_id=None,
            allowed_lots=allowed_lots,
            expected_round_trip_cost=expected_cost,
        )


def _read_float(payload: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if key in payload and payload[key] is not None:
            return float(payload[key])
    raise KeyError(f"Missing required numeric field: one of {keys}")


def _read_int(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in payload and payload[key] is not None:
            return int(payload[key])
    raise KeyError(f"Missing required integer field: one of {keys}")
