"""Fyers API v3 implementation of MarketDataProvider."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

from src.config.risk_config import RiskConfig, load_risk_config
from src.core.context import OpenPosition
from src.data.base_provider import (
    BreadthSnapshot,
    FyersAuthError,
    MarketDataError,
    MarketDataProvider,
    MarketDataTimeoutError,
    OhlcvBar,
    OptionChainPcr,
    OptionGreeks,
    OptionQuote,
    Quote,
    UntaggedPositionError,
)
from src.data.nifty50_symbols import load_nifty50_symbols
from src.features.greeks_engine import compute_greeks_from_market
from src.features.math_utils import compute_dte_from_expiry_timestamp

logger = logging.getLogger("a2a.fyers_provider")

T = TypeVar("T")

DEFAULT_VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
DEFAULT_NIFTY_INDEX = "NSE:NIFTY50-INDEX"
DEFAULT_REQUEST_TIMEOUT_SEC = 15.0
FYERS_QUOTES_BATCH_SIZE = 50
OPT_CHAIN_STRIKE_BOUND = 15
NIFTY_OPT_CHAIN_STRIKE_BOUND = 8

_DEFAULT_STRIKE_STEP_BY_SYMBOL: dict[str, float] = {
    "NSE:NIFTY50-INDEX": 50.0,
    "NSE:NIFTYBANK-INDEX": 100.0,
    "BSE:SENSEX-INDEX": 100.0,
}


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
        if code in (-8, -9, 401, 403, "401", "403"):
            raise FyersAuthError(f"{context} auth failed (code={code}): {message}")
        if isinstance(code, int) and code >= 500:
            raise MarketDataError(f"{context} broker unavailable (code={code}): {message}")
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


def _row_strike_price(row: dict[str, Any]) -> float | None:
    strike_raw = row.get("strike_price", row.get("strike"))
    if strike_raw is None:
        return None
    try:
        return float(strike_raw)
    except (TypeError, ValueError):
        return None


def _infer_strike_step(chain: list[dict[str, Any]], symbol: str) -> float:
    configured = _DEFAULT_STRIKE_STEP_BY_SYMBOL.get(symbol.upper())
    if configured is not None:
        return configured

    strikes = sorted(
        strike
        for row in chain
        if isinstance(row, dict)
        for strike in [_row_strike_price(row)]
        if strike is not None
    )
    if len(strikes) < 2:
        return 50.0

    diffs = [right - left for left, right in zip(strikes, strikes[1:]) if right > left]
    return min(diffs) if diffs else 50.0


def _opt_chain_strike_bound(symbol: str) -> int:
    """Per-index strike window for option-chain fetch and post-filter."""
    if symbol.upper() == DEFAULT_NIFTY_INDEX:
        return NIFTY_OPT_CHAIN_STRIKE_BOUND
    return OPT_CHAIN_STRIKE_BOUND


def _build_option_chain_payload(
    symbol: str,
    *,
    timestamp: str = "",
) -> dict[str, str | int]:
    """Minimal Fyers optionchain request — tight strikecount reduces broker payload."""
    return {
        "symbol": symbol,
        "strikecount": _opt_chain_strike_bound(symbol),
        "timestamp": timestamp,
    }


def _filter_chain_rows_near_spot(
    chain: list[dict[str, Any]],
    *,
    spot: float,
    symbol: str,
    strike_bound: int | None = None,
) -> list[dict[str, Any]]:
    """Keep only strikes within ±strike_bound steps of the underlying spot."""
    bound = strike_bound if strike_bound is not None else _opt_chain_strike_bound(symbol)
    step = _infer_strike_step(chain, symbol)
    min_strike = spot - bound * step
    max_strike = spot + bound * step

    filtered: list[dict[str, Any]] = []
    for row in chain:
        if not isinstance(row, dict):
            continue
        strike = _row_strike_price(row)
        if strike is None:
            continue
        if min_strike <= strike <= max_strike:
            filtered.append(row)

    if not filtered:
        raise MarketDataError(
            f"No option chain rows within ±{bound} strikes of spot {spot:.2f} "
            f"for {symbol} (step={step})."
        )
    return filtered


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
    spot: float | None = None,
) -> OptionChainPcr:
    data = response.get("data")
    if not isinstance(data, dict):
        raise MarketDataError("Fyers option chain response missing 'data' object.")

    chain = data.get("optionsChain")
    if not isinstance(chain, list) or not chain:
        raise MarketDataError("Fyers option chain response missing 'optionsChain'.")

    if spot is not None:
        chain = _filter_chain_rows_near_spot(
            chain,
            spot=spot,
            symbol=symbol,
            strike_bound=_opt_chain_strike_bound(symbol),
        )

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


_OPTION_SUFFIX = re.compile(r"(\d+(?:\.\d+)?)(CE|PE)$", re.IGNORECASE)


def _row_has_broker_tag(row: dict[str, Any]) -> bool:
    for key in ("strategy", "tag", "productType"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _row_strategy_tag(row: dict[str, Any]) -> str:
    for key in ("strategy", "tag", "productType"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    raise UntaggedPositionError(f"Position row missing broker tag: {row.get('symbol')}")


def _row_underlying_key(row: dict[str, Any]) -> str:
    underlying = row.get("underlying") or row.get("underlying_symbol")
    if underlying not in (None, ""):
        return str(underlying).strip().upper()
    symbol = str(row.get("symbol", "")).strip().upper()
    body = symbol.split(":")[-1] if ":" in symbol else symbol
    if "FUT" in body:
        match = re.match(r"^([A-Z]+)", body)
        return match.group(1) if match else body
    match = re.match(r"^([A-Z]+)", body)
    return match.group(1) if match else body


def _row_expiry_key(row: dict[str, Any]) -> str:
    for key in ("expiry", "expiry_timestamp", "expiryDate"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    symbol = str(row.get("symbol", "")).strip().upper()
    body = symbol.split(":")[-1] if ":" in symbol else symbol
    match = re.match(r"^[A-Z]+(\d{2}[A-Z]{3}\d{2})", body)
    if match:
        return match.group(1)
    return body


def _row_option_type_and_strike(row: dict[str, Any]) -> tuple[str, float]:
    option_type = str(row.get("option_type", "")).upper()
    strike_raw = row.get("strike", row.get("strike_price"))
    if option_type in {"CE", "PE"} and strike_raw is not None:
        return option_type, float(strike_raw)

    symbol = str(row.get("symbol", "")).strip().upper()
    body = symbol.split(":")[-1] if ":" in symbol else symbol
    match = _OPTION_SUFFIX.search(body)
    if match:
        return match.group(2).upper(), float(match.group(1))

    raise UntaggedPositionError(f"Cannot parse option leg from row: {row.get('symbol')}")


def _infer_strategy_from_legs(rows: list[dict[str, Any]]) -> str:
    """Infer strategy from untagged broker legs grouped by underlying + expiry."""
    if not rows:
        raise UntaggedPositionError("No position rows to infer strategy from.")

    if any("FUT" in str(row.get("symbol", "")).upper() for row in rows):
        raise UntaggedPositionError(
            "Futures positions are excluded in v4.1 (catastrophic loss risk on ₹6L account)."
        )

    ce_strikes: list[float] = []
    pe_strikes: list[float] = []
    for row in rows:
        option_type, strike = _row_option_type_and_strike(row)
        if option_type == "CE":
            ce_strikes.append(strike)
        else:
            pe_strikes.append(strike)

    if len(ce_strikes) == 1 and len(pe_strikes) == 1:
        raise UntaggedPositionError(
            "1CE+1PE leg pattern (strangle/straddle) excluded in v4.1 (undefined risk)."
        )

    if len(ce_strikes) == 2 and len(pe_strikes) == 2:
        ce_sorted = sorted(ce_strikes)
        pe_sorted = sorted(pe_strikes)
        if (
            ce_sorted[0] < ce_sorted[1]
            and pe_sorted[0] < pe_sorted[1]
            and pe_sorted[1] < ce_sorted[0]
        ):
            return "iron_condor"
        raise UntaggedPositionError(
            f"Ambiguous 2CE+2PE leg pattern: CE={ce_strikes}, PE={pe_strikes}"
        )

    raise UntaggedPositionError(
        f"Cannot infer strategy from {len(ce_strikes)} CE and {len(pe_strikes)} PE legs."
    )


def _active_position_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("netPositions", "overall", "positionBook"):
        bucket = response.get(key)
        if isinstance(bucket, list):
            rows.extend(item for item in bucket if isinstance(item, dict))

    active: list[dict[str, Any]] = []
    for row in rows:
        qty_raw = row.get("netQty", row.get("qty", 0))
        try:
            qty = abs(int(qty_raw))
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        symbol = str(row.get("symbol", "")).strip()
        if not symbol:
            continue
        active.append(row)
    return active


def _row_leg_id(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol", "")).strip()
    if not symbol:
        raise UntaggedPositionError("Position row missing symbol for leg_id.")
    return symbol


def _row_strategy_id(
    row: dict[str, Any],
    *,
    untagged_group_strategies: dict[tuple[str, str], str],
) -> str:
    if _row_has_broker_tag(row):
        return _row_strategy_tag(row)

    symbol = str(row.get("symbol", "")).upper()
    if "FUT" in symbol:
        return _infer_strategy_from_legs([row])

    group_key = (_row_underlying_key(row), _row_expiry_key(row))
    strategy = untagged_group_strategies.get(group_key)
    if strategy is None:
        raise UntaggedPositionError(
            f"No inferred strategy_id for untagged leg {row.get('symbol')} in group {group_key}."
        )
    return strategy


def _strategy_for_row(
    row: dict[str, Any],
    *,
    untagged_group_strategies: dict[tuple[str, str], str],
) -> str:
    if _row_has_broker_tag(row):
        return _row_strategy_tag(row)

    symbol = str(row.get("symbol", "")).upper()
    if "FUT" in symbol:
        return _infer_strategy_from_legs([row])

    group_key = (_row_underlying_key(row), _row_expiry_key(row))
    strategy = untagged_group_strategies.get(group_key)
    if strategy is None:
        raise UntaggedPositionError(
            f"No inferred strategy for untagged leg {row.get('symbol')} in group {group_key}."
        )
    return strategy


def _parse_positions(response: dict[str, Any]) -> list[OpenPosition]:
    active_rows = _active_position_rows(response)

    untagged_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in active_rows:
        if _row_has_broker_tag(row):
            continue
        symbol = str(row.get("symbol", "")).upper()
        if "FUT" in symbol:
            continue
        group_key = (_row_underlying_key(row), _row_expiry_key(row))
        untagged_groups.setdefault(group_key, []).append(row)

    if len(untagged_groups) > 1:
        underlyings = {key[0] for key in untagged_groups}
        if len(underlyings) > 1:
            raise UntaggedPositionError(
                f"Mixed underlyings in untagged positions: {sorted(underlyings)}"
            )

    untagged_group_strategies: dict[tuple[str, str], str] = {}
    for group_key, group_rows in untagged_groups.items():
        untagged_group_strategies[group_key] = _infer_strategy_from_legs(group_rows)

    positions: list[OpenPosition] = []
    for row in active_rows:
        avg_price_raw = row.get("avgPrice", row.get("netAvg", row.get("buyAvg", 0)))
        try:
            entry_price = float(avg_price_raw)
        except (TypeError, ValueError):
            continue
        if entry_price <= 0:
            continue

        lots_raw = row.get("lotSize", row.get("lots", 1))
        try:
            lots = max(int(lots_raw), 1)
        except (TypeError, ValueError):
            lots = 1

        symbol = str(row.get("symbol", "")).strip()
        strategy = _strategy_for_row(
            row,
            untagged_group_strategies=untagged_group_strategies,
        )
        positions.append(
            OpenPosition(
                symbol=symbol,
                strategy=strategy,
                lots=lots,
                entry_price=entry_price,
                leg_id=_row_leg_id(row),
                strategy_id=_row_strategy_id(
                    row,
                    untagged_group_strategies=untagged_group_strategies,
                ),
            )
        )

    return positions


def _parse_bid_ask(response: dict[str, Any], symbol: str) -> Quote:
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

        bid = values.get("bid")
        ask = values.get("ask")
        ltp = None
        for key in ("lp", "last_price", "close"):
            if key in values and values[key] is not None:
                ltp = float(values[key])
                break

        if bid is None or ask is None or ltp is None:
            raise MarketDataError(f"Fyers quote missing bid/ask/ltp for {symbol}.")

        bid_f = float(bid)
        ask_f = float(ask)
        if bid_f <= 0 or ask_f <= 0 or ltp <= 0:
            raise MarketDataError(f"Fyers quote has non-positive prices for {symbol}.")

        spread_pct = (ask_f - bid_f) / ltp
        underlying = values.get("underlying_ltp") or values.get("underlying")
        underlying_ltp = float(underlying) if underlying is not None else None

        return Quote(
            symbol=symbol,
            bid=bid_f,
            ask=ask_f,
            ltp=ltp,
            spread_pct=spread_pct,
            underlying_ltp=underlying_ltp,
        )

    raise MarketDataError(f"Could not parse bid/ask for {symbol} from Fyers quotes.")


def _parse_positive_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0.0 else None


def _parse_finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_option_chain_quotes(
    response: dict[str, Any],
    *,
    symbol: str,
    expiry_ts: int,
    spot: float | None = None,
) -> list[OptionQuote]:
    data = response.get("data")
    if not isinstance(data, dict):
        raise MarketDataError("Fyers option chain response missing 'data' object.")

    chain = data.get("optionsChain")
    if not isinstance(chain, list) or not chain:
        raise MarketDataError("Fyers option chain response missing 'optionsChain'.")

    if spot is not None:
        chain = _filter_chain_rows_near_spot(
            chain,
            spot=spot,
            symbol=symbol,
            strike_bound=_opt_chain_strike_bound(symbol),
        )

    quotes: list[OptionQuote] = []
    for row in chain:
        if not isinstance(row, dict):
            continue

        option_type = str(row.get("option_type", "")).upper()
        if option_type not in {"CE", "PE"}:
            continue

        strike_raw = row.get("strike_price", row.get("strike"))
        if strike_raw is None:
            continue

        try:
            strike = float(strike_raw)
        except (TypeError, ValueError):
            continue

        bid = _parse_positive_float(row.get("bid"))
        ask = _parse_positive_float(row.get("ask"))
        ltp_raw = row.get("ltp", row.get("last_price"))
        ltp = _parse_positive_float(ltp_raw)
        if ltp is None and bid is not None and ask is not None:
            ltp = (bid + ask) / 2.0
        if ltp is None or ltp <= 0.0:
            continue

        oi_raw = row.get("oi")
        oi: int | None
        try:
            oi = int(oi_raw) if oi_raw is not None else None
        except (TypeError, ValueError):
            oi = None

        broker_delta = _parse_finite_float(row.get("delta"))
        broker_gamma = _parse_finite_float(row.get("gamma"))
        option_symbol = str(row.get("symbol", f"{symbol}:{strike}:{option_type}"))
        quotes.append(
            OptionQuote(
                symbol=option_symbol,
                strike=strike,
                option_type=option_type,
                bid=bid,
                ask=ask,
                ltp=ltp,
                oi=oi,
                broker_delta=broker_delta,
                broker_gamma=broker_gamma,
            )
        )

    if not quotes:
        raise MarketDataError(
            f"Fyers option chain returned no quotes for {symbol} expiry {expiry_ts}."
        )
    return quotes


def _enrich_with_local_greeks(
    quotes: list[OptionQuote],
    *,
    spot: float,
    expiry_ts: int,
    config: RiskConfig,
) -> list[OptionGreeks]:
    dte_days = compute_dte_from_expiry_timestamp(expiry_ts)
    greeks: list[OptionGreeks] = []

    for quote in quotes:
        market = compute_greeks_from_market(
            spot=spot,
            strike=quote.strike,
            dte_days=dte_days,
            r=config.risk_free_rate,
            q=config.dividend_yield,
            option_ltp=quote.ltp,
            bid=quote.bid,
            ask=quote.ask,
            oi=quote.oi,
            is_call=quote.option_type == "CE",
            price_side=config.greeks_price_side,
            iv_max_iter=config.iv_solver_max_iter,
            iv_tol=config.iv_tolerance,
        )
        if quote.broker_delta is not None and quote.broker_gamma is not None:
            logger.debug(
                "greeks_compare symbol=%s local_delta=%.4f broker_delta=%.4f "
                "local_gamma=%.6f broker_gamma=%.6f iv=%s",
                quote.symbol,
                market.delta,
                quote.broker_delta,
                market.gamma,
                quote.broker_gamma,
                market.iv,
            )
        greeks.append(
            OptionGreeks(
                symbol=quote.symbol,
                strike=quote.strike,
                option_type=quote.option_type,
                delta=market.delta,
                gamma=market.gamma,
                confidence=market.confidence,
            )
        )

    return greeks


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
        self._risk_config: RiskConfig | None = None

    def bind_risk_config(self, config: RiskConfig) -> None:
        self._risk_config = config

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

    async def get_index_ltp(self, symbol: str) -> float:
        payload = {"symbols": symbol}

        def _fetch() -> float:
            client = self._get_client()
            response = _assert_fyers_ok(
                client.quotes(payload),
                context=f"quotes({symbol})",
            )
            return _extract_quote_last_price(response, symbol)

        return await self._call_with_timeout(_fetch, context=f"get_index_ltp({symbol})")

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
        strikecount: int | None = None,
    ) -> OptionChainPcr:
        spot = await self.get_index_ltp(symbol)
        payload = _build_option_chain_payload(symbol, timestamp="")
        if strikecount is not None:
            payload["strikecount"] = min(
                strikecount,
                _opt_chain_strike_bound(symbol),
            )

        def _fetch() -> OptionChainPcr:
            client = self._get_client()
            response = _assert_fyers_ok(
                client.optionchain(payload),
                context=f"optionchain({symbol})",
            )
            return _parse_option_chain_pcr(response, symbol=symbol, spot=spot)

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

    async def get_positions(self) -> list[OpenPosition]:
        def _fetch() -> list[OpenPosition]:
            client = self._get_client()
            response = _assert_fyers_ok(client.positions(), context="positions")
            return _parse_positions(response)

        return await self._call_with_timeout(_fetch, context="get_positions")

    async def get_option_chain_greeks(
        self,
        symbol: str,
        expiry_ts: int,
    ) -> list[OptionGreeks]:
        spot = await self.get_index_ltp(symbol)
        payload = _build_option_chain_payload(symbol, timestamp=str(expiry_ts))

        def _fetch_chain() -> dict[str, Any]:
            client = self._get_client()
            return _assert_fyers_ok(
                client.optionchain(payload),
                context=f"optionchain_greeks({symbol})",
            )

        response = await self._call_with_timeout(
            _fetch_chain,
            context=f"get_option_chain_greeks({symbol})",
        )
        quotes = _parse_option_chain_quotes(
            response,
            symbol=symbol,
            expiry_ts=expiry_ts,
            spot=spot,
        )
        config = self._risk_config or load_risk_config()
        return _enrich_with_local_greeks(
            quotes,
            spot=spot,
            expiry_ts=expiry_ts,
            config=config,
        )

    async def get_bid_ask(self, symbol: str) -> Quote:
        payload = {"symbols": symbol}

        def _fetch() -> Quote:
            client = self._get_client()
            response = _assert_fyers_ok(
                client.quotes(payload),
                context=f"quotes({symbol})",
            )
            return _parse_bid_ask(response, symbol)

        return await self._call_with_timeout(_fetch, context=f"get_bid_ask({symbol})")
