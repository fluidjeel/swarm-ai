"""
Security sanitizer middleware for Feature Engine → LLM boundary.

Blocks anomalous numeric market data and truncates text fields before agent prompts.

Reference: .context/02_hldd.md §1.5
"""

from __future__ import annotations

import math
from typing import Any

DEFAULT_TEXT_MAX_LENGTH = 512

# Canonical bounds for institutional feature payload fields.
NUMERIC_BOUNDS: dict[str, tuple[float, float]] = {
    "vix": (0.0, 100.0),
    "india_vix": (0.0, 100.0),
    "nifty_ad_ratio": (0.0, 50.0),
    "nifty_500_ad_ratio": (0.0, 50.0),
    "vix_atr_divergence": (-100.0, 100.0),
    "expiry_weighted_pcr_momentum": (-10.0, 10.0),
    "gift_nifty_change_pct": (-25.0, 25.0),
    "fii_net_cr": (-100_000.0, 100_000.0),
    "dii_net_cr": (-100_000.0, 100_000.0),
    "dte": (0.0, 45.0),
}

# Accept HLDD-style keys and normalize to canonical bounds keys.
FIELD_ALIASES: dict[str, str] = {
    "VIX": "vix",
    "India_VIX": "vix",
    "NIFTY_500_AD_Ratio": "nifty_500_ad_ratio",
    "VIX_ATR_Divergence": "vix_atr_divergence",
    "Expiry_Weighted_PCR_Momentum": "expiry_weighted_pcr_momentum",
}

TEXT_FIELDS = frozenset(
    {
        "bias",
        "captured_at_iso",
        "macro_event",
        "notes",
        "headline",
    }
)


class SanitizerError(ValueError):
    """Raised when market data fails security validation."""


def _is_invalid_number(value: float) -> bool:
    return not math.isfinite(value)


def validate_numeric(field: str, value: int | float, *, bounds: tuple[float, float] | None = None) -> float:
    """
    Validate a numeric market field against configured or explicit bounds.

    Raises SanitizerError when value is NaN/inf or outside allowed range.
    """
    canonical = FIELD_ALIASES.get(field, field)
    limits = bounds or NUMERIC_BOUNDS.get(canonical)
    if limits is None:
        raise SanitizerError(f"No numeric bounds configured for field '{field}'")

    numeric = float(value)
    if _is_invalid_number(numeric):
        raise SanitizerError(f"Field '{field}' must be a finite number (got {value!r})")

    low, high = limits
    if numeric < low or numeric > high:
        raise SanitizerError(
            f"Field '{field}' out of bounds: {numeric} not in [{low}, {high}]"
        )
    return numeric


def sanitize_text(value: str, max_length: int = DEFAULT_TEXT_MAX_LENGTH) -> str:
    """Truncate free-text fields to reduce prompt-injection surface area."""
    if not isinstance(value, str):
        raise SanitizerError(f"Text field must be str (got {type(value).__name__})")
    cleaned = value.replace("\x00", "").strip()
    if len(cleaned) > max_length:
        return cleaned[:max_length]
    return cleaned


def _canonical_field_name(field: str) -> str:
    return FIELD_ALIASES.get(field, field)


def sanitize_feature_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Validate and return a sanitized copy of a Feature Engine payload.

    Unknown keys are dropped to avoid unexpected prompt injection via extra fields.
    """
    if not isinstance(payload, dict):
        raise SanitizerError("Feature payload must be a dict")

    sanitized: dict[str, Any] = {}
    for field, value in payload.items():
        canonical = _canonical_field_name(field)

        if value is None:
            sanitized[field] = None
            continue

        if canonical in NUMERIC_BOUNDS:
            if isinstance(value, bool):
                raise SanitizerError(f"Field '{field}' must be numeric (got bool)")
            if not isinstance(value, (int, float)):
                raise SanitizerError(
                    f"Field '{field}' must be numeric (got {type(value).__name__})"
                )
            sanitized[field] = validate_numeric(field, value)
            continue

        if field in TEXT_FIELDS or canonical in TEXT_FIELDS:
            sanitized[field] = sanitize_text(str(value))
            continue

        if isinstance(value, list) and field == "macro_events":
            sanitized[field] = [sanitize_text(str(item)) for item in value]
            continue

        # Drop unknown fields — do not forward arbitrary keys into LLM prompts.

    return sanitized


def sanitize_market_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Sanitize nested market payloads (feature blocks + optional text context).
    """
    if not isinstance(payload, dict):
        raise SanitizerError("Market payload must be a dict")

    result: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            result[key] = sanitize_feature_payload(value)
        elif isinstance(value, list) and key == "macro_events":
            result[key] = [sanitize_text(str(item)) for item in value]
        elif key in TEXT_FIELDS and isinstance(value, str):
            result[key] = sanitize_text(value)
        elif _canonical_field_name(key) in NUMERIC_BOUNDS:
            if isinstance(value, bool):
                raise SanitizerError(f"Field '{key}' must be numeric (got bool)")
            if not isinstance(value, (int, float)):
                raise SanitizerError(
                    f"Field '{key}' must be numeric (got {type(value).__name__})"
                )
            result[key] = validate_numeric(key, value)
        # Drop unknown top-level keys from nested market envelopes.
    return result
