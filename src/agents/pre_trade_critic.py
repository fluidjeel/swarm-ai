"""Agent 3: pre-trade math critic (stale quote, spread, greeks)."""

from __future__ import annotations

from src.config.risk_config import RiskConfig
from src.core.context import AgentContext, CriticDecision, CriticStatus


def validate_pre_trade(
    ctx: AgentContext,
    *,
    live_underlying_ltp: float,
    bid_ask_spread_pct: float,
    greeks_confidence: str,
    greeks_delta: float | None,
    greeks_gamma: float | None,
    config: RiskConfig,
) -> AgentContext:
    """Pure math. Reject if baseline, snapshot, stale quote, spread, or greeks fail."""
    if not ctx.baseline_initialized:
        return ctx.update(
            critic_decision=CriticDecision(
                status=CriticStatus.REJECT,
                reason="baseline_not_initialized",
            )
        )
    if ctx.feature_snapshot_price is None:
        return ctx.update(
            critic_decision=CriticDecision(
                status=CriticStatus.REJECT,
                reason="snapshot_price_missing",
            )
        )
    if abs(live_underlying_ltp - ctx.feature_snapshot_price) > config.stale_quote_points:
        return ctx.update(
            critic_decision=CriticDecision(
                status=CriticStatus.REJECT,
                reason="stale_quote_abort",
            )
        )
    if bid_ask_spread_pct > config.max_spread_pct:
        return ctx.update(
            critic_decision=CriticDecision(
                status=CriticStatus.REJECT,
                reason="spread_too_wide",
            )
        )
    if greeks_confidence == "low":
        return ctx.update(
            critic_decision=CriticDecision(
                status=CriticStatus.REJECT,
                reason="greeks_low_confidence",
            )
        )
    if greeks_delta is None or greeks_gamma is None:
        return ctx.update(
            critic_decision=CriticDecision(
                status=CriticStatus.REJECT,
                reason="greeks_missing",
            )
        )
    if not (-1.0 <= greeks_delta <= 1.0) or greeks_gamma < 0 or greeks_gamma > config.max_gamma:
        return ctx.update(
            critic_decision=CriticDecision(
                status=CriticStatus.REJECT,
                reason="greeks_out_of_bounds",
            )
        )
    return ctx.update(
        critic_decision=CriticDecision(
            status=CriticStatus.APPROVE,
            reason="math_checks_passed",
        )
    )
