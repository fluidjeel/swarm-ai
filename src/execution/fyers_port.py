"""Fyers broker execution port (Phase 4.2)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any, Callable, TypeVar

from src.core.context import OpenPosition, StrategyName
from src.data.base_provider import FyersAuthError, MarketDataError, MarketDataTimeoutError
from src.execution.port import (
    CancelAck,
    ExecutionFailedError,
    ExecutionPort,
    LegActionIntent,
    OrderAck,
    OrderRow,
    OrderSide,
    PortHealth,
    idem_key,
)
from src.orchestration.session_clock import IST

logger = logging.getLogger("a2a.execution.fyers")

T = TypeVar("T")

FYERS_ORDER_MARKET = 2
FYERS_SIDE_BUY = 1
FYERS_SIDE_SELL = -1
FYERS_PRODUCT_TYPE = "MARGIN"
NIFTY_LOT_SIZE = 50

_FLATTEN_LEG_SIDES: dict[StrategyName, tuple[OrderSide, ...]] = {
    StrategyName.IRON_CONDOR: ("SELL", "BUY", "BUY", "SELL"),
    StrategyName.BULL_CALL_SPREAD: ("SELL", "BUY"),
    StrategyName.BEAR_PUT_SPREAD: ("SELL", "BUY"),
}

_ACTIVE_ORDER_STATUSES = frozenset({1, 2, 3, 4, 5, 6})


def _require_fyers_credentials() -> tuple[str, str]:
    app_id = os.getenv("FYERS_APP_ID", "").strip()
    access_token = os.getenv("FYERS_ACCESS_TOKEN", "").strip()
    if not app_id or not access_token:
        raise MarketDataError(
            "FYERS_APP_ID and FYERS_ACCESS_TOKEN must be set in the environment."
        )
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


def _fyers_side_to_order_side(side: int) -> OrderSide:
    return "BUY" if side == FYERS_SIDE_BUY else "SELL"


def _order_side_to_fyers(side: OrderSide) -> int:
    return FYERS_SIDE_BUY if side == "BUY" else FYERS_SIDE_SELL


def _extract_order_tag(row: dict[str, Any]) -> str:
    for key in ("orderTag", "ord_tag", "tag"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _parse_orderbook_rows(response: dict[str, Any]) -> list[OrderRow]:
    book = response.get("orderBook")
    if book is None:
        book = response.get("orderbook")
    if not isinstance(book, list):
        return []

    rows: list[OrderRow] = []
    for item in book:
        if not isinstance(item, dict):
            continue
        order_id = str(item.get("id", item.get("order_id", ""))).strip()
        symbol = str(item.get("symbol", "")).strip()
        if not order_id or not symbol:
            continue
        side_raw = item.get("side", FYERS_SIDE_BUY)
        try:
            side = _fyers_side_to_order_side(int(side_raw))
        except (TypeError, ValueError):
            side = "BUY"
        qty_raw = item.get("qty", item.get("remainingQty", 0))
        try:
            qty = int(qty_raw)
        except (TypeError, ValueError):
            qty = 0
        tag = _extract_order_tag(item)
        status_raw = item.get("status", item.get("orderStatus", "unknown"))
        rows.append(
            OrderRow(
                order_id=order_id,
                leg_id=symbol,
                symbol=symbol,
                side=side,
                qty=qty,
                tag=tag,
                status=str(status_raw),
            )
        )
    return rows


def _is_transient_broker_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "504" in message or "502" in message or "broker unavailable" in message


class FyersExecutionPort(ExecutionPort):
    """Live Fyers order I/O with orderbook idempotency."""

    def __init__(
        self,
        *,
        app_id: str | None = None,
        access_token: str | None = None,
        request_timeout_sec: float = 15.0,
        log_path: str = "",
        product_type: str = FYERS_PRODUCT_TYPE,
    ) -> None:
        if app_id and access_token:
            self._app_id = app_id
            self._access_token = access_token
        else:
            self._app_id, self._access_token = _require_fyers_credentials()
        self._request_timeout_sec = request_timeout_sec
        self._log_path = log_path
        self._product_type = product_type
        self._client: Any | None = None
        self._last_error: str | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from fyers_apiv3 import fyersModel
        except ImportError as exc:
            raise MarketDataError(
                "fyers-apiv3 is not installed. Run: pip install fyers-apiv3"
            ) from exc

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
            self._last_error = f"{context} timed out"
            raise MarketDataTimeoutError(
                f"{context} timed out after {self._request_timeout_sec:.1f}s"
            ) from exc
        except (MarketDataError, FyersAuthError):
            raise
        except Exception as exc:
            self._last_error = str(exc)
            raise MarketDataError(f"{context} failed: {exc}") from exc

    async def _fetch_orderbook_raw(self) -> list[OrderRow]:
        def _fetch() -> list[OrderRow]:
            client = self._get_client()
            response = _assert_fyers_ok(client.orderbook(), context="orderbook")
            return _parse_orderbook_rows(response)

        return await self._call_with_timeout(_fetch, context="get_orderbook")

    async def get_orderbook(self) -> list[OrderRow]:
        return await self._fetch_orderbook_raw()

    async def _find_order_by_tag(self, tag: str) -> OrderRow | None:
        if not tag:
            return None
        for row in await self._fetch_orderbook_raw():
            if row.tag == tag:
                return row
        return None

    def _build_place_order_payload(self, intent: LegActionIntent) -> dict[str, Any]:
        return {
            "symbol": intent.symbol,
            "qty": intent.qty,
            "type": FYERS_ORDER_MARKET,
            "side": _order_side_to_fyers(intent.side),
            "productType": self._product_type,
            "limitPrice": 0,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "stopLoss": 0,
            "takeProfit": 0,
            "orderTag": intent.tag,
        }

    def _ack_from_existing(self, intent: LegActionIntent, existing: OrderRow) -> OrderAck:
        return OrderAck(
            leg_id=intent.leg_id,
            order_id=existing.order_id,
            status="DEFERRED",
            reason="DUPLICATE_TAG",
            submitted_at=datetime.now(IST),
        )

    async def _place_order_once(self, intent: LegActionIntent) -> OrderAck:
        payload = self._build_place_order_payload(intent)

        def _place() -> OrderAck:
            client = self._get_client()
            response = _assert_fyers_ok(
                client.place_order(payload),
                context=f"place_order({intent.symbol})",
            )
            order_id = response.get("id")
            if order_id is None and isinstance(response.get("data"), dict):
                order_id = response["data"].get("id")
            if order_id is None:
                raise MarketDataError(
                    f"place_order missing order id for {intent.symbol}: {response}"
                )
            return OrderAck(
                leg_id=intent.leg_id,
                order_id=str(order_id),
                status="ACCEPTED",
                reason=None,
                submitted_at=datetime.now(IST),
            )

        return await self._call_with_timeout(_place, context=f"submit_legs({intent.symbol})")

    async def _submit_legs_impl(self, intent: LegActionIntent) -> OrderAck:
        existing = await self._find_order_by_tag(intent.tag)
        if existing is not None:
            logger.info(
                "Idempotent skip: tag=%s order_id=%s symbol=%s",
                intent.tag,
                existing.order_id,
                intent.symbol,
            )
            return self._ack_from_existing(intent, existing)

        try:
            return await self._place_order_once(intent)
        except MarketDataError as exc:
            if not _is_transient_broker_error(exc):
                return OrderAck(
                    leg_id=intent.leg_id,
                    order_id=None,
                    status="REJECTED",
                    reason=str(exc),
                    submitted_at=datetime.now(IST),
                )

            logger.warning(
                "Transient broker error for %s; re-querying orderbook before retry: %s",
                intent.symbol,
                exc,
            )
            existing = await self._find_order_by_tag(intent.tag)
            if existing is not None:
                return OrderAck(
                    leg_id=intent.leg_id,
                    order_id=existing.order_id,
                    status="ACCEPTED",
                    reason="RECOVERED_AFTER_TRANSIENT",
                    submitted_at=datetime.now(IST),
                )

            try:
                return await self._place_order_once(intent)
            except MarketDataError as retry_exc:
                existing = await self._find_order_by_tag(intent.tag)
                if existing is not None:
                    return OrderAck(
                        leg_id=intent.leg_id,
                        order_id=existing.order_id,
                        status="ACCEPTED",
                        reason="RECOVERED_AFTER_RETRY",
                        submitted_at=datetime.now(IST),
                    )
                return OrderAck(
                    leg_id=intent.leg_id,
                    order_id=None,
                    status="REJECTED",
                    reason=str(retry_exc),
                    submitted_at=datetime.now(IST),
                )

    async def _flatten_position_impl(self, position: OpenPosition) -> None:
        legs = list(position.legs or [position])
        flatten_sides = _FLATTEN_LEG_SIDES.get(position.strategy)
        if flatten_sides is None or len(flatten_sides) != len(legs):
            raise ExecutionFailedError(
                f"Cannot flatten unsupported strategy layout: {position.strategy}"
            )

        qty = max(position.lots, 1) * NIFTY_LOT_SIZE
        tick_timestamp = datetime.now(IST).isoformat()
        for leg, side in zip(legs, flatten_sides, strict=True):
            intent = LegActionIntent(
                leg_id=leg.leg_id or leg.symbol,
                symbol=leg.symbol,
                side=side,
                qty=qty,
                tag=idem_key(
                    tick_timestamp=tick_timestamp,
                    leg_id=f"flatten-{leg.symbol}",
                    symbol=leg.symbol,
                    side=side,
                ),
            )
            ack = await self._submit_legs_impl(intent)
            if ack.status == "REJECTED":
                raise ExecutionFailedError(
                    f"flatten leg rejected for {leg.symbol}: {ack.reason}"
                )

    async def cancel_order(self, order_id: str) -> CancelAck:
        def _cancel() -> CancelAck:
            client = self._get_client()
            response = _assert_fyers_ok(
                client.cancel_order({"id": order_id}),
                context=f"cancel_order({order_id})",
            )
            status = "ACCEPTED" if response.get("s") == "ok" else "REJECTED"
            return CancelAck(
                order_id=order_id,
                status=status,  # type: ignore[arg-type]
                reason=response.get("message"),
            )

        return await self._call_with_timeout(_cancel, context=f"cancel_order({order_id})")

    async def health_check(self) -> PortHealth:
        start = time.perf_counter()
        try:
            await self._fetch_orderbook_raw()
        except Exception as exc:
            latency_ms = max(1, int((time.perf_counter() - start) * 1000))
            self._last_error = str(exc)
            return PortHealth(ok=False, latency_ms=latency_ms, last_error=str(exc))
        latency_ms = max(1, int((time.perf_counter() - start) * 1000))
        return PortHealth(ok=True, latency_ms=latency_ms, last_error=None)
