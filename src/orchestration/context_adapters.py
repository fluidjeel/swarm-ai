"""Adapters between Feature Engine payloads and AgentContext (HLDD §1.1)."""

from __future__ import annotations

from typing import Any

from src.core.context import SESSION_CIRCUIT_BREAKER_PNL, AgentContext, OpeningRegime
from src.features.feature_engine import FeaturePayload, to_opening_regime


def apply_feature_payload(
    ctx: AgentContext,
    payload: FeaturePayload,
    *,
    captured_at_iso: str | None = None,
    feature_snapshot_price: float | None = None,
) -> AgentContext:
    """Merge a sanitized feature payload into session context."""
    updates: dict[str, object] = {
        "opening_regime": to_opening_regime(payload, captured_at_iso=captured_at_iso),
        "dte": int(payload["dte"]),
        "data_degraded": False,
    }
    if feature_snapshot_price is not None:
        updates["feature_snapshot_price"] = float(feature_snapshot_price)
    return ctx.update(**updates)


def opening_regime_to_feature_payload(ctx: AgentContext) -> dict[str, Any]:
    """
    Canonical feature view for gatekeeper, exit engine, sanitizer, and agent prompts.

    Maps AgentContext.opening_regime + ctx.dte into the HLDD eval-compatible field names.
    """
    regime = ctx.opening_regime
    payload: dict[str, Any] = {"dte": ctx.dte}

    if regime.nifty_ad_ratio is not None:
        payload["NIFTY_500_AD_Ratio"] = regime.nifty_ad_ratio
    if regime.vix is not None:
        payload["vix"] = regime.vix
    if regime.vix_atr_divergence is not None:
        payload["VIX_ATR_Divergence"] = regime.vix_atr_divergence
    if regime.expiry_weighted_pcr_momentum is not None:
        payload["Expiry_Weighted_PCR_Momentum"] = regime.expiry_weighted_pcr_momentum

    return payload


def feature_payload_from_opening_regime(
    regime: OpeningRegime,
    *,
    dte: int,
) -> dict[str, Any]:
    """Build a feature payload dict from an OpeningRegime snapshot and DTE."""
    ctx = AgentContext(session_id="adapter-synthetic", opening_regime=regime, dte=dte)
    return opening_regime_to_feature_payload(ctx)


def sync_circuit_breaker(ctx: AgentContext) -> AgentContext:
    """Align circuit_status with daily_pnl per HLDD §2.2."""
    tripped = ctx.daily_pnl <= SESSION_CIRCUIT_BREAKER_PNL
    if ctx.circuit_status == tripped:
        return ctx
    return ctx.update(circuit_status=tripped)
