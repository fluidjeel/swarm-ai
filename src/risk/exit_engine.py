"""Deterministic exit rules for open positions (HLDD §2.3).

Single-leg path
    Delegates to ``evaluate()`` / strategy-specific helpers (backward compatible).

Multi-leg path (``evaluate_position`` when ``open_position.legs`` has 2+ entries)
    Each leg is evaluated independently using ``per_leg_quotes[leg.symbol]``.
    ANY leg → EXIT_MARKET ⇒ overall EXIT_MARKET; all legs receive EXIT_MARKET intents
    (defensive flatten of the full cluster). ALL legs HOLD ⇒ overall HOLD with per-leg
    HOLD intents. Blended ``theta_capture_pct`` is the worst (highest) leg decay.
    Missing ``per_leg_quotes`` raises ``ExitEngineError`` (fail-closed).

Broker errors during quote fetch are handled in ``SessionPipeline._evaluate_exit``:
    emergency flatten with reason ``broker_error_emergency_flatten``.

Phase 4
    The executor will consume ``ExitDecision.leg_action_intents`` as separate orders.
    Step 4.1 stops at producing that list; no order placement here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Sequence

from src.data.base_provider import OhlcvBar, Quote
from src.features.math_utils import compute_atr

from src.core.context import OpenPosition, StrategyName
from src.risk.friction import (
    compute_entry_credit_inr,
    compute_exit_close_cost_inr,
)

ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2.0
REGIME_FLIP_AD_THRESHOLD = 1.0
THETA_CAPTURE_TARGET = 0.60
VIX_INTRADAY_SPIKE_THRESHOLD = 0.10
DEFAULT_CREDIT_STOP_MULTIPLIER = 1.5

LegIntentAction = Literal["EXIT_MARKET", "HOLD"]


class ExitEngineError(RuntimeError):
    """Raised when exit evaluation cannot proceed safely."""


class ExitAction(StrEnum):
    HOLD = "HOLD"
    EXIT_MARKET = "EXIT_MARKET"


@dataclass(frozen=True, slots=True)
class LegActionIntent:
    """Per-leg verdict consumed by the Phase-4 executor."""

    symbol: str
    action: LegIntentAction
    leg_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExitDecision:
    action: ExitAction
    reason: str
    rule_id: str | None = None
    trailing_stop: float | None = None
    theta_capture_pct: float | None = None
    leg_action_intents: list[LegActionIntent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FuturesPosition:
    side: Literal["long", "short"]
    entry_price: float
    current_price: float
    extreme_price: float


@dataclass(frozen=True, slots=True)
class CreditSpreadPosition:
    """Short-vol position: entry_credit is premium received at open."""

    entry_credit: float
    current_close_cost: float


class ExitEngine:
    """Enforces hard exit rules for directional and range positions."""

    def __init__(
        self,
        *,
        atr_period: int = ATR_PERIOD,
        atr_stop_multiplier: float = ATR_STOP_MULTIPLIER,
        regime_flip_ad_threshold: float = REGIME_FLIP_AD_THRESHOLD,
        theta_capture_target: float = THETA_CAPTURE_TARGET,
        vix_spike_threshold: float = VIX_INTRADAY_SPIKE_THRESHOLD,
        credit_stop_multiplier: float = DEFAULT_CREDIT_STOP_MULTIPLIER,
    ) -> None:
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.regime_flip_ad_threshold = regime_flip_ad_threshold
        self.theta_capture_target = theta_capture_target
        self.vix_spike_threshold = vix_spike_threshold
        self.credit_stop_multiplier = credit_stop_multiplier

    def evaluate_futures(
        self,
        position: FuturesPosition,
        *,
        feature_payload: dict[str, Any],
        nifty_bars: Sequence[OhlcvBar],
    ) -> ExitDecision:
        ad_ratio = _read_float(
            feature_payload,
            "NIFTY_500_AD_Ratio",
            "nifty_ad_ratio",
            "nifty_500_ad_ratio",
        )
        atr = compute_atr(nifty_bars, period=self.atr_period)
        updated = _update_futures_extreme(position)
        trailing_stop = compute_atr_trailing_stop(
            side=updated.side,
            extreme_price=updated.extreme_price,
            atr=atr,
            multiplier=self.atr_stop_multiplier,
        )

        if updated.side == "long" and ad_ratio < self.regime_flip_ad_threshold:
            return ExitDecision(
                action=ExitAction.EXIT_MARKET,
                reason=(
                    f"Regime flip: A/D ratio {ad_ratio:.4f} "
                    f"< {self.regime_flip_ad_threshold:.4f}"
                ),
                rule_id="regime_flip",
                trailing_stop=trailing_stop,
            )

        if _trailing_stop_breached(
            side=updated.side,
            current_price=updated.current_price,
            trailing_stop=trailing_stop,
        ):
            return ExitDecision(
                action=ExitAction.EXIT_MARKET,
                reason=(
                    f"2x ATR trailing stop breached: price {updated.current_price:.2f} "
                    f"vs stop {trailing_stop:.2f} (ATR={atr:.2f})"
                ),
                rule_id="atr_trailing_stop",
                trailing_stop=trailing_stop,
            )

        return ExitDecision(
            action=ExitAction.HOLD,
            reason="Futures position within exit thresholds.",
            trailing_stop=trailing_stop,
        )

    def evaluate_credit_spread(
        self,
        position: CreditSpreadPosition,
        *,
        feature_payload: dict[str, Any],
        session_open_vix: float,
    ) -> ExitDecision:
        current_vix = _read_float(feature_payload, "vix", "VIX")
        theta_capture = compute_theta_capture_pct(position)

        if session_open_vix > 0:
            vix_change = (current_vix - session_open_vix) / session_open_vix
            if vix_change > self.vix_spike_threshold:
                return ExitDecision(
                    action=ExitAction.EXIT_MARKET,
                    reason=(
                        f"VIX intraday spike: {vix_change * 100:.1f}% "
                        f"> {self.vix_spike_threshold * 100:.0f}% "
                        f"(open={session_open_vix:.2f}, now={current_vix:.2f})"
                    ),
                    rule_id="vix_intraday_spike",
                    theta_capture_pct=theta_capture,
                )

        if position.entry_credit > 0:
            stop_level = position.entry_credit * self.credit_stop_multiplier
            if position.current_close_cost >= stop_level:
                return ExitDecision(
                    action=ExitAction.EXIT_MARKET,
                    reason=(
                        f"Premium stop breached: close cost {position.current_close_cost:.2f} "
                        f">= {self.credit_stop_multiplier:.1f}x entry credit "
                        f"{position.entry_credit:.2f} (stop={stop_level:.2f})"
                    ),
                    rule_id="credit_stop_loss",
                    theta_capture_pct=theta_capture,
                )

        if theta_capture >= self.theta_capture_target:
            return ExitDecision(
                action=ExitAction.EXIT_MARKET,
                reason=(
                    f"Theta capture target hit: {theta_capture * 100:.1f}% "
                    f">= {self.theta_capture_target * 100:.0f}%"
                ),
                rule_id="theta_capture",
                theta_capture_pct=theta_capture,
            )

        return ExitDecision(
            action=ExitAction.HOLD,
            reason="Credit spread within exit thresholds.",
            theta_capture_pct=theta_capture,
        )

    def evaluate(
        self,
        *,
        strategy: str,
        position: FuturesPosition | CreditSpreadPosition,
        feature_payload: dict[str, Any],
        nifty_bars: Sequence[OhlcvBar] | None = None,
        session_open_vix: float | None = None,
    ) -> ExitDecision:
        _ = nifty_bars
        strategy_key = _strategy_key(strategy)
        if not _is_defined_risk_exit_strategy(strategy_key):
            raise ValueError(f"Unsupported strategy for exit engine: {strategy}")
        if not isinstance(position, CreditSpreadPosition):
            raise TypeError("Defined-risk spread strategies require a CreditSpreadPosition.")
        if session_open_vix is None:
            raise ValueError("session_open_vix required for credit spread exit evaluation.")
        return self.evaluate_credit_spread(
            position,
            feature_payload=feature_payload,
            session_open_vix=session_open_vix,
        )

    def evaluate_position(
        self,
        open_position: OpenPosition,
        *,
        feature_payload: dict[str, Any],
        nifty_bars: Sequence[OhlcvBar] | None = None,
        session_open_vix: float | None = None,
        per_leg_quotes: dict[str, Quote] | None = None,
        lot_size: int = 1,
    ) -> ExitDecision:
        """Evaluate exit for any open position, including multi-leg clusters.

        Multi-leg positions are evaluated at spread level (net entry credit vs
        net close cost), not per-leg.
        """
        legs = open_position.legs
        if legs is not None and len(legs) >= 2:
            return self._evaluate_multi_leg_spread(
                open_position,
                feature_payload=feature_payload,
                session_open_vix=session_open_vix,
                per_leg_quotes=per_leg_quotes,
                lot_size=lot_size,
            )

        if legs is None or len(legs) < 2:
            return self._evaluate_single_leg_position(
                open_position,
                feature_payload=feature_payload,
                nifty_bars=nifty_bars,
                session_open_vix=session_open_vix,
                per_leg_quotes=per_leg_quotes,
            )

    def _evaluate_multi_leg_spread(
        self,
        open_position: OpenPosition,
        *,
        feature_payload: dict[str, Any],
        session_open_vix: float | None,
        per_leg_quotes: dict[str, Quote] | None,
        lot_size: int,
    ) -> ExitDecision:
        legs = open_position.legs
        if legs is None or len(legs) < 2:
            raise ExitEngineError("multi-leg spread evaluation requires 2+ legs")
        if per_leg_quotes is None:
            raise ExitEngineError("per_leg_quotes required for multi-leg exit evaluation")
        if session_open_vix is None:
            raise ExitEngineError(
                "session_open_vix required for multi-leg credit spread exit evaluation."
            )

        strategy_key = _strategy_key(open_position.strategy)
        if not _is_defined_risk_exit_strategy(strategy_key):
            raise ExitEngineError(
                f"Unsupported strategy for multi-leg exit evaluation: {open_position.strategy}"
            )

        leg_symbols = [leg.symbol for leg in legs]
        for symbol in leg_symbols:
            if symbol not in per_leg_quotes:
                raise ExitEngineError(f"Missing per_leg_quotes entry for leg symbol: {symbol}")

        entry_credit = _resolve_entry_credit_per_unit(
            open_position,
            lot_size=lot_size,
        )
        close_cost = _resolve_exit_close_cost_per_unit(
            open_position.strategy,
            leg_symbols=leg_symbols,
            per_leg_quotes=per_leg_quotes,
            lot_size=lot_size,
            lots=open_position.lots,
        )
        spread_decision = self.evaluate_credit_spread(
            CreditSpreadPosition(
                entry_credit=entry_credit,
                current_close_cost=close_cost,
            ),
            feature_payload=feature_payload,
            session_open_vix=session_open_vix,
        )
        intents = [
            LegActionIntent(
                symbol=leg.symbol,
                action=(
                    "EXIT_MARKET"
                    if spread_decision.action == ExitAction.EXIT_MARKET
                    else "HOLD"
                ),
                leg_id=leg.leg_id or leg.symbol,
            )
            for leg in legs
        ]
        return ExitDecision(
            action=spread_decision.action,
            reason=spread_decision.reason,
            rule_id=spread_decision.rule_id,
            trailing_stop=spread_decision.trailing_stop,
            theta_capture_pct=spread_decision.theta_capture_pct,
            leg_action_intents=intents,
        )

    def build_emergency_flatten_decision(self, open_position: OpenPosition) -> ExitDecision:
        """Fail-closed flatten when broker quotes are unavailable."""
        legs = open_position.legs or []
        intents = [
            LegActionIntent(
                symbol=leg.symbol,
                action="EXIT_MARKET",
                leg_id=leg.leg_id or leg.symbol,
            )
            for leg in legs
        ]
        return ExitDecision(
            action=ExitAction.EXIT_MARKET,
            reason="broker_error_emergency_flatten",
            rule_id="broker_error_emergency_flatten",
            leg_action_intents=intents,
        )

    def _evaluate_single_leg_position(
        self,
        open_position: OpenPosition,
        *,
        feature_payload: dict[str, Any],
        nifty_bars: Sequence[OhlcvBar] | None,
        session_open_vix: float | None,
        per_leg_quotes: dict[str, Quote] | None,
    ) -> ExitDecision:
        strategy_key = _strategy_key(open_position.strategy)
        if not _is_defined_risk_exit_strategy(strategy_key):
            raise ExitEngineError(
                f"Unsupported strategy for exit evaluation: {open_position.strategy}"
            )
        if session_open_vix is None:
            raise ExitEngineError("session_open_vix required for credit spread exit evaluation.")
        quote = (per_leg_quotes or {}).get(open_position.symbol)
        close_cost = (
            _leg_close_cost(quote)
            if quote is not None
            else open_position.entry_price
        )
        position = CreditSpreadPosition(
            entry_credit=open_position.entry_price,
            current_close_cost=close_cost,
        )

        return self.evaluate(
            strategy=strategy_key,
            position=position,
            feature_payload=feature_payload,
            session_open_vix=session_open_vix,
        )


def compute_atr_trailing_stop(
    *,
    side: Literal["long", "short"],
    extreme_price: float,
    atr: float,
    multiplier: float = ATR_STOP_MULTIPLIER,
) -> float:
    offset = atr * multiplier
    if side == "long":
        return extreme_price - offset
    return extreme_price + offset


def compute_theta_capture_pct(position: CreditSpreadPosition) -> float:
    if position.entry_credit <= 0:
        raise ValueError("entry_credit must be positive for theta capture.")
    profit = position.entry_credit - position.current_close_cost
    return profit / position.entry_credit


def _resolve_entry_credit_per_unit(open_position: OpenPosition, *, lot_size: int) -> float:
    qty = lot_size * open_position.lots
    if open_position.entry_cash_flow_inr is not None and qty > 0:
        return open_position.entry_cash_flow_inr / qty
    if open_position.legs:
        leg_symbols = [leg.symbol for leg in open_position.legs]
        entry_quotes = {
            leg.symbol: Quote(
                symbol=leg.symbol,
                bid=leg.entry_price,
                ask=leg.entry_price,
                ltp=leg.entry_price,
                spread_pct=0.0,
            )
            for leg in open_position.legs
        }
        return compute_entry_credit_inr(
            open_position.strategy,
            leg_symbols=leg_symbols,
            per_leg_quotes=entry_quotes,
            lot_size=1,
            lots=1,
        )
    return open_position.entry_price


def _resolve_exit_close_cost_per_unit(
    strategy: StrategyName | str,
    *,
    leg_symbols: list[str],
    per_leg_quotes: dict[str, Quote],
    lot_size: int,
    lots: int,
) -> float:
    qty = lot_size * lots
    if qty <= 0:
        raise ExitEngineError("lot_size and lots must be positive for exit evaluation")
    total_inr = compute_exit_close_cost_inr(
        strategy,
        leg_symbols=leg_symbols,
        per_leg_quotes=per_leg_quotes,
        lot_size=lot_size,
        lots=lots,
    )
    return total_inr / qty


def _strategy_key(strategy: StrategyName | str) -> str:
    if isinstance(strategy, StrategyName):
        return strategy.value
    return strategy.strip().lower()


def _is_defined_risk_exit_strategy(strategy_key: str) -> bool:
    return strategy_key in {
        StrategyName.IRON_CONDOR.value,
        StrategyName.BULL_CALL_SPREAD.value,
        StrategyName.BEAR_PUT_SPREAD.value,
    }


def _update_futures_extreme(position: FuturesPosition) -> FuturesPosition:
    if position.side == "long":
        extreme = max(position.extreme_price, position.current_price)
    else:
        extreme = min(position.extreme_price, position.current_price)
    return FuturesPosition(
        side=position.side,
        entry_price=position.entry_price,
        current_price=position.current_price,
        extreme_price=extreme,
    )


def _trailing_stop_breached(
    *,
    side: Literal["long", "short"],
    current_price: float,
    trailing_stop: float,
) -> bool:
    if side == "long":
        return current_price <= trailing_stop
    return current_price >= trailing_stop


def _read_float(payload: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if key in payload and payload[key] is not None:
            return float(payload[key])
    raise KeyError(f"Missing required numeric field: one of {keys}")


def _leg_close_cost(quote: Quote) -> float:
    return quote.ask if quote.ask > 0 else quote.ltp
