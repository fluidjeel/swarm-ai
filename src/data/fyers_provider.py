"""Fyers API v3 implementation of MarketDataProvider."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

from src.data.base_provider import (
    BreadthSnapshot,
    MarketDataError,
    MarketDataProvider,
    MarketDataTimeoutError,
    OhlcvBar,
    OptionChainPcr,
)
from src.data.nifty50_symbols import load_nifty50_symbols

T = TypeVar("T")

DEFAULT_VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
DEFAULT_NIFTY_INDEX = "NSE:NIFTY50-INDEX"
DEFAULT_REQUEST_TIMEOUT_SEC = 15.0
FYERS_QUOTES_BATCH_SIZE = 50


def _require_fyers_credentials() -> tuple[str, str]:
    app_id = os.getenv("FYERS_APP_ID", "").strip()
    access_token = os.getenv("FYERS_ACCESS_TOKEN", "").strip()
    if not app_id or not access_token:
        raise MarketDataError(
            "FYERS_APP_ID and FYERS_ACCESS_TOKEN must be set in the environment."
        )
    if app_id.startswith("DUMMY") or access_token.startswith("DUMMY"):
        raise MarketDataError("Fyers credentials are placeholders; authenticate first.")
    return app_id, access_token


def _assert_fyers_ok(response: dict[str, Any], *, context: str) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise MarketDataError(f"{context}: unexpected response type {type(response)!r}")
    if response.get("s") != "ok":
        code = response.get("code", "n/a")
        message = response.get("message", "unknown error")
        raise MarketDataError(f"{context} failed (code={code}): {message}")
    return response


def _parse_history_candles(response: dict[str, Any]) -> list[OhlcvBar]:
    candles = response.get("candles")
    if not isinstance(candles, list):
        raise MarketDataError("Fyers history response missing 'candles' list.")

    bars: list[OhlcvBar] = []
    for row in candles:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        bars.append(
            {
                "timestamp": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": int(row[5]),
            }
        )
    if not bars:
        raise MarketDataError("Fyers history returned no usable candles.")
    return bars


def _extract_quote_last_price(response: dict[str, Any], symbol: str) -> float:
    quotes = response.get("d")
    if not isinstance(quotes, list) or not quotes:
        raise MarketDataError(f"Fyers quotes response empty for {symbol}.")

    for item in quotes:
        if not isinstance(item, dict):
            continue
        item_symbol = item.get("n") or item.get("symbol")
        if item_symbol and item_symbol != symbol:
            continue
        values = item.get("v")
        if not isinstance(values, dict):
            continue
        for key in ("lp", "last_price", "close"):
            if key in values and values[key] is not None:
                return float(values[key])

    first = quotes[0]
    values = first.get("v") if isinstance(first, dict) else None
    if isinstance(values, dict):
        for key in ("lp", "last_price", "close"):
            if key in values and values[key] is not None:
                return float(values[key])

    raise MarketDataError(f"Could not parse last price for {symbol} from Fyers quotes.")


def _extract_quote_prices(values: dict[str, Any]) -> tuple[float | None, float | None]:
    last_price = None
    prev_close = None
    for key in ("lp", "last_price", "close"):
        if key in values and values[key] is not None:
            last_price = float(values[key])
            break
    for key in ("prev_close_price", "prev_close", "pc", "previous_close"):
        if key in values and values[key] is not None:
            prev_close = float(values[key])
            break
    return last_price, prev_close


def _parse_breadth_from_quotes(response: dict[str, Any]) -> BreadthSnapshot:
    quotes = response.get("d")
    if not isinstance(quotes, list) or not quotes:
        raise MarketDataError("Fyers quotes response empty for breadth calculation.")

    advancers = 0
    decliners = 0
    unchanged = 0
    used = 0

    for item in quotes:
        if not isinstance(item, dict):
            continue
        values = item.get("v")
        if not isinstance(values, dict):
            continue
        last_price, prev_close = _extract_quote_prices(values)
        if last_price is None or prev_close is None or prev_close <= 0:
            continue
        used += 1
        if last_price > prev_close:
            advancers += 1
        elif last_price < prev_close:
            decliners += 1
        else:
            unchanged += 1

    if used == 0:
        raise MarketDataError("No usable Nifty 50 quote rows for breadth calculation.")

    return BreadthSnapshot(
        ad_ratio=advancers / max(decliners, 1),
        advancers=advancers,
        decliners=decliners,
        unchanged=unchanged,
        sample_size=used,
    )


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[idx : idx + size] for idx in range(0, len(items), size)]


def _sum_option_oi(chain: list[dict[str, Any]]) -> tuple[int, int, int | None]:
    call_oi = 0
    put_oi = 0
    expiry_timestamp: int | None = None

    for row in chain:
        if not isinstance(row, dict):
            continue

        option_type = str(row.get("option_type", "")).upper()
        if option_type not in {"CE", "PE"}:
            continue

        oi_raw = row.get("oi", 0)
        try:
            oi = int(oi_raw)
        except (TypeError, ValueError):
            continue

        if option_type == "CE":
            call_oi += oi
        else:
            put_oi += oi

        if expiry_timestamp is None:
            for key in ("expiry", "expiry_timestamp", "timestamp"):
                if key in row and row[key] not in (None, ""):
                    try:
                        expiry_timestamp = int(row[key])
                        break
                    except (TypeError, ValueError):
                        continue

    if call_oi <= 0:
        raise MarketDataError("Option chain call OI is zero; cannot compute PCR.")

    return call_oi, put_oi, expiry_timestamp


def _parse_option_chain_pcr(
    response: dict[str, Any],
    *,
    symbol: str,
) -> OptionChainPcr:
    data = response.get("data")
    if not isinstance(data, dict):
        raise MarketDataError("Fyers option chain response missing 'data' object.")

    chain = data.get("optionsChain")
    if not isinstance(chain, list) or not chain:
        raise MarketDataError("Fyers option chain response missing 'optionsChain'.")

    call_oi, put_oi, expiry_timestamp = _sum_option_oi(chain)
    if expiry_timestamp is None:
        expiry_raw = data.get("expiryData") or data.get("timestamp")
        if expiry_raw not in (None, ""):
            try:
                expiry_timestamp = int(expiry_raw)
            except (TypeError, ValueError):
                expiry_timestamp = None

    return OptionChainPcr(
        pcr=put_oi / call_oi,
        call_oi=call_oi,
        put_oi=put_oi,
        expiry_timestamp=expiry_timestamp,
        symbol=symbol,
    )


class FyersMarketDataProvider(MarketDataProvider):
    """Fetches market data via the official fyers-apiv3 SDK."""

    def __init__(
        self,
        *,
        app_id: str | None = None,
        access_token: str | None = None,
        request_timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC,
        vix_symbol: str = DEFAULT_VIX_SYMBOL,
        log_path: str = "",
    ) -> None:
        if app_id and access_token:
            self._app_id = app_id
            self._access_token = access_token
        else:
            self._app_id, self._access_token = _require_fyers_credentials()
        self._request_timeout_sec = request_timeout_sec
        self._vix_symbol = vix_symbol
        self._log_path = log_path
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from fyers_apiv3 import fyersModel
        except ImportError as exc:
            raise MarketDataError("fyers-apiv3 is not installed. Run: pip install fyers-apiv3") from exc

        self._client = fyersModel.FyersModel(
            client_id=self._app_id,
            is_async=False,
            token=self._access_token,
            log_path=self._log_path,
        )
        return self._client

    async def _call_with_timeout(self, fn: Callable[[], T], *, context: str) -> T:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn),
                timeout=self._request_timeout_sec,
            )
        except TimeoutError as exc:
            raise MarketDataTimeoutError(
                f"{context} timed out after {self._request_timeout_sec:.1f}s"
            ) from exc
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError(f"{context} failed: {exc}") from exc

    async def get_index_ohlcv(
        self,
        symbol: str,
        *,
        resolution: str = "5",
        lookback_bars: int = 50,
    ) -> list[OhlcvBar]:
        lookback_bars = max(lookback_bars, 1)
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=7)

        payload = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "0",
            "range_from": str(int(start_dt.timestamp())),
            "range_to": str(int(end_dt.timestamp())),
            "cont_flag": "1",
        }

        def _fetch() -> list[OhlcvBar]:
            client = self._get_client()
            response = _assert_fyers_ok(client.history(payload), context=f"history({symbol})")
            bars = _parse_history_candles(response)
            return bars[-lookback_bars:]

        return await self._call_with_timeout(_fetch, context=f"get_index_ohlcv({symbol})")

    async def get_vix(self) -> float:
        payload = {"symbols": self._vix_symbol}

        def _fetch() -> float:
            client = self._get_client()
            response = _assert_fyers_ok(
                client.quotes(payload),
                context=f"quotes({self._vix_symbol})",
            )
            return _extract_quote_last_price(response, self._vix_symbol)

        return await self._call_with_timeout(_fetch, context="get_vix")

    async def get_option_chain_pcr(
        self,
        symbol: str = "NSE:NIFTY50-INDEX",
        *,
        strikecount: int = 50,
    ) -> OptionChainPcr:
        payload = {
            "symbol": symbol,
            "strikecount": strikecount,
            "timestamp": "",
        }

        def _fetch() -> OptionChainPcr:
            client = self._get_client()
            response = _assert_fyers_ok(
                client.optionchain(payload),
                context=f"optionchain({symbol})",
            )
            return _parse_option_chain_pcr(response, symbol=symbol)

        return await self._call_with_timeout(_fetch, context=f"get_option_chain_pcr({symbol})")

    async def get_nifty50_ad_ratio(self) -> BreadthSnapshot:
        symbols = list(load_nifty50_symbols())
        batches = _chunked(symbols, FYERS_QUOTES_BATCH_SIZE)

        def _fetch() -> BreadthSnapshot:
            client = self._get_client()
            total_advancers = 0
            total_decliners = 0
            total_unchanged = 0
            total_used = 0

            for batch in batches:
                payload = {"symbols": ",".join(batch)}
                response = _assert_fyers_ok(
                    client.quotes(payload),
                    context="quotes(nifty50_breadth)",
                )
                snapshot = _parse_breadth_from_quotes(response)
                total_advancers += snapshot.advancers
                total_decliners += snapshot.decliners
                total_unchanged += snapshot.unchanged
                total_used += snapshot.sample_size

            if total_used == 0:
                raise MarketDataError("Nifty 50 breadth calculation returned zero usable quotes.")

            return BreadthSnapshot(
                ad_ratio=total_advancers / max(total_decliners, 1),
                advancers=total_advancers,
                decliners=total_decliners,
                unchanged=total_unchanged,
                sample_size=total_used,
            )

        return await self._call_with_timeout(_fetch, context="get_nifty50_ad_ratio")
