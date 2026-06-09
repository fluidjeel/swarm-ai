"""Strike and expiry selection for Agent 3 quote/greeks fetch (v4.2)."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from src.config.index_contracts import ExpirySchedule, IndexContract, resolve_index_contract
from src.config.risk_config import RiskConfig
from src.core.context import AgentContext, StrategyName
from src.data.base_provider import OptionGreeks
from src.features.math_utils import compute_dte_from_expiry_timestamp
from src.orchestration.session_clock import IST, NSE_HOLIDAYS

NIFTY_INDEX_SYMBOL = "NSE:NIFTY50-INDEX"

NIFTY_EXPIRY_CLOSE_IST = time(15, 30)


class ExpirySelectionError(Exception):
    """Raised when no expiry falls within the configured DTE band."""


class StrikeSelectionError(Exception):
    """Raised when no strike is within delta tolerance of the target."""


def quote_symbol_for_strategy(ctx: AgentContext, *, index_symbol: str = NIFTY_INDEX_SYMBOL) -> str:
    """Return the index symbol used for option chain refresh."""
    _ = ctx.strategy_decision
    return index_symbol


def expiry_ts_for_context(ctx: AgentContext) -> int:
    """Deprecated: use ``select_expiry`` with ``RiskConfig`` instead."""
    _ = ctx.dte
    return 0


def select_expiry(
    ctx: AgentContext,
    config: RiskConfig,
    *,
    index_symbol: str = NIFTY_INDEX_SYMBOL,
    now: datetime | None = None,
) -> int:
    """Return the expiry timestamp for the option chain.

    Picks the nearest upcoming expiry for ``index_symbol`` whose calendar DTE
    falls in ``[min_dte_for_entry, max_dte_for_entry]``. On index expiry day
    (PCR ``ctx.dte == 0``), rolls to the next in-band series instead of rejecting.
    """
    contract = resolve_index_contract(index_symbol)
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)

    candidates = _upcoming_expiry_timestamps(contract, now=now, count=4)
    if not candidates:
        raise ExpirySelectionError("no_expiry_within_dte_band")

    for expiry_ts in candidates:
        leg_dte = compute_dte_from_expiry_timestamp(expiry_ts, now=now)
        if config.min_dte_for_entry <= leg_dte <= config.max_dte_for_entry:
            return expiry_ts

    raise ExpirySelectionError("no_expiry_within_dte_band")


def leg_dte_for_expiry(expiry_ts: int, *, now: datetime | None = None) -> int:
    return compute_dte_from_expiry_timestamp(expiry_ts, now=now)


def select_strike(
    greeks_list: list[OptionGreeks],
    *,
    strategy: str,
    config: RiskConfig,
) -> OptionGreeks:
    """Pick the primary strike for single-leg strategies.

    Multi-leg strategies must use ``select_strategy_symbols`` instead.
    """
    strategy_key = _strategy_key(strategy)
    if strategy_key in {
        StrategyName.IRON_CONDOR.value,
        StrategyName.BULL_CALL_SPREAD.value,
        StrategyName.BEAR_PUT_SPREAD.value,
    }:
        legs = select_strategy_symbols_for_strategy(
            strategy_key,
            greeks_list=greeks_list,
            config=config,
        )
        return legs[0]
    if strategy_key == StrategyName.CASH_NO_TRADE.value:
        raise ValueError("select_strike must not be called for cash_no_trade")
    raise ValueError(f"Unsupported strategy for strike selection: {strategy}")


def select_strategy_symbols(
    ctx: AgentContext,
    *,
    greeks_list: list[OptionGreeks],
    config: RiskConfig,
) -> list[OptionGreeks]:
    """Return the leg ``OptionGreeks`` rows Agent 3 will validate."""
    if ctx.strategy_decision is None:
        raise ValueError("strategy_decision required for symbol selection")
    return select_strategy_symbols_for_strategy(
        ctx.strategy_decision.strategy,
        greeks_list=greeks_list,
        config=config,
    )


def select_strategy_symbols_for_strategy(
    strategy: str,
    *,
    greeks_list: list[OptionGreeks],
    config: RiskConfig,
) -> list[OptionGreeks]:
    strategy_key = _strategy_key(strategy)
    if strategy_key == StrategyName.CASH_NO_TRADE.value:
        raise ValueError("select_strategy_symbols must not be called for cash_no_trade")

    if strategy_key == StrategyName.IRON_CONDOR.value:
        return _select_iron_condor_legs(greeks_list, config=config)
    if strategy_key == StrategyName.BULL_CALL_SPREAD.value:
        return _select_bull_call_spread_legs(greeks_list, config=config)
    if strategy_key == StrategyName.BEAR_PUT_SPREAD.value:
        return _select_bear_put_spread_legs(greeks_list, config=config)

    raise ValueError(f"Unsupported strategy for symbol selection: {strategy}")


def _upcoming_expiry_timestamps(
    contract: IndexContract,
    *,
    now: datetime,
    count: int,
) -> list[int]:
    if contract.schedule == ExpirySchedule.MONTHLY_LAST_TUESDAY:
        return _monthly_last_tuesday_timestamps(now=now, count=count)
    weekday = _weekly_weekday_for_contract(contract, on_date=now.date())
    return _weekly_expiry_timestamps(weekday=weekday, now=now, count=count)


def _weekly_weekday_for_contract(contract: IndexContract, *, on_date: date) -> int:
    from src.config.index_contracts import weekly_expiry_weekday

    return weekly_expiry_weekday(contract, on_date=on_date)


def _is_valid_expiry_date(candidate: date) -> bool:
    if candidate.weekday() >= 5:
        return False
    return candidate not in NSE_HOLIDAYS


def _weekly_expiry_timestamps(
    *,
    weekday: int,
    now: datetime | None = None,
    count: int = 2,
) -> list[int]:
    """Return the next ``count`` expiry timestamps on ``weekday`` (IST close)."""
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)

    expiries: list[int] = []
    cursor_date = now.date()

    for _ in range(count + 16):
        days_until = (weekday - cursor_date.weekday()) % 7
        candidate_date = cursor_date + timedelta(days=days_until)
        if not _is_valid_expiry_date(candidate_date):
            cursor_date = candidate_date + timedelta(days=1)
            continue

        expiry_dt = datetime.combine(candidate_date, NIFTY_EXPIRY_CLOSE_IST, tzinfo=IST)
        if expiry_dt > now:
            ts = int(expiry_dt.timestamp())
            if ts not in expiries:
                expiries.append(ts)
            if len(expiries) >= count:
                break
        cursor_date = candidate_date + timedelta(days=1)

    return expiries[:count]


def _monthly_last_tuesday_timestamps(*, now: datetime, count: int) -> list[int]:
    expiries: list[int] = []
    year = now.year
    month = now.month

    for _ in range(count + 24):
        last_day = calendar.monthrange(year, month)[1]
        candidate = date(year, month, last_day)
        while candidate.weekday() != 1:
            candidate -= timedelta(days=1)
        while not _is_valid_expiry_date(candidate):
            candidate -= timedelta(days=1)

        expiry_dt = datetime.combine(candidate, NIFTY_EXPIRY_CLOSE_IST, tzinfo=IST)
        if expiry_dt > now:
            ts = int(expiry_dt.timestamp())
            if ts not in expiries:
                expiries.append(ts)
            if len(expiries) >= count:
                break

        month += 1
        if month > 12:
            month = 1
            year += 1

    return expiries[:count]


def _pick_by_delta(
    greeks_list: list[OptionGreeks],
    *,
    option_type: str,
    target_delta: float,
    tolerance: float,
) -> OptionGreeks:
    normalized = option_type.upper()
    candidates = [row for row in greeks_list if row.option_type.upper() == normalized]
    if not candidates:
        raise StrikeSelectionError(f"no_{normalized.lower()}_strikes_available")

    best = min(candidates, key=lambda row: abs(row.delta - target_delta))
    if abs(best.delta - target_delta) > tolerance:
        deltas = sorted(row.delta for row in candidates)
        raise StrikeSelectionError(
            "delta_out_of_tolerance: "
            f"target={target_delta:.3f}, best={best.delta:.3f}, "
            f"type={normalized}, distribution={deltas}"
        )
    return best


def _pick_by_strike(
    greeks_list: list[OptionGreeks],
    *,
    option_type: str,
    target_strike: float,
) -> OptionGreeks:
    normalized = option_type.upper()
    candidates = [row for row in greeks_list if row.option_type.upper() == normalized]
    if not candidates:
        raise StrikeSelectionError(f"no_{normalized.lower()}_strikes_available")
    return min(candidates, key=lambda row: abs(row.strike - target_strike))


def _select_iron_condor_legs(
    greeks_list: list[OptionGreeks],
    *,
    config: RiskConfig,
) -> list[OptionGreeks]:
    short_put = _pick_by_delta(
        greeks_list,
        option_type="PE",
        target_delta=config.delta_target_short_put,
        tolerance=config.delta_tolerance,
    )
    wing_width = float(config.wing_width_points)
    long_put = _pick_by_strike(
        greeks_list,
        option_type="PE",
        target_strike=short_put.strike - wing_width,
    )
    short_call = _pick_by_delta(
        greeks_list,
        option_type="CE",
        target_delta=config.delta_target_short_call,
        tolerance=config.delta_tolerance,
    )
    long_call = _pick_by_strike(
        greeks_list,
        option_type="CE",
        target_strike=short_call.strike + wing_width,
    )
    return [long_put, short_put, short_call, long_call]


def _strategy_key(strategy: StrategyName | str) -> str:
    if isinstance(strategy, StrategyName):
        return strategy.value
    return strategy.strip().lower()


def _select_bull_call_spread_legs(
    greeks_list: list[OptionGreeks],
    *,
    config: RiskConfig,
) -> list[OptionGreeks]:
    long_call = _pick_by_delta(
        greeks_list,
        option_type="CE",
        target_delta=0.50,
        tolerance=config.delta_tolerance,
    )
    short_call = _pick_by_delta(
        greeks_list,
        option_type="CE",
        target_delta=0.20,
        tolerance=config.delta_tolerance,
    )
    return [long_call, short_call]


def _select_bear_put_spread_legs(
    greeks_list: list[OptionGreeks],
    *,
    config: RiskConfig,
) -> list[OptionGreeks]:
    long_put = _pick_by_delta(
        greeks_list,
        option_type="PE",
        target_delta=-0.50,
        tolerance=config.delta_tolerance,
    )
    short_put = _pick_by_delta(
        greeks_list,
        option_type="PE",
        target_delta=-0.20,
        tolerance=config.delta_tolerance,
    )
    return [long_put, short_put]
