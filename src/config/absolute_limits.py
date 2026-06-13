"""Hard ceilings for Agent 7 parameter tuning (HLDD §2.2)."""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger("a2a.absolute_limits")


class AbsoluteLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    vix_choppy_threshold: tuple[float, float, str] = (10.0, 35.0, "VIX choppy floor/ceiling")
    ad_trend_up_threshold: tuple[float, float, str] = (0.5, 3.0, "AD ratio trend-up band")
    ad_trend_down_threshold: tuple[float, float, str] = (0.3, 2.0, "AD ratio trend-down band")
    pcr_bull_threshold: tuple[float, float, str] = (-0.50, 0.50, "PCR momentum bull band")
    pcr_bear_threshold: tuple[float, float, str] = (-0.50, 0.50, "PCR momentum bear band")
    range_divergence_band: tuple[float, float, str] = (0.01, 0.50, "Range divergence band")
    stale_quote_points: tuple[float, float, str] = (1.0, 50.0, "Stale-quote NIFTY points band")
    max_spread_pct: tuple[float, float, str] = (0.005, 0.20, "Max spread % band")
    max_gamma: tuple[float, float, str] = (0.001, 0.20, "Max gamma band")
    max_lots_per_trade: tuple[int, int, str] = (1, 10, "Max lots per trade")
    max_loss_per_trade_inr: tuple[float, float, str] = (500.0, 20000.0, "Per-trade loss cap")
    max_loss_per_day_inr: tuple[float, float, str] = (2000.0, 25000.0, "Daily loss cap")
    delta_target_short_put: tuple[float, float, str] = (-0.60, -0.05, "Short put delta target")
    delta_target_short_call: tuple[float, float, str] = (0.05, 0.60, "Short call delta target")
    delta_tolerance: tuple[float, float, str] = (0.02, 0.30, "Strike delta tolerance")
    max_dte_for_entry: tuple[int, int, str] = (0, 21, "Max DTE for entry")
    min_dte_for_entry: tuple[int, int, str] = (0, 14, "Min DTE for entry")
    wing_width_points: tuple[int, int, str] = (100, 500, "Wing width band")
    risk_free_rate: tuple[float, float, str] = (0.0, 0.20, "outside RBI plausible range 0%-20%")
    dividend_yield: tuple[float, float, str] = (0.0, 0.10, "implausible index yield > 10%")
    iv_solver_max_iter: tuple[int, int, str] = (1, 200, "Newton-Raphson budget")
    iv_tolerance: tuple[float, float, str] = (1e-9, 1e-2, "BSM solver precision")
    iv_percentile_min: tuple[float, float, str] = (5.0, 80.0, "IV percentile floor for premium selling")
    vix_low_vol_floor: tuple[float, float, str] = (8.0, 20.0, "VIX proxy floor when IVP unavailable")
    credit_stop_multiplier: tuple[float, float, str] = (1.5, 2.0, "Credit spread stop multiplier")


ABSOLUTE_LIMITS = AbsoluteLimits()


def clamp_to_absolute(key: str, value: float) -> float:
    """Return max(lower, min(upper, value)) and log if clamping occurred."""
    bounds = getattr(ABSOLUTE_LIMITS, key)
    lower, upper, reason = bounds
    lower_f = float(lower)
    upper_f = float(upper)
    if value < lower_f:
        logger.warning("Clamped %s=%s to lower bound %s (%s)", key, value, lower_f, reason)
        return lower_f
    if value > upper_f:
        logger.warning("Clamped %s=%s to upper bound %s (%s)", key, value, upper_f, reason)
        return upper_f
    return value
