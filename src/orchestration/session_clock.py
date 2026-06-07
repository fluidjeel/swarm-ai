"""NSE session phase clock for intraday pipeline gating (v4.1)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum

IST = timezone(timedelta(hours=5, minutes=30))

# Hardcoded NSE holidays for v4.1; expand via calendar service later.
NSE_HOLIDAYS: frozenset[date] = frozenset({
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 3),    # Holi
    date(2026, 3, 26),   # Good Friday
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 10),  # Diwali Balipratipada
    date(2026, 11, 24),  # Gurunanak Jayanti
    date(2026, 12, 25),  # Christmas
})


class MarketPhase(StrEnum):
    PRE_OPEN = "pre_open"
    OPENING = "opening"
    INTRADAY = "intraday"
    NO_NEW_ENTRY = "no_new_entry"
    SQUARE_OFF = "square_off"
    CLOSED = "closed"


def _to_ist(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def is_trading_day(now_ist: datetime) -> bool:
    """Return True on weekdays that are not NSE holidays."""
    local = _to_ist(now_ist)
    if local.weekday() >= 5:
        return False
    return local.date() not in NSE_HOLIDAYS


def current_phase(now_ist: datetime) -> MarketPhase:
    """Classify the current NSE cash-session phase in IST."""
    local = _to_ist(now_ist)
    if not is_trading_day(local):
        return MarketPhase.CLOSED

    clock = local.time()
    if clock < time(9, 0):
        return MarketPhase.PRE_OPEN
    if clock < time(9, 30):
        return MarketPhase.OPENING
    if clock < time(14, 30):
        return MarketPhase.INTRADAY
    if clock < time(15, 10):
        return MarketPhase.NO_NEW_ENTRY
    if clock < time(15, 20):
        return MarketPhase.SQUARE_OFF
    return MarketPhase.CLOSED


def is_session_start_allowed(now_ist: datetime) -> bool:
    """Bootstrap is allowed only during INTRADAY or SQUARE_OFF."""
    phase = current_phase(now_ist)
    return phase in {MarketPhase.INTRADAY, MarketPhase.SQUARE_OFF}
