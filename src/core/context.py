"""
Agent Context Contract for the A2A Trading Engine.

A single validated state object passed chronologically through the execution chain.
Agents read from it and append to it; they do not fetch independent data.

Reference: .context/02_hldd.md §1.1
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

SESSION_CIRCUIT_BREAKER_PNL = -8000.0
STALE_QUOTE_POINTS = 10.0


class RegimeLabel(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    CHOPPY = "CHOPPY"
    UNCERTAIN = "UNCERTAIN"


class CriticStatus(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", validate_assignment=True)


class OvernightContext(StrictModel):
    """Priming bias populated by Agent 0 (Pre-Market Scout)."""

    bias: str | None = None
    gift_nifty_change_pct: float | None = None
    fii_net_cr: float | None = None
    dii_net_cr: float | None = None
    macro_events: list[str] = Field(default_factory=list)


class OpeningRegime(StrictModel):
    """Deterministic feature snapshot populated by the Feature Engine at 8:45 AM."""

    nifty_ad_ratio: float | None = Field(default=None, ge=0.0)
    vix: float | None = Field(default=None, ge=0.0, le=100.0)
    vix_atr_divergence: float | None = None
    expiry_weighted_pcr_momentum: float | None = None
    captured_at_iso: str | None = None


class StrategyDecision(StrictModel):
    """Strategy output populated by Agent 2 (Strategy Selector)."""

    strategy: str = Field(..., min_length=1)
    supporting_signals: list[str] = Field(..., min_length=2)


class CriticDecision(StrictModel):
    """Adversarial veto output populated by Agent 3 (The Critic)."""

    status: CriticStatus
    reason: str = Field(..., min_length=1)


class OpenPosition(StrictModel):
    """Active trade state tracked for Agent 4 (Position Advisor)."""

    symbol: str = Field(..., min_length=1)
    strategy: str = Field(..., min_length=1)
    lots: int = Field(..., ge=1)
    entry_price: float = Field(..., gt=0.0)
    leg_id: str | None = Field(
        default=None,
        description="Stable leg identifier; symbol for each broker leg.",
    )
    strategy_id: str | None = Field(
        default=None,
        description="Groups multi-leg legs under one strategy cluster.",
    )
    legs: list[OpenPosition] | None = Field(
        default=None,
        description="All legs when this row is a multi-leg summary position.",
    )


class AgentContext(StrictModel):
    """
    Canonical session state passed linearly through the swarm and deterministic engines.
    """

    session_id: str = Field(..., min_length=8, max_length=128)

    # Static per session
    overnight_context: OvernightContext = Field(default_factory=OvernightContext)
    opening_regime: OpeningRegime = Field(default_factory=OpeningRegime)

    # Dynamic, accumulated per routing event (pure Python intraday — v4.1)
    regime_decision: RegimeLabel | None = None
    strategy_decision: StrategyDecision | None = None
    critic_decision: CriticDecision | None = None

    # Risk & session state
    open_position: OpenPosition | None = None
    feature_snapshot_price: float | None = Field(
        default=None,
        gt=0.0,
        description=(
            "NIFTY LTP at feature capture; used for stale-quote aborts. "
            "Paired with baseline_initialized — Agent 3 rejects ticks when baseline is unset."
        ),
    )
    baseline_initialized: bool = Field(
        default=False,
        description=(
            "Set True after first successful snapshot capture. "
            "Required by Agent 3 to avoid stale-quote abort on first tick."
        ),
    )
    data_degraded: bool = False
    daily_pnl: float = 0.0
    circuit_status: bool = False
    dte: int = Field(default=0, ge=0, le=45)

    @model_validator(mode="after")
    def _validate_circuit_breaker(self) -> AgentContext:
        tripped = self.daily_pnl <= SESSION_CIRCUIT_BREAKER_PNL
        if tripped and not self.circuit_status:
            raise ValueError(
                f"circuit_status must be True when daily_pnl <= {SESSION_CIRCUIT_BREAKER_PNL}"
            )
        if self.circuit_status and not tripped:
            raise ValueError(
                f"circuit_status cannot be True unless daily_pnl <= {SESSION_CIRCUIT_BREAKER_PNL}"
            )
        return self

    def update(self, **fields: Any) -> AgentContext:
        """Return an updated copy; agents append state without mutating shared references."""
        return self.model_copy(update=fields)

    @property
    def is_halted(self) -> bool:
        return self.circuit_status

    @property
    def has_open_position(self) -> bool:
        return self.open_position is not None
