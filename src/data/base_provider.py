"""Abstract market data provider contract for the Feature Engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from src.core.context import OpenPosition


class OhlcvBar(TypedDict):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True, slots=True)
class BreadthSnapshot:
    """Advancers / decliners snapshot for index breadth proxy."""

    ad_ratio: float
    advancers: int
    decliners: int
    unchanged: int
    sample_size: int


@dataclass(frozen=True, slots=True)
class OptionChainPcr:
    """Put/Call ratio for the active option expiry."""

    pcr: float
    call_oi: int
    put_oi: int
    expiry_timestamp: int | None
    symbol: str


@dataclass(frozen=True, slots=True)
class Quote:
    """Bid/ask snapshot for a symbol."""

    symbol: str
    bid: float
    ask: float
    ltp: float
    spread_pct: float
    underlying_ltp: float | None = None


@dataclass(frozen=True, slots=True)
class OptionGreeks:
    """Greeks for a single option strike."""

    symbol: str
    strike: float
    option_type: str
    delta: float
    gamma: float
    confidence: str


class MarketDataError(RuntimeError):
    """Raised when a market data provider returns an invalid or failed response."""


class FyersAuthError(MarketDataError):
    """Raised when Fyers access token is expired or invalid."""


class UntaggedPositionError(MarketDataError):
    """Raised when broker positions lack tags and leg grouping is ambiguous."""


class MarketDataTimeoutError(MarketDataError):
    """Raised when a market data request exceeds the configured timeout."""


class MarketDataProvider(ABC):
    """Async interface for index, volatility, and option-chain market data."""

    @abstractmethod
    async def get_index_ltp(self, symbol: str) -> float:
        """Return the current last-traded price for an index symbol."""

    @abstractmethod
    async def get_index_ohlcv(
        self,
        symbol: str,
        *,
        resolution: str = "5",
        lookback_bars: int = 50,
    ) -> list[OhlcvBar]:
        """Return recent OHLCV candles for an index symbol (oldest first)."""

    @abstractmethod
    async def get_vix(self) -> float:
        """Return the current India VIX level."""

    @abstractmethod
    async def get_option_chain_pcr(
        self,
        symbol: str = "NSE:NIFTY50-INDEX",
        *,
        strikecount: int = 50,
    ) -> OptionChainPcr:
        """Return put/call open-interest ratio for the current expiry."""

    @abstractmethod
    async def get_nifty50_ad_ratio(self) -> BreadthSnapshot:
        """Return Nifty 50 advancers/decliners ratio (v1 breadth proxy)."""

    @abstractmethod
    async def get_positions(self) -> list[OpenPosition]:
        """Query broker positions. Return reconstructed OpenPosition list.

        On 5xx/transient errors: raise MarketDataError (fail-closed).
        On auth expired: raise FyersAuthError.
        Never return [] silently on error.
        """

    @abstractmethod
    async def get_option_chain_greeks(
        self,
        symbol: str,
        expiry_ts: int,
    ) -> list[OptionGreeks]:
        """Return greeks with confidence flag; mark illiquid strikes low confidence."""

    @abstractmethod
    async def get_bid_ask(self, symbol: str) -> Quote:
        """Return bid, ask, ltp, spread_pct. Raise on missing fields."""
