"""Full Feature Engine payload for Agent 1 and sanitizer boundary."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, TypedDict

from src.core.context import OpeningRegime
from src.data.base_provider import MarketDataError, MarketDataProvider, MarketDataTimeoutError
from src.features.math_utils import (
    compute_dte_from_expiry_timestamp,
    compute_expiry_weighted_pcr_momentum,
    compute_vix_atr_divergence,
)
from src.features.pcr_history import (
    find_pcr_near_hours_ago,
    load_pcr_history,
    save_pcr_snapshot,
)
from src.security.sanitizer import SanitizerError, sanitize_feature_payload

DEFAULT_OPTION_SYMBOL: Final = "NSE:NIFTY50-INDEX"
DEFAULT_NIFTY_SYMBOL: Final = "NSE:NIFTY50-INDEX"
DEFAULT_VIX_HISTORY_SYMBOL: Final = "NSE:INDIAVIX-INDEX"
DEFAULT_REQUEST_TIMEOUT_SEC: Final = 30.0


class FeaturePayload(TypedDict):
    NIFTY_500_AD_Ratio: float
    vix: float
    VIX_ATR_Divergence: float
    Expiry_Weighted_PCR_Momentum: float
    dte: int


class FeatureEngineError(RuntimeError):
    """Raised when the feature engine cannot produce a safe payload."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_opening_regime(payload: FeaturePayload, *, captured_at_iso: str | None = None) -> OpeningRegime:
    return OpeningRegime(
        nifty_ad_ratio=payload["NIFTY_500_AD_Ratio"],
        vix=payload["vix"],
        vix_atr_divergence=payload["VIX_ATR_Divergence"],
        expiry_weighted_pcr_momentum=payload["Expiry_Weighted_PCR_Momentum"],
        captured_at_iso=captured_at_iso or _utc_now_iso(),
    )


async def compute_feature_payload(
    provider: MarketDataProvider,
    *,
    option_symbol: str = DEFAULT_OPTION_SYMBOL,
    nifty_symbol: str = DEFAULT_NIFTY_SYMBOL,
    request_timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC,
    pcr_history_path: Path | None = None,
) -> FeaturePayload:
    """Build the eval-compatible feature payload and validate via sanitizer."""
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                provider.get_vix(),
                provider.get_option_chain_pcr(option_symbol),
                provider.get_nifty50_ad_ratio(),
                provider.get_index_ohlcv(nifty_symbol, resolution="5", lookback_bars=20),
                provider.get_index_ohlcv(DEFAULT_VIX_HISTORY_SYMBOL, resolution="D", lookback_bars=5),
                return_exceptions=True,
            ),
            timeout=request_timeout_sec,
        )
    except TimeoutError as exc:
        raise FeatureEngineError(
            f"Feature engine timed out after {request_timeout_sec:.1f}s"
        ) from exc

    labels = ("vix", "pcr", "breadth", "nifty_ohlcv", "vix_history")
    errors: list[str] = []
    for label, result in zip(labels, results, strict=True):
        if isinstance(result, Exception):
            errors.append(f"{label}: {result}")

    if errors:
        if any(isinstance(r, (MarketDataTimeoutError, TimeoutError)) for r in results):
            raise FeatureEngineError("Feature engine failed due to API timeout: " + "; ".join(errors))
        if any(isinstance(r, MarketDataError) for r in results):
            raise FeatureEngineError("Feature engine failed due to market data error: " + "; ".join(errors))
        raise FeatureEngineError("Feature engine failed: " + "; ".join(errors))

    current_vix = float(results[0])  # type: ignore[arg-type]
    pcr_snapshot = results[1]  # type: ignore[assignment]
    breadth = results[2]  # type: ignore[assignment]
    nifty_bars = results[3]  # type: ignore[assignment]
    vix_bars = results[4]  # type: ignore[assignment]

    previous_vix = float(vix_bars[-2]["close"]) if len(vix_bars) >= 2 else current_vix
    dte = compute_dte_from_expiry_timestamp(pcr_snapshot.expiry_timestamp)

    history = load_pcr_history(pcr_history_path) if pcr_history_path else load_pcr_history()
    prior_pcr = find_pcr_near_hours_ago(history, hours=2)
    pcr_momentum = compute_expiry_weighted_pcr_momentum(
        current_pcr=pcr_snapshot.pcr,
        prior_pcr=prior_pcr,
        dte=dte,
    )
    save_kwargs = {"path": pcr_history_path} if pcr_history_path else {}
    save_pcr_snapshot(pcr_snapshot.pcr, **save_kwargs)

    payload: dict[str, Any] = {
        "NIFTY_500_AD_Ratio": round(breadth.ad_ratio, 4),
        "vix": round(current_vix, 4),
        "VIX_ATR_Divergence": round(
            compute_vix_atr_divergence(
                current_vix=current_vix,
                previous_vix=previous_vix,
                nifty_bars=nifty_bars,
            ),
            4,
        ),
        "Expiry_Weighted_PCR_Momentum": round(pcr_momentum, 4),
        "dte": int(dte),
    }

    try:
        sanitized = sanitize_feature_payload(payload)
    except SanitizerError as exc:
        raise FeatureEngineError(f"Sanitizer rejected feature payload: {exc}") from exc

    return FeaturePayload(
        NIFTY_500_AD_Ratio=float(sanitized["NIFTY_500_AD_Ratio"]),
        vix=float(sanitized["vix"]),
        VIX_ATR_Divergence=float(sanitized["VIX_ATR_Divergence"]),
        Expiry_Weighted_PCR_Momentum=float(sanitized["Expiry_Weighted_PCR_Momentum"]),
        dte=int(sanitized["dte"]),
    )


class PollSummary(TypedDict):
    successful_ticks: int
    failed_ticks: int
    elapsed_secs: float


async def poll_feature_payload(
    provider: MarketDataProvider,
    *,
    interval_secs: int = 300,
    duration_secs: float | None = None,
    request_timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC,
    log_memory: bool = False,
    on_tick: Any | None = None,
    on_error: Any | None = None,
) -> PollSummary:
    """Poll full feature payload on a fixed cadence for soak tests."""
    import sys
    import time

    if interval_secs < 1:
        raise ValueError("interval_secs must be >= 1")

    started = time.monotonic()
    successful_ticks = 0
    failed_ticks = 0

    while True:
        tick_started = time.monotonic()
        try:
            payload = await compute_feature_payload(
                provider,
                request_timeout_sec=request_timeout_sec,
            )
            tick: dict[str, Any] = {
                "captured_at_iso": _utc_now_iso(),
                "features": payload,
            }
            if log_memory:
                try:
                    import resource

                    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    tick["rss_bytes"] = int(usage) if sys.platform == "win32" else int(usage) * 1024
                except Exception:
                    pass
            successful_ticks += 1
            if on_tick is not None:
                on_tick(tick)
        except FeatureEngineError as exc:
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
