"""Deterministic exit rules for open positions (HLDD §2.3)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Sequence

from src.data.base_provider import OhlcvBar
from src.features.math_utils import compute_atr

FUTURES_STRATEGIES = frozenset({"nifty_futures_long", "nifty_futures_short"})
CREDIT_SPREAD_STRATEGIES = frozenset(
    {
        "iron_condor",
        "short_strangle",
        "short_straddle",
    }
)

ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2.0
REGIME_FLIP_AD_THRESHOLD = 1.0
THETA_CAPTURE_TARGET = 0.60
VIX_INTRADAY_SPIKE_THRESHOLD = 0.10


class ExitAction(StrEnum):
    HOLD = "HOLD"
    EXIT_MARKET = "EXIT_MARKET"


@dataclass(frozen=True, slots=True)
class ExitDecision:
    action: ExitAction
    reason: str
    rule_id: str | None = None
    trailing_stop: float | None = None
    theta_capture_pct: float | None = None


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
    ) -> None:
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.regime_flip_ad_threshold = regime_flip_ad_threshold
        self.theta_capture_target = theta_capture_target
        self.vix_spike_threshold = vix_spike_threshold

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
        strategy_key = strategy.strip().lower()

        if strategy_key in FUTURES_STRATEGIES:
            if not isinstance(position, FuturesPosition):
                raise TypeError("Futures strategies require a FuturesPosition.")
            if nifty_bars is None:
                raise ValueError("nifty_bars required for futures exit evaluation.")
            return self.evaluate_futures(
                position,
                feature_payload=feature_payload,
                nifty_bars=nifty_bars,
            )

        if strategy_key in CREDIT_SPREAD_STRATEGIES:
            if not isinstance(position, CreditSpreadPosition):
                raise TypeError("Credit spread strategies require a CreditSpreadPosition.")
            if session_open_vix is None:
                raise ValueError("session_open_vix required for credit spread exit evaluation.")
            return self.evaluate_credit_spread(
                position,
                feature_payload=feature_payload,
                session_open_vix=session_open_vix,
            )

        raise ValueError(f"Unsupported strategy for exit engine: {strategy}")


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
