"""Agent 2: pure-Python strategy matrix lookup.

# v4.1 STRATEGY POLICY (architect-signed, do not extend):
#
# This module maps Regime -> Strategy for the v4.1 deterministic
# engine. The matrix below is the SINGLE allowed entry point for
# any new strategy. To add a strategy:
#   1. Add it to StrategyName StrEnum in src/core/context.py
#   2. Add the leg-count to LEG_COUNTS in src/core/strategy_registry.py
#   3. Add a Regime mapping in REGIME_STRATEGY_MATRIX below
#   4. Add the strike-selection function in src/agents/symbol_resolver.py
#   5. Add the gatekeeper rule in src/risk/gatekeeper.py
#   6. Update .context/06_pending_fixes.md with the capital
#      impact analysis
#
# EXCLUDED strategies (do not add):
#   - short_strangle / short_straddle: undefined risk, margin
#     requirement > ₹6L per leg
#   - nifty_futures_long / nifty_futures_short: catastrophic loss
#     on t3.small account size
#
# Any future Cursor session that proposes adding these strategies
# without explicit product-manager approval should be rejected.
"""

from __future__ import annotations

from src.core.context import AgentContext, RegimeLabel, StrategyDecision, StrategyName

REGIME_STRATEGY_MATRIX: dict[RegimeLabel, StrategyName] = {
    RegimeLabel.RANGE: StrategyName.IRON_CONDOR,
    RegimeLabel.TREND_UP: StrategyName.BULL_CALL_SPREAD,
    RegimeLabel.TREND_DOWN: StrategyName.BEAR_PUT_SPREAD,
    RegimeLabel.CHOPPY: StrategyName.CASH_NO_TRADE,
    RegimeLabel.UNCERTAIN: StrategyName.CASH_NO_TRADE,
}


def select_strategy(ctx: AgentContext) -> AgentContext:
    if ctx.regime_decision is None:
        return ctx.update(
            strategy_decision=StrategyDecision(
                strategy=StrategyName.CASH_NO_TRADE,
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
