"""Agent 2: pure-Python strategy matrix lookup."""

from __future__ import annotations

from src.core.context import AgentContext, RegimeLabel, StrategyDecision

REGIME_STRATEGY_MATRIX: dict[RegimeLabel, str] = {
    RegimeLabel.RANGE: "iron_condor",
    RegimeLabel.TREND_UP: "bull_call_spread",
    RegimeLabel.TREND_DOWN: "bear_put_spread",
    RegimeLabel.CHOPPY: "cash_no_trade",
    RegimeLabel.UNCERTAIN: "cash_no_trade",
}


def select_strategy(ctx: AgentContext) -> AgentContext:
    if ctx.regime_decision is None:
        return ctx.update(
            strategy_decision=StrategyDecision(
                strategy="cash_no_trade",
                supporting_signals=["no_regime_decision", "regime_missing"],
            )
        )
    strategy = REGIME_STRATEGY_MATRIX[ctx.regime_decision]
    signals = _derive_supporting_signals(ctx)
    return ctx.update(
        strategy_decision=StrategyDecision(
            strategy=strategy,
            supporting_signals=signals,
        )
    )


def _derive_supporting_signals(ctx: AgentContext) -> list[str]:
    signals: list[str] = []
    regime = ctx.opening_regime
    if regime.nifty_ad_ratio is not None:
        signals.append(f"ad_ratio={regime.nifty_ad_ratio:.2f}")
    if regime.vix is not None:
        signals.append(f"vix={regime.vix:.2f}")
    if regime.expiry_weighted_pcr_momentum is not None:
        signals.append(f"pcr_mom={regime.expiry_weighted_pcr_momentum:.3f}")
    if len(signals) < 2:
        signals.append(f"dte={ctx.dte}")
    return signals
