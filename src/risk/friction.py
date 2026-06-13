"""Per-leg round-trip friction model for Indian F&O (brokerage, STT, slippage)."""

from __future__ import annotations

from src.core.context import StrategyName
from src.core.strategy_registry import expected_leg_count
from src.data.base_provider import OptionGreeks, Quote

FRICTION_PER_LEG_ROUND_TRIP_INR = 40.0
FRICTION_EV_MIN_PROFIT_MULTIPLIER = 2.0

ENTRY_LEG_SIDES: dict[StrategyName, tuple[str, ...]] = {
    StrategyName.IRON_CONDOR: ("BUY", "SELL", "SELL", "BUY"),
    StrategyName.BULL_CALL_SPREAD: ("BUY", "SELL"),
    StrategyName.BEAR_PUT_SPREAD: ("BUY", "SELL"),
}

_ENTRY_LEG_SIDES = ENTRY_LEG_SIDES


def _leg_side_rows(
    strategy: StrategyName | str,
    leg_symbols: list[str],
) -> list[tuple[str, str]]:
    strategy_key = strategy if isinstance(strategy, StrategyName) else StrategyName(strategy)
    sides = ENTRY_LEG_SIDES.get(strategy_key)
    if sides is None or len(sides) != len(leg_symbols):
        raise ValueError(f"Cannot resolve leg sides for strategy {strategy_key!s}")
    return list(zip(leg_symbols, sides, strict=True))

def compute_entry_credit_inr(
    strategy: StrategyName | str,
    *,
    leg_symbols: list[str],
    per_leg_quotes: dict[str, Quote],
    lot_size: int,
    lots: int = 1,
) -> float:
    """
    Net premium cash flow at entry in INR.

    Positive = net credit received; negative = net debit paid (debit spreads).
    """
    qty = lot_size * lots
    net = 0.0
    for symbol, side in _leg_side_rows(strategy, leg_symbols):
        price = per_leg_quotes[symbol].ltp
        leg_cash = price * qty
        if side == "SELL":
            net += leg_cash
        else:
            net -= leg_cash
    return net


def compute_exit_close_cost_inr(
    strategy: StrategyName | str,
    *,
    leg_symbols: list[str],
    per_leg_quotes: dict[str, Quote],
    lot_size: int,
    lots: int = 1,
) -> float:
    """
    Net debit required to flatten the position in INR.

    Positive = pay to close; negative = receive on close (winning mark).
    gross_pnl_inr = entry_credit_inr - exit_close_cost_inr
    """
    qty = lot_size * lots
    paid = 0.0
    received = 0.0
    for symbol, entry_side in _leg_side_rows(strategy, leg_symbols):
        quote = per_leg_quotes[symbol]
        if entry_side == "SELL":
            paid += quote.ask * qty
        else:
            received += quote.bid * qty
    return paid - received


def compute_gross_pnl_inr(entry_credit_inr: float, exit_close_cost_inr: float) -> float:
    return entry_credit_inr - exit_close_cost_inr


def round_trip_friction(strategy: StrategyName | str, *, leg_count: int | None = None) -> float:
    """
    Round-trip friction in INR.

    ₹40 per leg (entry + exit + taxes/STT/slippage). Iron condor (4 legs) → ₹160;
    vertical spreads (2 legs) → ₹80.
    """
    legs = leg_count if leg_count is not None else expected_leg_count(strategy)
    return FRICTION_PER_LEG_ROUND_TRIP_INR * max(legs, 1)


def friction_ev_threshold_inr(friction_inr: float) -> float:
    """Minimum max-profit required to clear the friction EV gate."""
    return friction_inr * FRICTION_EV_MIN_PROFIT_MULTIPLIER


def passes_friction_ev_gate(max_profit_inr: float, friction_inr: float) -> bool:
    return max_profit_inr >= friction_ev_threshold_inr(friction_inr)


def estimate_max_profit_inr(
    strategy: StrategyName,
    *,
    selected_legs: list[OptionGreeks],
    per_leg_quotes: dict[str, Quote],
    lot_size: int,
    lots: int = 1,
) -> float:
    """Estimate strategy max profit at entry using leg mids and strike width."""
    sides = _ENTRY_LEG_SIDES.get(strategy)
    if sides is None or len(sides) != len(selected_legs):
        return 0.0

    qty = lot_size * lots
    net_cash = 0.0
    ce_strikes: list[float] = []
    pe_strikes: list[float] = []

    for leg_greek, side in zip(selected_legs, sides, strict=True):
        quote = per_leg_quotes[leg_greek.symbol]
        leg_cash = quote.ltp * qty
        if side == "SELL":
            net_cash += leg_cash
        else:
            net_cash -= leg_cash
        if leg_greek.option_type == "CE":
            ce_strikes.append(leg_greek.strike)
        elif leg_greek.option_type == "PE":
            pe_strikes.append(leg_greek.strike)

    if strategy == StrategyName.IRON_CONDOR:
        return max(net_cash, 0.0)

    if strategy == StrategyName.BULL_CALL_SPREAD and len(ce_strikes) >= 2:
        width = max(ce_strikes) - min(ce_strikes)
        debit = max(-net_cash, 0.0)
        return max(width * qty - debit, 0.0)

    if strategy == StrategyName.BEAR_PUT_SPREAD and len(pe_strikes) >= 2:
        width = max(pe_strikes) - min(pe_strikes)
        debit = max(-net_cash, 0.0)
        return max(width * qty - debit, 0.0)

    return max(net_cash, 0.0)


def compute_paper_exit_net_pnl(
    gross_pnl_inr: float,
    *,
    strategy: StrategyName | str,
    leg_count: int | None = None,
) -> tuple[float, float]:
    """Return (net_pnl_inr, friction_inr) after deducting round-trip friction."""
    friction = round_trip_friction(strategy, leg_count=leg_count)
    return gross_pnl_inr - friction, friction
