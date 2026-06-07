"""Tests for AgentContext ↔ Feature Engine adapters."""

from __future__ import annotations

import unittest

from src.core.context import AgentContext, OpeningRegime, SESSION_CIRCUIT_BREAKER_PNL
from src.features.feature_engine import FeaturePayload
from src.orchestration.context_adapters import (
    apply_feature_payload,
    feature_payload_from_opening_regime,
    opening_regime_to_feature_payload,
    sync_circuit_breaker,
)


class ContextAdapterTests(unittest.TestCase):
    def test_apply_feature_payload_updates_regime_and_dte(self) -> None:
        ctx = AgentContext(session_id="test-session-001")
        payload: FeaturePayload = {
            "NIFTY_500_AD_Ratio": 1.25,
            "vix": 15.5,
            "VIX_ATR_Divergence": 0.12,
            "Expiry_Weighted_PCR_Momentum": 0.03,
            "dte": 6,
        }

        updated = apply_feature_payload(
            ctx,
            payload,
            captured_at_iso="2026-06-06T09:15:00+00:00",
        )

        self.assertEqual(updated.dte, 6)
        self.assertEqual(updated.opening_regime.nifty_ad_ratio, 1.25)
        self.assertEqual(updated.opening_regime.vix, 15.5)
        self.assertEqual(updated.opening_regime.vix_atr_divergence, 0.12)
        self.assertEqual(updated.opening_regime.expiry_weighted_pcr_momentum, 0.03)
        self.assertEqual(updated.opening_regime.captured_at_iso, "2026-06-06T09:15:00+00:00")
        self.assertFalse(updated.data_degraded)

    def test_apply_feature_payload_sets_snapshot_price(self) -> None:
        ctx = AgentContext(session_id="test-session-001b")
        payload: FeaturePayload = {
            "NIFTY_500_AD_Ratio": 1.0,
            "vix": 14.0,
            "VIX_ATR_Divergence": 0.0,
            "Expiry_Weighted_PCR_Momentum": None,
            "dte": 3,
        }
        updated = apply_feature_payload(ctx, payload, feature_snapshot_price=24_850.5)
        self.assertEqual(updated.feature_snapshot_price, 24_850.5)

    def test_opening_regime_to_feature_payload_roundtrip(self) -> None:
        payload: FeaturePayload = {
            "NIFTY_500_AD_Ratio": 1.1,
            "vix": 14.0,
            "VIX_ATR_Divergence": 0.2,
            "Expiry_Weighted_PCR_Momentum": None,
            "dte": 7,
        }
        ctx = apply_feature_payload(AgentContext(session_id="test-session-002"), payload)
        view = opening_regime_to_feature_payload(ctx)

        self.assertEqual(view["NIFTY_500_AD_Ratio"], 1.1)
        self.assertEqual(view["vix"], 14.0)
        self.assertEqual(view["VIX_ATR_Divergence"], 0.2)
        self.assertEqual(view["dte"], 7)
        self.assertNotIn("Expiry_Weighted_PCR_Momentum", view)

    def test_feature_payload_from_opening_regime_helper(self) -> None:
        regime = OpeningRegime(nifty_ad_ratio=1.3, vix=13.0, vix_atr_divergence=-0.1)
        view = feature_payload_from_opening_regime(regime, dte=4)
        self.assertEqual(view["NIFTY_500_AD_Ratio"], 1.3)
        self.assertEqual(view["dte"], 4)

    def test_sync_circuit_breaker_noop_when_healthy(self) -> None:
        ctx = AgentContext(session_id="test-session-003", daily_pnl=-100.0)
        self.assertEqual(sync_circuit_breaker(ctx), ctx)

    def test_sync_circuit_breaker_noop_when_tripped(self) -> None:
        ctx = AgentContext(
            session_id="test-session-004",
            daily_pnl=SESSION_CIRCUIT_BREAKER_PNL,
            circuit_status=True,
        )
        self.assertEqual(sync_circuit_breaker(ctx), ctx)


if __name__ == "__main__":
    unittest.main()
