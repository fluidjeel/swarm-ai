"""Regime-oriented feature metrics built on top of MarketDataProvider."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Final, TypedDict

from src.data.base_provider import MarketDataError, MarketDataProvider, MarketDataTimeoutError

DEFAULT_VIX_HISTORY_SYMBOL: Final = "NSE:INDIAVIX-INDEX"
DEFAULT_OPTION_SYMBOL: Final = "NSE:NIFTY50-INDEX"
DEFAULT_REQUEST_TIMEOUT_SEC: Final = 15.0
VIX_TREND_THRESHOLD: Final = 0.15


class RegimeMetrics(TypedDict):
    current_vix: float
    pcr: float
    vix_trend: str


class RegimeMetricsError(RuntimeError):
    """Raised when regime metrics cannot be computed safely."""


def _derive_vix_trend(vix_bars: list[dict[str, float | int]]) -> str:
    if len(vix_bars) < 2:
        return "FLAT"

    previous_close = float(vix_bars[-2]["close"])
    latest_close = float(vix_bars[-1]["close"])
    delta = latest_close - previous_close

    if delta > VIX_TREND_THRESHOLD:
        return "UP"
    if delta < -VIX_TREND_THRESHOLD:
        return "DOWN"
    return "FLAT"


async def compute_regime_metrics(
    provider: MarketDataProvider,
    *,
    option_symbol: str = DEFAULT_OPTION_SYMBOL,
    request_timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC,
) -> RegimeMetrics:
    """
    Build a strict JSON-ready regime metrics payload.

    Returns:
        {"current_vix": float, "pcr": float, "vix_trend": str}
    """
    try:
        vix_task = asyncio.create_task(provider.get_vix())
        pcr_task = asyncio.create_task(provider.get_option_chain_pcr(option_symbol))
        vix_history_task = asyncio.create_task(
            provider.get_index_ohlcv(
                DEFAULT_VIX_HISTORY_SYMBOL,
                resolution="D",
                lookback_bars=5,
            )
        )

        results = await asyncio.wait_for(
            asyncio.gather(vix_task, pcr_task, vix_history_task, return_exceptions=True),
            timeout=request_timeout_sec,
        )
    except TimeoutError as exc:
        raise RegimeMetricsError(
            f"Regime metrics timed out after {request_timeout_sec:.1f}s"
        ) from exc

    current_vix_result, pcr_result, vix_history_result = results

    errors: list[str] = []
    if isinstance(current_vix_result, Exception):
        errors.append(f"current_vix: {current_vix_result}")
    if isinstance(pcr_result, Exception):
        errors.append(f"pcr: {pcr_result}")
    if isinstance(vix_history_result, Exception):
        errors.append(f"vix_trend: {vix_history_result}")

    if errors:
        if any(
            isinstance(result, (MarketDataTimeoutError, TimeoutError, asyncio.TimeoutError))
            for result in (current_vix_result, pcr_result, vix_history_result)
        ):
            raise RegimeMetricsError(
                "Regime metrics failed due to API timeout: " + "; ".join(errors)
            )
        if any(isinstance(result, MarketDataError) for result in results):
            raise RegimeMetricsError(
                "Regime metrics failed due to market data error: " + "; ".join(errors)
            )
        raise RegimeMetricsError("Regime metrics failed: " + "; ".join(errors))

    current_vix = float(current_vix_result)  # type: ignore[arg-type]
    pcr = float(pcr_result.pcr)  # type: ignore[union-attr]
    vix_trend = _derive_vix_trend(vix_history_result)  # type: ignore[arg-type]

    if current_vix < 0:
        raise RegimeMetricsError(f"Invalid VIX value: {current_vix}")
    if pcr < 0:
        raise RegimeMetricsError(f"Invalid PCR value: {pcr}")
    if vix_trend not in {"UP", "DOWN", "FLAT"}:
        raise RegimeMetricsError(f"Invalid vix_trend label: {vix_trend}")

    return {
        "current_vix": round(current_vix, 4),
        "pcr": round(pcr, 4),
        "vix_trend": vix_trend,
    }


class PollSummary(TypedDict):
    successful_ticks: int
    failed_ticks: int
    elapsed_secs: float


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rss_bytes() -> int | None:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "win32":
            return int(usage)
        return int(usage) * 1024
    except Exception:
        return None


def _build_tick_payload(
    metrics: RegimeMetrics,
    *,
    log_memory: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "captured_at_iso": _utc_now_iso(),
        "metrics": metrics,
    }
    if log_memory:
        rss = _rss_bytes()
        if rss is not None:
            payload["rss_bytes"] = rss
    return payload


async def poll_regime_metrics(
    provider: MarketDataProvider,
    *,
    interval_secs: int = 300,
    duration_secs: float | None = None,
    option_symbol: str = DEFAULT_OPTION_SYMBOL,
    request_timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC,
    log_memory: bool = False,
    on_tick: Callable[[dict[str, Any]], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> PollSummary:
    """
    Poll regime metrics on a fixed cadence.

    Designed for Epic 2.1 soak tests (e.g. every 5 minutes for 4 hours).
    Errors on individual ticks are logged and do not stop the loop.
    """
    if interval_secs < 1:
        raise ValueError("interval_secs must be >= 1")

    started = time.monotonic()
    successful_ticks = 0
    failed_ticks = 0

    while True:
        tick_started = time.monotonic()
        try:
            metrics = await compute_regime_metrics(
                provider,
                option_symbol=option_symbol,
                request_timeout_sec=request_timeout_sec,
            )
            payload = _build_tick_payload(metrics, log_memory=log_memory)
            successful_ticks += 1
            if on_tick is not None:
                on_tick(payload)
        except RegimeMetricsError as exc:
            failed_ticks += 1
            message = str(exc)
            if on_error is not None:
                on_error(message)
            else:
                print(f"WARN: {message}", flush=True)

        elapsed = time.monotonic() - started
        if duration_secs is not None and elapsed >= duration_secs:
            break

        sleep_for = max(0.0, interval_secs - (time.monotonic() - tick_started))
        await asyncio.sleep(sleep_for)

    return {
        "successful_ticks": successful_ticks,
        "failed_ticks": failed_ticks,
        "elapsed_secs": time.monotonic() - started,
    }
