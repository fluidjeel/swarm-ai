"""Tests for full feature engine orchestration."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.core.context import OpenPosition
from src.data.base_provider import BreadthSnapshot, MarketDataError, OptionChainPcr, OptionGreeks, Quote
from src.features.feature_engine import (
    FeatureEngineError,
    FeatureEngineErrorCode,
    compute_feature_payload,
    to_opening_regime,
)


class _FakeProvider:
    async def get_vix(self) -> float:
        return 14.5

    async def get_option_chain_pcr(self, symbol: str = "NSE:NIFTY50-INDEX", *, strikecount: int = 50):
        expiry = datetime.now(timezone.utc) + timedelta(days=7)
        return OptionChainPcr(
            pcr=1.05,
            call_oi=1000,
            put_oi=1050,
            expiry_timestamp=int(expiry.timestamp()),
            symbol=symbol,
        )

    async def get_nifty50_ad_ratio(self) -> BreadthSnapshot:
        return BreadthSnapshot(
            ad_ratio=1.2,
            advancers=30,
            decliners=25,
            unchanged=5,
            sample_size=50,
        )

    async def get_index_ohlcv(self, symbol: str, *, resolution: str = "5", lookback_bars: int = 50):
        return [
            {"timestamp": 1, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            {"timestamp": 2, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1},
            {"timestamp": 3, "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1},
        ]

    async def get_positions(self) -> list[OpenPosition]:
        return []

    async def get_option_chain_greeks(self, symbol: str, expiry_ts: int) -> list[OptionGreeks]:
        return [
            OptionGreeks(
                symbol=f"{symbol}:25000:CE",
                strike=25000.0,
                option_type="CE",
                delta=0.25,
                gamma=0.01,
                confidence="high",
            )
        ]

    async def get_bid_ask(self, symbol: str) -> Quote:
        return Quote(symbol=symbol, bid=99.0, ask=101.0, ltp=100.0, spread_pct=0.02)


class _FailingVixProvider(_FakeProvider):
    async def get_vix(self) -> float:
        raise MarketDataError("simulated provider failure")


class FeatureEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_compute_feature_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "pcr_history.json"
            payload = await compute_feature_payload(
                _FakeProvider(),
                pcr_history_path=history_path,
            )
        self.assertEqual(set(payload.keys()), {
            "NIFTY_500_AD_Ratio",
            "vix",
            "VIX_ATR_Divergence",
            "Expiry_Weighted_PCR_Momentum",
            "dte",
        })
        self.assertEqual(payload["vix"], 14.5)
        self.assertEqual(payload["NIFTY_500_AD_Ratio"], 1.2)
        self.assertIsNone(payload["Expiry_Weighted_PCR_Momentum"])

    async def test_market_data_error_has_code(self) -> None:
        with self.assertRaises(FeatureEngineError) as ctx:
            await compute_feature_payload(_FailingVixProvider())
        self.assertEqual(ctx.exception.code, FeatureEngineErrorCode.MARKET_DATA)

    def test_to_opening_regime(self) -> None:
        payload = {
            "NIFTY_500_AD_Ratio": 1.1,
            "vix": 14.0,
            "VIX_ATR_Divergence": 0.2,
            "Expiry_Weighted_PCR_Momentum": 0.05,
            "dte": 7,
        }
        opening = to_opening_regime(payload, captured_at_iso="2026-06-06T10:00:00+00:00")
        self.assertEqual(opening.nifty_ad_ratio, 1.1)
        self.assertEqual(opening.captured_at_iso, "2026-06-06T10:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
