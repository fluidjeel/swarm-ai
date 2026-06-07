"""Hard mathematical risk rules before broker execution."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.config.absolute_limits import clamp_to_absolute
from src.config.risk_config import RiskConfig
from src.core.context import SESSION_CIRCUIT_BREAKER_PNL, AgentContext, CriticStatus

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
    CRITIC_BLOCK = "critic_block"
    STALE_QUOTE_BLOCK = "stale_quote_block"
    UNDEFINED_RISK_BLOCK = "undefined_risk_block"
    MAX_LOSS_DAY_BLOCK = "max_loss_day_block"
    MAX_LOSS_TRADE_BLOCK = "max_loss_trade_block"
    MAX_LOTS_BLOCK = "max_lots_block"
    MARGIN_BLOCK = "margin_block"
    CASH_NO_TRADE = "cash_no_trade"


NAKED_SHORT_STRATEGIES = frozenset(
    {
        "short_strangle",
        "short_straddle",
        "naked_short_call",
        "naked_short_put",
    }
)


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


def evaluate_from_context(
    ctx: AgentContext,
    *,
    config: RiskConfig,
    gatekeeper: RiskGatekeeper | None = None,
    current_capital: float = BASE_CAPITAL_INR,
    requested_lots: int = 1,
) -> AgentContext:
    """Apply post-critic gatekeeper rules from AgentContext."""
    gk = gatekeeper or RiskGatekeeper(
        max_daily_loss=-clamp_to_absolute("max_loss_per_day_inr", config.max_loss_per_day_inr),
    )

    strategy_decision = ctx.strategy_decision
    if strategy_decision is None or strategy_decision.strategy == "cash_no_trade":
        return ctx.update(
            gatekeeper_decision=GatekeeperDecision(
                verdict=GatekeeperVerdict.REJECT,
                reason="No actionable strategy selected.",
                rule_id=GatekeeperRule.CASH_NO_TRADE,
            )
        )

    strategy_key = strategy_decision.strategy.strip().lower()
    expected_cost = round_trip_slippage(strategy_key)

    critic = ctx.critic_decision
    if critic is None or critic.status != CriticStatus.APPROVE:
        rule = GatekeeperRule.CRITIC_BLOCK
        if critic is not None and critic.reason == "stale_quote_abort":
            rule = GatekeeperRule.STALE_QUOTE_BLOCK
        detail = critic.reason if critic is not None else "critic_missing"
        return ctx.update(
            gatekeeper_decision=GatekeeperDecision(
                verdict=GatekeeperVerdict.REJECT,
                reason=f"Critic veto: {detail}",
                rule_id=rule,
                expected_round_trip_cost=expected_cost,
            )
        )

    if strategy_key in NAKED_SHORT_STRATEGIES:
        return ctx.update(
            gatekeeper_decision=GatekeeperDecision(
                verdict=GatekeeperVerdict.REJECT,
                reason=f"Undefined risk block for naked short strategy: {strategy_key}",
                rule_id=GatekeeperRule.UNDEFINED_RISK_BLOCK,
                expected_round_trip_cost=expected_cost,
            )
        )

    max_day_loss = -clamp_to_absolute("max_loss_per_day_inr", config.max_loss_per_day_inr)
    if ctx.daily_pnl <= max_day_loss:
        return ctx.update(
            gatekeeper_decision=GatekeeperDecision(
                verdict=GatekeeperVerdict.REJECT,
                reason=f"Daily loss cap breached: {ctx.daily_pnl:.2f} <= {max_day_loss:.2f}",
                rule_id=GatekeeperRule.MAX_LOSS_DAY_BLOCK,
                expected_round_trip_cost=expected_cost,
            )
        )

    max_lots = int(clamp_to_absolute("max_lots_per_trade", float(config.max_lots_per_trade)))
    if requested_lots > max_lots:
        return ctx.update(
            gatekeeper_decision=GatekeeperDecision(
                verdict=GatekeeperVerdict.REJECT,
                reason=f"Max lots block: requested {requested_lots} > {max_lots}",
                rule_id=GatekeeperRule.MAX_LOTS_BLOCK,
                allowed_lots=max_lots,
                expected_round_trip_cost=expected_cost,
            )
        )

    feature_payload = _feature_payload_from_ctx(ctx)
    legacy = gk.evaluate(
        strategy=strategy_key,
        feature_payload=feature_payload,
        daily_realized_pnl=ctx.daily_pnl,
        current_capital=current_capital,
        requested_lots=requested_lots,
    )
    return ctx.update(gatekeeper_decision=legacy)


def _feature_payload_from_ctx(ctx: AgentContext) -> dict[str, Any]:
    regime = ctx.opening_regime
    payload: dict[str, Any] = {"dte": ctx.dte}
    if regime.nifty_ad_ratio is not None:
        payload["NIFTY_500_AD_Ratio"] = regime.nifty_ad_ratio
    if regime.vix is not None:
        payload["vix"] = regime.vix
    if regime.vix_atr_divergence is not None:
        payload["VIX_ATR_Divergence"] = regime.vix_atr_divergence
    if regime.expiry_weighted_pcr_momentum is not None:
        payload["Expiry_Weighted_PCR_Momentum"] = regime.expiry_weighted_pcr_momentum
    return payload


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
