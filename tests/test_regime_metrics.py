"""Unit tests for regime metrics helpers."""

from __future__ import annotations

import unittest

from src.core.context import OpenPosition
from src.data.base_provider import BreadthSnapshot, OptionChainPcr, OptionGreeks, Quote
from src.features.regime_metrics import _derive_vix_trend, compute_regime_metrics


class _FakeProvider:
    def __init__(self, *, fail_vix: bool = False) -> None:
        self.fail_vix = fail_vix

    async def get_vix(self) -> float:
        if self.fail_vix:
            raise RuntimeError("vix down")
        return 14.2

    async def get_option_chain_pcr(self, symbol: str = "NSE:NIFTY50-INDEX", *, strikecount: int = 50):
        from src.data.base_provider import OptionChainPcr

        return OptionChainPcr(
            pcr=1.1,
            call_oi=1000,
            put_oi=1100,
            expiry_timestamp=1_810_000_000,
            symbol=symbol,
        )

    async def get_index_ohlcv(self, symbol: str, *, resolution: str = "5", lookback_bars: int = 50):
        return [
            {"timestamp": 1, "open": 14.0, "high": 14.5, "low": 13.8, "close": 14.0, "volume": 0},
            {"timestamp": 2, "open": 14.0, "high": 14.8, "low": 14.0, "close": 14.6, "volume": 0},
        ]

    async def get_nifty50_ad_ratio(self) -> BreadthSnapshot:
        return BreadthSnapshot(ad_ratio=1.0, advancers=25, decliners=25, unchanged=0, sample_size=50)

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


class RegimeMetricsTests(unittest.IsolatedAsyncioTestCase):
    def test_vix_trend_up(self) -> None:
        bars = [
            {"close": 14.0},
            {"close": 14.5},
        ]
        self.assertEqual(_derive_vix_trend(bars), "UP")

    def test_vix_trend_down(self) -> None:
        bars = [
            {"close": 16.0},
            {"close": 15.5},
        ]
        self.assertEqual(_derive_vix_trend(bars), "DOWN")

    def test_vix_trend_flat(self) -> None:
        bars = [
            {"close": 15.0},
            {"close": 15.05},
        ]
        self.assertEqual(_derive_vix_trend(bars), "FLAT")

    async def test_compute_regime_metrics(self) -> None:
        metrics = await compute_regime_metrics(_FakeProvider())
        self.assertEqual(metrics["current_vix"], 14.2)
        self.assertEqual(metrics["pcr"], 1.1)
        self.assertEqual(metrics["vix_trend"], "UP")



if __name__ == "__main__":
    unittest.main()
