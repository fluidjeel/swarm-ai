"""Supported index option contracts for v4.1 intraday execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config.risk_config import RiskConfig


class ExpirySchedule(str, Enum):
    NIFTY_WEEKLY = "nifty_weekly"  # Thursday before 2025-09-01, Tuesday after
    WEEKLY_TUESDAY = "weekly_tuesday"
    WEEKLY_THURSDAY = "weekly_thursday"
    MONTHLY_LAST_TUESDAY = "monthly_last_tuesday"


# NSE moved NIFTY weekly expiry from Thursday → Tuesday effective 2025-09-01.
NIFTY_TUESDAY_EXPIRY_START = date(2025, 9, 1)


@dataclass(frozen=True, slots=True)
class IndexContract:
    key: str
    symbol: str
    lot_size: int
    schedule: ExpirySchedule
    display_name: str
    min_dte_for_entry: int | None = None
    max_dte_for_entry: int | None = None
    wing_width_points: int | None = None
    stale_quote_points: float | None = None


_INDEX_REGISTRY: dict[str, IndexContract] = {
    "nifty": IndexContract(
        key="nifty",
        symbol="NSE:NIFTY50-INDEX",
        lot_size=50,
        schedule=ExpirySchedule.NIFTY_WEEKLY,
        display_name="NIFTY 50",
    ),
    "sensex": IndexContract(
        key="sensex",
        symbol="BSE:SENSEX-INDEX",
        lot_size=10,
        schedule=ExpirySchedule.WEEKLY_THURSDAY,
        display_name="SENSEX",
    ),
    "banknifty": IndexContract(
        key="banknifty",
        symbol="NSE:NIFTYBANK-INDEX",
        lot_size=15,
        schedule=ExpirySchedule.MONTHLY_LAST_TUESDAY,
        display_name="BANK NIFTY",
        min_dte_for_entry=7,
        max_dte_for_entry=21,
        wing_width_points=500,
        stale_quote_points=50.0,
    ),
}

_SYMBOL_LOOKUP: dict[str, IndexContract] = {
    contract.symbol.upper(): contract for contract in _INDEX_REGISTRY.values()
}


SOAK_INDEX_KEYS: tuple[str, ...] = ("nifty", "banknifty", "sensex")


def list_index_keys() -> tuple[str, ...]:
    return tuple(_INDEX_REGISTRY.keys())


def resolve_soak_index_keys(name_or_symbol: str) -> tuple[str, ...]:
    """Resolve soak target(s): single key, comma-separated list, or ``all``."""
    normalized = name_or_symbol.strip().lower()
    if not normalized:
        raise ValueError("Soak index selection is required.")

    if normalized in {"all", "*", "triple", "multi"}:
        return SOAK_INDEX_KEYS

    if "," in normalized:
        keys: list[str] = []
        for part in normalized.split(","):
            part = part.strip()
            if not part:
                continue
            keys.append(resolve_index_contract(part).key)
        if not keys:
            raise ValueError("Soak index list is empty.")
        return tuple(keys)

    return (resolve_index_contract(normalized).key,)


def resolve_index_contract(name_or_symbol: str) -> IndexContract:
    """Resolve a shorthand key (``nifty``) or full Fyers symbol to an ``IndexContract``."""
    normalized = name_or_symbol.strip()
    if not normalized:
        raise ValueError("Index contract name or symbol is required.")

    key = normalized.lower()
    if key in _INDEX_REGISTRY:
        return _INDEX_REGISTRY[key]

    symbol_key = normalized.upper()
    if symbol_key in _SYMBOL_LOOKUP:
        return _SYMBOL_LOOKUP[symbol_key]

    raise ValueError(
        f"Unsupported index contract {name_or_symbol!r}. "
        f"Choose one of: {', '.join(list_index_keys())}, or a full Fyers index symbol."
    )


def risk_config_for_contract(
    contract: IndexContract,
    base: "RiskConfig | None" = None,
) -> "RiskConfig":
    """Merge per-index soak/execution overrides onto the shared risk config."""
    from src.config.risk_config import load_risk_config

    resolved = base or load_risk_config()
    updates: dict[str, int | float] = {}
    if contract.min_dte_for_entry is not None:
        updates["min_dte_for_entry"] = contract.min_dte_for_entry
    if contract.max_dte_for_entry is not None:
        updates["max_dte_for_entry"] = contract.max_dte_for_entry
    if contract.wing_width_points is not None:
        updates["wing_width_points"] = contract.wing_width_points
    if contract.stale_quote_points is not None:
        updates["stale_quote_points"] = contract.stale_quote_points
    if not updates:
        return resolved
    return resolved.model_copy(update=updates)


def weekly_expiry_weekday(contract: IndexContract, *, on_date: date) -> int:
    """Return ``datetime.weekday()`` for the contract's weekly expiry (0=Mon … 6=Sun)."""
    if contract.schedule == ExpirySchedule.WEEKLY_TUESDAY:
        return 1
    if contract.schedule == ExpirySchedule.WEEKLY_THURSDAY:
        return 3
    if contract.schedule == ExpirySchedule.NIFTY_WEEKLY:
        return 1 if on_date >= NIFTY_TUESDAY_EXPIRY_START else 3
    raise ValueError(f"Contract {contract.key} has no weekly expiry schedule.")
