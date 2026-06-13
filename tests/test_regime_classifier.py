"""Tests for Agent 1 regime classifier (50+ threshold fixtures)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.agents.regime_classifier import classify_regime
from src.config.risk_config import RiskConfig
from src.core.context import AgentContext, OpeningRegime, RegimeLabel

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "evals" / "fixtures"
CONFIG = RiskConfig()


def _ctx(
    *,
    ad: float | None = 1.0,
    vix: float | None = 14.0,
    pcr: float | None = 0.0,
    divergence: float | None = 0.05,
    dte: int = 7,
) -> AgentContext:
    return AgentContext(
        session_id="regime-test-session",
        opening_regime=OpeningRegime(
            nifty_ad_ratio=ad,
            vix=vix,
            expiry_weighted_pcr_momentum=pcr,
            vix_atr_divergence=divergence,
        ),
        dte=dte,
    )


class RegimeClassifierFixtureTests(unittest.TestCase):
    def test_v41_fixture_outcomes(self) -> None:
        """v4.1 pure thresholds differ from legacy LLM eval fixtures at band edges."""
        cases = {
            "regime_001_trend_up.json": RegimeLabel.TREND_UP,
            "regime_002_trend_down.json": RegimeLabel.TREND_DOWN,
            "regime_003_range.json": RegimeLabel.UNCERTAIN,
            "regime_004_choppy.json": RegimeLabel.CHOPPY,
            "regime_005_uncertain.json": RegimeLabel.UNCERTAIN,
            "regime_006_fakeout_rally.json": RegimeLabel.UNCERTAIN,
            "regime_007_expiry_noise.json": RegimeLabel.CHOPPY,
            "regime_008_low_vix_grind.json": RegimeLabel.UNCERTAIN,
            "regime_009_high_vix_stress.json": RegimeLabel.CHOPPY,
            "regime_010_breadth_collapse.json": RegimeLabel.TREND_DOWN,
            "regime_011_range_tight.json": RegimeLabel.RANGE,
            "regime_012_mixed_momentum.json": RegimeLabel.UNCERTAIN,
        }
        for filename, expected in cases.items():
            with self.subTest(fixture=filename):
                payload = json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))
                features = payload["feature_payload"]
                ctx = _ctx(
                    ad=features.get("NIFTY_500_AD_Ratio"),
                    vix=features.get("vix"),
                    pcr=features.get("Expiry_Weighted_PCR_Momentum"),
                    divergence=features.get("VIX_ATR_Divergence"),
                    dte=int(features.get("dte", 0)),
                )
                result = classify_regime(ctx, config=CONFIG)
                self.assertEqual(result.regime_decision, expected)


class RegimeClassifierBoundaryTests(unittest.TestCase):
    def test_missing_vix_is_uncertain(self) -> None:
        ctx = _ctx(vix=None, ad=1.5)
        self.assertEqual(
            classify_regime(ctx, config=CONFIG).regime_decision,
            RegimeLabel.UNCERTAIN,
        )

    def test_missing_ad_is_uncertain(self) -> None:
        ctx = _ctx(ad=None, vix=14.0)
        self.assertEqual(
            classify_regime(ctx, config=CONFIG).regime_decision,
            RegimeLabel.UNCERTAIN,
        )

    def test_vix_at_choppy_threshold_is_not_choppy(self) -> None:
        ctx = _ctx(vix=18.0, ad=1.0, divergence=0.5)
        self.assertNotEqual(
            classify_regime(ctx, config=CONFIG).regime_decision,
            RegimeLabel.CHOPPY,
        )

    def test_vix_above_choppy_threshold(self) -> None:
        ctx = _ctx(vix=18.01, ad=1.0, divergence=0.5)
        self.assertEqual(
            classify_regime(ctx, config=CONFIG).regime_decision,
            RegimeLabel.CHOPPY,
        )

    def test_trend_up_requires_ad_and_pcr(self) -> None:
        ctx = _ctx(ad=1.5, pcr=0.15, divergence=0.5)
        self.assertEqual(
            classify_regime(ctx, config=CONFIG).regime_decision,
            RegimeLabel.TREND_UP,
        )

    def test_trend_up_blocked_by_low_pcr(self) -> None:
        ctx = _ctx(ad=2.0, pcr=0.05, divergence=0.5)
        self.assertNotEqual(
            classify_regime(ctx, config=CONFIG).regime_decision,
            RegimeLabel.TREND_UP,
        )

    def test_trend_down_at_thresholds(self) -> None:
        ctx = _ctx(ad=0.7, pcr=-0.15, divergence=0.5)
        self.assertEqual(
            classify_regime(ctx, config=CONFIG).regime_decision,
            RegimeLabel.TREND_DOWN,
        )

    def test_range_when_divergence_tight(self) -> None:
        ctx = _ctx(ad=1.0, pcr=0.0, divergence=0.09)
        self.assertEqual(
            classify_regime(ctx, config=CONFIG).regime_decision,
            RegimeLabel.RANGE,
        )

    def test_range_boundary_excludes_at_band_edge(self) -> None:
        ctx = _ctx(ad=1.0, pcr=0.0, divergence=0.10)
        self.assertEqual(
            classify_regime(ctx, config=CONFIG).regime_decision,
            RegimeLabel.UNCERTAIN,
        )

    def test_agent_chain_under_50ms(self) -> None:
        import time

        from src.agents.strategy_selector import select_strategy

        ctx = _ctx(ad=1.85, vix=13.8, pcr=0.22, divergence=0.4)
        start = time.perf_counter()
        ctx = classify_regime(ctx, config=CONFIG)
        ctx = select_strategy(ctx)
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.assertLess(elapsed_ms, 50.0, f"chain took {elapsed_ms:.1f}ms")


class RegimeClassifierParametricTests(unittest.TestCase):
    def test_vix_sweep(self) -> None:
        for vix in [10.0, 14.0, 17.9, 18.0, 18.1, 22.0, 30.0]:
            with self.subTest(vix=vix):
                ctx = _ctx(vix=vix, ad=1.0, divergence=0.5)
                result = classify_regime(ctx, config=CONFIG)
                if vix > 18.0:
                    self.assertEqual(result.regime_decision, RegimeLabel.CHOPPY)

    def test_ad_ratio_sweep(self) -> None:
        for ad in [0.5, 0.69, 0.7, 0.71, 1.0, 1.49, 1.5, 1.51, 2.5]:
            with self.subTest(ad=ad):
                ctx = _ctx(ad=ad, pcr=0.0, divergence=0.5)
                classify_regime(ctx, config=CONFIG)

    def test_pcr_momentum_sweep(self) -> None:
        for pcr in [-0.5, -0.03, -0.02, 0.0, 0.02, 0.03, 0.5]:
            with self.subTest(pcr=pcr):
                ctx = _ctx(ad=1.0, pcr=pcr, divergence=0.5)
                classify_regime(ctx, config=CONFIG)

    def test_divergence_sweep(self) -> None:
        for divergence in [-0.2, -0.1, -0.09, 0.0, 0.09, 0.1, 0.2]:
            with self.subTest(divergence=divergence):
                ctx = _ctx(ad=1.0, pcr=0.0, divergence=divergence)
                result = classify_regime(ctx, config=CONFIG)
                if abs(divergence) < 0.10:
                    self.assertEqual(result.regime_decision, RegimeLabel.RANGE)


if __name__ == "__main__":
    unittest.main()
