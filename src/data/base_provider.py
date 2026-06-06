"""Abstract market data provider contract for the Feature Engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TypedDict


class OhlcvBar(TypedDict):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True, slots=True)
class OptionChainPcr:
    """Put/Call ratio for the active option expiry."""

    pcr: float
    call_oi: int
    put_oi: int
    expiry_timestamp: int | None
    symbol: str


class MarketDataError(RuntimeError):
    """Raised when a market data provider returns an invalid or failed response."""


class MarketDataTimeoutError(MarketDataError):
    """Raised when a market data request exceeds the configured timeout."""


class MarketDataProvider(ABC):
    """Async interface for index, volatility, and option-chain market data."""

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
