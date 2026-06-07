"""Agent 1: pure-Python regime classification (thresholds only)."""

from __future__ import annotations

from src.config.risk_config import RiskConfig
from src.core.context import AgentContext, RegimeLabel


def classify_regime(ctx: AgentContext, *, config: RiskConfig) -> AgentContext:
    """Pure threshold function; no I/O. Completes in <10ms."""
    regime = ctx.opening_regime
    if regime.vix is None or regime.nifty_ad_ratio is None:
        return ctx.update(regime_decision=RegimeLabel.UNCERTAIN)

    if regime.vix > config.vix_choppy_threshold:
        return ctx.update(regime_decision=RegimeLabel.CHOPPY)

    pcr = regime.expiry_weighted_pcr_momentum or 0.0
    if regime.nifty_ad_ratio >= config.ad_trend_up_threshold and pcr > config.pcr_bull_threshold:
        return ctx.update(regime_decision=RegimeLabel.TREND_UP)
    if regime.nifty_ad_ratio <= config.ad_trend_down_threshold and pcr < config.pcr_bear_threshold:
        return ctx.update(regime_decision=RegimeLabel.TREND_DOWN)
    if abs(regime.vix_atr_divergence or 0.0) < config.range_divergence_band:
        return ctx.update(regime_decision=RegimeLabel.RANGE)
    return ctx.update(regime_decision=RegimeLabel.UNCERTAIN)
