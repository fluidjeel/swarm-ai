"""Deterministic math helpers for Feature Engine metrics."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Sequence

from src.data.base_provider import OhlcvBar


def compute_atr(bars: Sequence[OhlcvBar], period: int = 14) -> float:
    if len(bars) < 2:
        raise ValueError("Need at least 2 OHLCV bars to compute ATR.")

    period = max(1, min(period, len(bars) - 1))
    true_ranges: list[float] = []
    for idx in range(1, len(bars)):
        high = float(bars[idx]["high"])
        low = float(bars[idx]["low"])
        prev_close = float(bars[idx - 1]["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    window = true_ranges[-period:]
    return sum(window) / len(window)


def compute_vix_atr_divergence(
    *,
    current_vix: float,
    previous_vix: float,
    nifty_bars: Sequence[OhlcvBar],
    atr_period: int = 14,
) -> float:
    if len(nifty_bars) < 2:
        return 0.0

    atr = compute_atr(nifty_bars, period=atr_period)
    if atr <= 0 or previous_vix <= 0:
        return 0.0

    vix_norm = (current_vix - previous_vix) / previous_vix
    nifty_delta = float(nifty_bars[-1]["close"]) - float(nifty_bars[-2]["close"])
    nifty_norm = nifty_delta / atr
    return vix_norm - nifty_norm


def compute_expiry_weighted_pcr_momentum(
    *,
    current_pcr: float,
    prior_pcr: float | None,
    dte: int,
) -> float | None:
    if prior_pcr is None or prior_pcr <= 0:
        return None

    raw_momentum = (current_pcr - prior_pcr) / prior_pcr
    dte_weight = min(max(dte, 0) / 10.0, 1.0)
    return raw_momentum * dte_weight


def compute_dte_from_expiry_timestamp(expiry_timestamp: int | None, *, now: datetime | None = None) -> int:
    if expiry_timestamp is None:
        return 0

    now = now or datetime.now(timezone.utc)
    expiry_dt = datetime.fromtimestamp(expiry_timestamp, tz=timezone.utc)
    expiry_date = expiry_dt.date()
    today = now.astimezone(timezone.utc).date()
    return max((expiry_date - today).days, 0)


def compute_ad_ratio(advancers: int, decliners: int) -> float:
    if advancers < 0 or decliners < 0:
        raise ValueError("Advancer/decliner counts must be non-negative.")
    return advancers / max(decliners, 1)
