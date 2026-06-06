"""Hard mathematical risk rules before broker execution."""

from __future__ import annotations

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

VIX_CEILING = 18.0
EXPIRY_DTE_BLOCK = 1


class GatekeeperVerdict(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class GatekeeperDecision:
    verdict: GatekeeperVerdict
    reason: str
    rule_id: str | None = None


class RiskGatekeeper:
    """Absolute final authority before Fyers API execution."""

    def __init__(
        self,
        *,
        max_daily_loss: float = SESSION_CIRCUIT_BREAKER_PNL,
        vix_ceiling: float = VIX_CEILING,
        expiry_dte_block: int = EXPIRY_DTE_BLOCK,
    ) -> None:
        self.max_daily_loss = max_daily_loss
        self.vix_ceiling = vix_ceiling
        self.expiry_dte_block = expiry_dte_block

    def evaluate(
        self,
        *,
        strategy: str,
        feature_payload: dict[str, Any],
        daily_realized_pnl: float,
    ) -> GatekeeperDecision:
        vix = _read_float(feature_payload, "vix", "VIX")
        dte = _read_int(feature_payload, "dte", "DTE")
        strategy_key = strategy.strip().lower()

        if daily_realized_pnl <= self.max_daily_loss:
            return GatekeeperDecision(
                verdict=GatekeeperVerdict.REJECT,
                reason=(
                    f"Daily circuit breaker hit: realized PnL {daily_realized_pnl:.2f} "
                    f"<= {self.max_daily_loss:.2f}"
                ),
                rule_id="daily_circuit_breaker",
            )

        if strategy_key in RANGE_SHORT_VOL_STRATEGIES and vix > self.vix_ceiling:
            return GatekeeperDecision(
                verdict=GatekeeperVerdict.REJECT,
                reason=f"VIX ceiling breached for short-vol RANGE strategy: {vix:.2f} > {self.vix_ceiling:.2f}",
                rule_id="vix_ceiling",
            )

        if strategy_key in RANGE_SHORT_VOL_STRATEGIES and dte <= self.expiry_dte_block:
            return GatekeeperDecision(
                verdict=GatekeeperVerdict.REJECT,
                reason=f"Gamma/DTE filter: DTE {dte} <= {self.expiry_dte_block} for RANGE strategy",
                rule_id="gamma_dte_filter",
            )

        return GatekeeperDecision(
            verdict=GatekeeperVerdict.APPROVE,
            reason="All gatekeeper checks passed.",
            rule_id=None,
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
