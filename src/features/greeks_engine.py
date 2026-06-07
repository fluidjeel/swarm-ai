"""Pure Black-Scholes Greeks and implied-volatility solver (stdlib only)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

RISK_FREE_RATE_DEFAULT = 0.065
IV_SOLVER_MAX_ITER = 50
IV_SOLVER_TOL = 1e-5
IV_CLAMP_LO = 0.01
IV_CLAMP_HI = 5.00

_SQRT_2 = math.sqrt(2.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)


@dataclass(frozen=True, slots=True)
class BSMGreeks:
    """Black-Scholes greeks for one option.

    theta is per calendar day (annualized theta / 365), matching broker UIs.
    vega is sensitivity to a 1 percentage-point move in implied volatility.
    """

    delta: float
    gamma: float
    theta: float
    vega: float


@dataclass(frozen=True, slots=True)
class IVSolveResult:
    iv: float | None
    converged_in: int


@dataclass(frozen=True, slots=True)
class MarketGreeks:
    delta: float
    gamma: float
    theta: float | None
    vega: float | None
    iv: float | None
    confidence: Literal["high", "low"]
    mid_price: float
    spread_pct: float
    oi: int | None


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT_2))


def _norm_pdf(x: float) -> float:
    return (1.0 / _SQRT_2PI) * math.exp(-0.5 * x * x)


def bsm_d1(spot: float, strike: float, t_years: float, r: float, q: float, sigma: float) -> float:
    return (
        math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t_years
    ) / (sigma * math.sqrt(t_years))


def bsm_d2(spot: float, strike: float, t_years: float, r: float, q: float, sigma: float) -> float:
    return bsm_d1(spot, strike, t_years, r, q, sigma) - sigma * math.sqrt(t_years)


def bsm_call_price(
    spot: float, strike: float, t_years: float, r: float, q: float, sigma: float
) -> float:
    d1 = bsm_d1(spot, strike, t_years, r, q, sigma)
    d2 = bsm_d2(spot, strike, t_years, r, q, sigma)
    return spot * math.exp(-q * t_years) * _norm_cdf(d1) - strike * math.exp(
        -r * t_years
    ) * _norm_cdf(d2)


def bsm_put_price(
    spot: float, strike: float, t_years: float, r: float, q: float, sigma: float
) -> float:
    d1 = bsm_d1(spot, strike, t_years, r, q, sigma)
    d2 = bsm_d2(spot, strike, t_years, r, q, sigma)
    return strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * math.exp(
        -q * t_years
    ) * _norm_cdf(-d1)


def _bsm_vega_raw(spot: float, t_years: float, r: float, q: float, d1: float) -> float:
    return spot * math.exp(-q * t_years) * _norm_pdf(d1) * math.sqrt(t_years)


def bsm_greeks(
    spot: float,
    strike: float,
    t_years: float,
    r: float,
    q: float,
    sigma: float,
    *,
    is_call: bool,
) -> BSMGreeks:
    d1 = bsm_d1(spot, strike, t_years, r, q, sigma)
    d2 = bsm_d2(spot, strike, t_years, r, q, sigma)
    disc_q = math.exp(-q * t_years)
    disc_r = math.exp(-r * t_years)
    pdf_d1 = _norm_pdf(d1)
    sqrt_t = math.sqrt(t_years)

    if is_call:
        delta = disc_q * _norm_cdf(d1)
        theta_annual = (
            -(spot * pdf_d1 * sigma * disc_q) / (2.0 * sqrt_t)
            - r * strike * disc_r * _norm_cdf(d2)
            + q * spot * disc_q * _norm_cdf(d1)
        )
    else:
        delta = disc_q * (_norm_cdf(d1) - 1.0)
        theta_annual = (
            -(spot * pdf_d1 * sigma * disc_q) / (2.0 * sqrt_t)
            + r * strike * disc_r * _norm_cdf(-d2)
            - q * spot * disc_q * _norm_cdf(-d1)
        )

    gamma = disc_q * pdf_d1 / (spot * sigma * sqrt_t)
    vega = _bsm_vega_raw(spot, t_years, r, q, d1) / 100.0

    return BSMGreeks(
        delta=delta,
        gamma=gamma,
        theta=theta_annual / 365.0,
        vega=vega,
    )


def _price_for_type(
    spot: float,
    strike: float,
    t_years: float,
    r: float,
    q: float,
    sigma: float,
    *,
    is_call: bool,
) -> float:
    if is_call:
        return bsm_call_price(spot, strike, t_years, r, q, sigma)
    return bsm_put_price(spot, strike, t_years, r, q, sigma)


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    t_years: float,
    r: float,
    q: float = 0.0,
    *,
    is_call: bool,
    tol: float = IV_SOLVER_TOL,
    max_iter: int = IV_SOLVER_MAX_ITER,
) -> IVSolveResult:
    if (
        market_price <= 0.0
        or spot <= 0.0
        or strike <= 0.0
        or t_years <= 0.0
        or max_iter < 1
    ):
        return IVSolveResult(iv=None, converged_in=max_iter)

    sigma = 0.20
    use_bisection = False
    lo = IV_CLAMP_LO
    hi = IV_CLAMP_HI
    price_lo = _price_for_type(spot, strike, t_years, r, q, lo, is_call=is_call)
    price_hi = _price_for_type(spot, strike, t_years, r, q, hi, is_call=is_call)
    brackets = price_lo <= market_price <= price_hi or price_hi <= market_price <= price_lo

    for iteration in range(1, max_iter + 1):
        if use_bisection or not brackets:
            if not brackets:
                return IVSolveResult(iv=None, converged_in=iteration - 1 or max_iter)

            mid = 0.5 * (lo + hi)
            price_mid = _price_for_type(spot, strike, t_years, r, q, mid, is_call=is_call)
            if abs(price_mid - market_price) < tol:
                return IVSolveResult(iv=mid, converged_in=iteration)

            if (price_lo - market_price) * (price_mid - market_price) <= 0.0:
                hi = mid
                price_hi = price_mid
            else:
                lo = mid
                price_lo = price_mid
            sigma = 0.5 * (lo + hi)
            continue

        price = _price_for_type(spot, strike, t_years, r, q, sigma, is_call=is_call)
        diff = price - market_price
        if abs(diff) < tol:
            return IVSolveResult(iv=sigma, converged_in=iteration)

        d1 = bsm_d1(spot, strike, t_years, r, q, sigma)
        vega = _bsm_vega_raw(spot, t_years, r, q, d1)
        if vega <= 1e-12:
            use_bisection = True
            continue

        sigma_next = sigma - diff / vega
        if sigma_next < IV_CLAMP_LO or sigma_next > IV_CLAMP_HI:
            use_bisection = True
            continue

        sigma = max(IV_CLAMP_LO, min(IV_CLAMP_HI, sigma_next))

    if brackets:
        return IVSolveResult(iv=0.5 * (lo + hi), converged_in=max_iter)
    return IVSolveResult(iv=None, converged_in=max_iter)


def _finite_positive(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0.0


def _mid_or_ltp(bid: float | None, ask: float | None, ltp: float) -> float:
    if _finite_positive(bid) and _finite_positive(ask) and ask is not None and bid is not None:
        if ask >= bid:
            return (bid + ask) / 2.0
    return ltp


def _spread_pct(bid: float | None, ask: float | None, mid: float) -> float:
    if mid <= 0.0 or not _finite_positive(bid) or not _finite_positive(ask):
        return float("inf")
    if ask is None or bid is None:
        return float("inf")
    return (ask - bid) / mid


def _confidence(
    iv: float | None,
    oi: int | None,
    spread_pct: float,
    *,
    converged_in: int,
) -> Literal["high", "low"]:
    if iv is None:
        return "low"
    if not (0.05 <= iv <= 1.50):
        return "low"
    if converged_in >= 20:
        return "low"
    if spread_pct > 0.10:
        return "low"
    if oi is not None and oi < 100:
        return "low"
    return "high"


def compute_greeks_from_market(
    *,
    spot: float,
    strike: float,
    dte_days: int,
    r: float,
    q: float = 0.0,
    option_ltp: float,
    bid: float | None,
    ask: float | None,
    oi: int | None,
    is_call: bool,
    price_side: Literal["mid", "ask"] = "mid",
    iv_max_iter: int = IV_SOLVER_MAX_ITER,
    iv_tol: float = IV_SOLVER_TOL,
) -> MarketGreeks:
    if spot <= 0.0 or strike <= 0.0 or option_ltp <= 0.0:
        return MarketGreeks(
            delta=0.0,
            gamma=0.0,
            theta=None,
            vega=None,
            iv=None,
            confidence="low",
            mid_price=max(option_ltp, 0.0),
            spread_pct=float("inf"),
            oi=oi,
        )

    t_years = max(dte_days / 365.25, 1e-5)
    mid = _mid_or_ltp(bid, ask, option_ltp)
    spread = _spread_pct(bid, ask, mid)

    if price_side == "ask" and _finite_positive(ask):
        iv_price = float(ask)
    else:
        iv_price = mid

    iv_result = implied_volatility(
        iv_price,
        spot,
        strike,
        t_years,
        r,
        q,
        is_call=is_call,
        tol=iv_tol,
        max_iter=iv_max_iter,
    )
    sigma = iv_result.iv if iv_result.iv is not None else 0.20
    greeks = bsm_greeks(spot, strike, t_years, r, q, sigma, is_call=is_call)
    confidence = _confidence(iv_result.iv, oi, spread, converged_in=iv_result.converged_in)

    return MarketGreeks(
        delta=greeks.delta,
        gamma=greeks.gamma,
        theta=None,
        vega=None,
        iv=iv_result.iv,
        confidence=confidence,
        mid_price=mid,
        spread_pct=spread,
        oi=oi,
    )
