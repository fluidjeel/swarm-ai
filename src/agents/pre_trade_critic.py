"""Agent 3: pre-trade math critic (stale quote, spread, greeks)."""

from __future__ import annotations

from src.config.risk_config import RiskConfig
from src.core.context import AgentContext, CriticDecision, CriticStatus

# Wide enough for 5-delta iron-condor long wings (|delta| ~= 0.05).
LEG_DELTA_ABS_MIN = 0.03
LEG_DELTA_ABS_MAX = 0.97


def effective_stale_quote_threshold(
    config: RiskConfig,
    *,
    atr_5m: float | None = None,
) -> float:
    """Volatility-aware stale-quote threshold.

    Returns ``min(stale_quote_points, stale_quote_atr_mult * atr_5m)``.

    The fixed ``stale_quote_points`` (10 NIFTY pts) is a HARD CEILING per Prime
    Directive #5: if the index moved more than that since feature capture, the
    option-chain snapshot is genuinely stale regardless of volatility. The ATR
    term only *tightens* the gate in calm markets, where 10 pts is too loose.
    When ``atr_5m`` is unavailable the fixed ceiling is used (legacy behaviour).
    """
    ceiling = config.stale_quote_points
    if atr_5m is None or atr_5m <= 0.0:
        return ceiling
    adaptive = config.stale_quote_atr_mult * atr_5m
    return min(ceiling, adaptive)


def validate_pre_trade(
    ctx: AgentContext,
    *,
    live_underlying_ltp: float,
    bid_ask_spread_pct: float,
    greeks_confidence: str,
    leg_deltas: list[float],
    leg_gammas: list[float],
    config: RiskConfig,
    atr_5m: float | None = None,
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
    stale_threshold = effective_stale_quote_threshold(config, atr_5m=atr_5m)
    if abs(live_underlying_ltp - ctx.feature_snapshot_price) > stale_threshold:
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
    if not leg_deltas or not leg_gammas or len(leg_deltas) != len(leg_gammas):
        return ctx.update(
            critic_decision=CriticDecision(
                status=CriticStatus.REJECT,
                reason="greeks_missing",
            )
        )
    for delta in leg_deltas:
        if not (LEG_DELTA_ABS_MIN <= abs(delta) <= LEG_DELTA_ABS_MAX):
            return ctx.update(
                critic_decision=CriticDecision(
                    status=CriticStatus.REJECT,
                    reason="greeks_out_of_bounds",
                )
            )
    for gamma in leg_gammas:
        if gamma < 0 or gamma > config.max_gamma:
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
