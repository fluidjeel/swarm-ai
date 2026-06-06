#!/usr/bin/env python3
"""Generate 22+ market extreme fixtures for offline eval suite."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = ROOT / "fixtures"


def _write(name: str, payload: dict) -> None:
    path = FIXTURES_DIR / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    regime_cases = [
        ("regime_001_trend_up.json", "TREND_UP", {"NIFTY_500_AD_Ratio": 1.85, "vix": 13.8, "VIX_ATR_Divergence": 0.4, "Expiry_Weighted_PCR_Momentum": 0.22, "dte": 7}),
        ("regime_002_trend_down.json", "TREND_DOWN", {"NIFTY_500_AD_Ratio": 0.62, "vix": 17.5, "VIX_ATR_Divergence": -0.5, "Expiry_Weighted_PCR_Momentum": -0.31, "dte": 6}),
        ("regime_003_range.json", "RANGE", {"NIFTY_500_AD_Ratio": 1.05, "vix": 14.2, "VIX_ATR_Divergence": 0.1, "Expiry_Weighted_PCR_Momentum": 0.03, "dte": 9}),
        ("regime_004_choppy.json", "CHOPPY", {"NIFTY_500_AD_Ratio": 0.98, "vix": 19.4, "VIX_ATR_Divergence": 0.7, "Expiry_Weighted_PCR_Momentum": -0.12, "dte": 4}),
        ("regime_005_uncertain.json", "UNCERTAIN", {"NIFTY_500_AD_Ratio": 1.02, "vix": 16.1, "VIX_ATR_Divergence": -0.2, "Expiry_Weighted_PCR_Momentum": 0.18, "dte": 3}),
        ("regime_006_fakeout_rally.json", "UNCERTAIN", {"NIFTY_500_AD_Ratio": 0.84, "vix": 15.0, "VIX_ATR_Divergence": 0.2, "Expiry_Weighted_PCR_Momentum": 0.09, "dte": 8}),
        ("regime_007_expiry_noise.json", "CHOPPY", {"NIFTY_500_AD_Ratio": 1.1, "vix": 18.9, "VIX_ATR_Divergence": 0.3, "Expiry_Weighted_PCR_Momentum": 0.45, "dte": 1}),
        ("regime_008_low_vix_grind.json", "TREND_UP", {"NIFTY_500_AD_Ratio": 1.45, "vix": 11.6, "VIX_ATR_Divergence": -0.3, "Expiry_Weighted_PCR_Momentum": 0.11, "dte": 11}),
        ("regime_009_high_vix_stress.json", "CHOPPY", {"NIFTY_500_AD_Ratio": 0.9, "vix": 22.3, "VIX_ATR_Divergence": 1.1, "Expiry_Weighted_PCR_Momentum": -0.25, "dte": 5}),
        ("regime_010_breadth_collapse.json", "TREND_DOWN", {"NIFTY_500_AD_Ratio": 0.48, "vix": 18.0, "VIX_ATR_Divergence": 0.6, "Expiry_Weighted_PCR_Momentum": -0.4, "dte": 6}),
        ("regime_011_range_tight.json", "RANGE", {"NIFTY_500_AD_Ratio": 1.0, "vix": 13.1, "VIX_ATR_Divergence": 0.0, "Expiry_Weighted_PCR_Momentum": 0.0, "dte": 10}),
        ("regime_012_mixed_momentum.json", "UNCERTAIN", {"NIFTY_500_AD_Ratio": 1.2, "vix": 16.8, "VIX_ATR_Divergence": -0.4, "Expiry_Weighted_PCR_Momentum": -0.2, "dte": 2}),
    ]

    for filename, expected, payload in regime_cases:
        _write(
            filename,
            {
                "id": filename.replace(".json", ""),
                "agent": "regime_classifier",
                "feature_payload": payload,
                "expected": {"regime_decision": expected},
                "golden_output": {
                    "regime_decision": expected,
                    "rationale": f"Fixture expects {expected} based on payload extremes.",
                },
            },
        )

    strategy_cases = [
        ("strategy_001_range_iron_condor.json", "iron_condor", "RANGE", {"NIFTY_500_AD_Ratio": 1.03, "vix": 14.0, "VIX_ATR_Divergence": 0.05, "Expiry_Weighted_PCR_Momentum": 0.02, "dte": 8}),
        ("strategy_002_trend_up_bull_call.json", "bull_call_spread", "TREND_UP", {"NIFTY_500_AD_Ratio": 1.7, "vix": 13.5, "VIX_ATR_Divergence": 0.3, "Expiry_Weighted_PCR_Momentum": 0.2, "dte": 7}),
        ("strategy_003_trend_down_bear_put.json", "bear_put_spread", "TREND_DOWN", {"NIFTY_500_AD_Ratio": 0.6, "vix": 17.0, "VIX_ATR_Divergence": -0.4, "Expiry_Weighted_PCR_Momentum": -0.3, "dte": 6}),
        ("strategy_004_choppy_no_trade.json", "cash_no_trade", "CHOPPY", {"NIFTY_500_AD_Ratio": 0.95, "vix": 20.0, "VIX_ATR_Divergence": 0.8, "Expiry_Weighted_PCR_Momentum": -0.1, "dte": 4}),
        ("strategy_005_uncertain_no_trade.json", "cash_no_trade", "UNCERTAIN", {"NIFTY_500_AD_Ratio": 1.01, "vix": 16.0, "VIX_ATR_Divergence": -0.1, "Expiry_Weighted_PCR_Momentum": 0.15, "dte": 3}),
        ("strategy_006_high_vix_block_short_vol.json", "cash_no_trade", "RANGE", {"NIFTY_500_AD_Ratio": 1.05, "vix": 19.2, "VIX_ATR_Divergence": 0.2, "Expiry_Weighted_PCR_Momentum": 0.01, "dte": 7}),
        ("strategy_007_expiry_block_short_vol.json", "cash_no_trade", "RANGE", {"NIFTY_500_AD_Ratio": 1.08, "vix": 15.5, "VIX_ATR_Divergence": 0.1, "Expiry_Weighted_PCR_Momentum": 0.4, "dte": 1}),
        ("strategy_008_trend_up_futures.json", "nifty_futures_long", "TREND_UP", {"NIFTY_500_AD_Ratio": 1.55, "vix": 12.8, "VIX_ATR_Divergence": -0.2, "Expiry_Weighted_PCR_Momentum": 0.18, "dte": 9}),
        ("strategy_009_trend_down_futures.json", "nifty_futures_short", "TREND_DOWN", {"NIFTY_500_AD_Ratio": 0.55, "vix": 18.5, "VIX_ATR_Divergence": 0.5, "Expiry_Weighted_PCR_Momentum": -0.35, "dte": 5}),
        ("strategy_010_range_strangle.json", "short_strangle", "RANGE", {"NIFTY_500_AD_Ratio": 1.0, "vix": 13.0, "VIX_ATR_Divergence": 0.0, "Expiry_Weighted_PCR_Momentum": -0.01, "dte": 12}),
    ]

    for filename, strategy, regime, payload in strategy_cases:
        _write(
            filename,
            {
                "id": filename.replace(".json", ""),
                "agent": "strategy_selector",
                "feature_payload": payload,
                "context": {"regime_decision": regime},
                "expected": {"strategy": strategy},
                "golden_output": {
                    "strategy": strategy,
                    "supporting_signals": ["payload_regime_alignment", "risk_filter_pass"],
                    "rationale": f"Fixture expects {strategy} under {regime}.",
                },
            },
        )

    print(f"Wrote {len(regime_cases) + len(strategy_cases)} fixtures to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
