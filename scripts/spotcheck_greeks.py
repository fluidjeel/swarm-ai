"""Spot-check local Black-Scholes Greeks against NSE option chain website.

Compare the printed delta against the NSE option chain for the same strike/expiry.
"""

from __future__ import annotations

import argparse

from src.features.greeks_engine import RISK_FREE_RATE_DEFAULT, compute_greeks_from_market


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute local BSM Greeks for one chain row.")
    parser.add_argument("--spot", type=float, required=True)
    parser.add_argument("--strike", type=float, required=True)
    parser.add_argument("--type", choices=["CE", "PE"], required=True)
    parser.add_argument("--ltp", type=float, required=True)
    parser.add_argument("--bid", type=float, default=None)
    parser.add_argument("--ask", type=float, default=None)
    parser.add_argument("--oi", type=int, default=None)
    parser.add_argument("--dte", type=int, required=True)
    parser.add_argument("--r", type=float, default=RISK_FREE_RATE_DEFAULT)
    parser.add_argument("--q", type=float, default=0.0)
    args = parser.parse_args()

    market = compute_greeks_from_market(
        spot=args.spot,
        strike=args.strike,
        dte_days=args.dte,
        r=args.r,
        q=args.q,
        option_ltp=args.ltp,
        bid=args.bid,
        ask=args.ask,
        oi=args.oi,
        is_call=args.type == "CE",
    )
    print(f"delta={market.delta:.4f}")
    print(f"gamma={market.gamma:.6f}")
    print(f"iv={market.iv}")
    print(f"confidence={market.confidence}")
    print(f"mid_price={market.mid_price:.2f}")
    print(f"spread_pct={market.spread_pct:.4f}")


if __name__ == "__main__":
    main()
