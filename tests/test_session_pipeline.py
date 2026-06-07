"""Tests for SessionPipeline deterministic tick loop."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.core.context import (
    SESSION_CIRCUIT_BREAKER_PNL,
    AgentContext,
    OpenPosition,
)
from src.data.base_provider import BreadthSnapshot, MarketDataError, OptionChainPcr, OptionGreeks, Quote
from src.features.feature_engine import FeatureEngineErrorCode
from src.orchestration.session_pipeline import SessionPipeline, SessionPipelineError
from src.orchestration.tick_lock import FileTickLock, NullTickLock
from src.risk.exit_engine import (
    CreditSpreadPosition,
    ExitAction,
    FuturesPosition,
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


def _sample_bars() -> list[dict]:
    return [
        {"timestamp": 1, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        {"timestamp": 2, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1},
        {"timestamp": 3, "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1},
    ]


class SessionPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_tick_refreshes_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = SessionPipeline(
                _FakeProvider(),
                pcr_history_path=Path(tmp) / "pcr_history.json",
                tick_lock=NullTickLock(),
            )
            ctx = AgentContext(session_id="pipeline-session-01")
            result = await pipeline.run_tick(ctx)

        self.assertIsNone(result.exit_decision)
        self.assertEqual(result.ctx.opening_regime.vix, 14.5)
        self.assertEqual(result.ctx.opening_regime.nifty_ad_ratio, 1.2)
        self.assertGreaterEqual(result.ctx.dte, 0)
        self.assertIsNotNone(result.ctx.opening_regime.captured_at_iso)
        self.assertEqual(result.ctx.feature_snapshot_price, 102.0)
        self.assertFalse(result.ctx.data_degraded)

    async def test_run_tick_halted_without_position_still_refreshes(self) -> None:
        ctx = AgentContext(
            session_id="pipeline-session-02",
            daily_pnl=SESSION_CIRCUIT_BREAKER_PNL,
            circuit_status=True,
        )
        pipeline = SessionPipeline(_FakeProvider(), tick_lock=NullTickLock())
        result = await pipeline.run_tick(ctx)

        self.assertEqual(result.ctx.opening_regime.vix, 14.5)
        self.assertIsNone(result.ctx.open_position)
        self.assertIsNone(result.exit_decision)

    async def test_run_tick_holds_healthy_futures_position(self) -> None:
        ctx = AgentContext(
            session_id="pipeline-session-03",
            open_position=OpenPosition(
                symbol="NSE:NIFTY24JUNFUT",
                strategy="nifty_futures_long",
                lots=1,
                entry_price=100.0,
            ),
        )
        pipeline = SessionPipeline(_FakeProvider(), tick_lock=NullTickLock())
        result = await pipeline.run_tick(
            ctx,
            futures_position=FuturesPosition(
                side="long",
                entry_price=100.0,
                current_price=102.0,
                extreme_price=102.0,
            ),
            nifty_bars=_sample_bars(),
        )

        self.assertIsNotNone(result.ctx.open_position)
        self.assertEqual(result.exit_decision.action, ExitAction.HOLD)

    async def test_run_tick_exits_credit_spread_and_clears_position(self) -> None:
        ctx = AgentContext(
            session_id="pipeline-session-04",
            open_position=OpenPosition(
                symbol="NSE:NIFTY-OPT",
                strategy="iron_condor",
                lots=1,
                entry_price=100.0,
            ),
        )
        pipeline = SessionPipeline(_FakeProvider(), session_open_vix=14.0)
        result = await pipeline.run_tick(
            ctx,
            credit_spread_position=CreditSpreadPosition(
                entry_credit=100.0,
                current_close_cost=35.0,
            ),
        )

        self.assertIsNone(result.ctx.open_position)
        self.assertEqual(result.exit_decision.action, ExitAction.EXIT_MARKET)
        self.assertEqual(result.exit_decision.rule_id, "theta_capture")

    async def test_run_tick_exits_futures_on_regime_flip(self) -> None:
        provider = _FakeProvider()

        async def low_breadth() -> BreadthSnapshot:
            return BreadthSnapshot(
                ad_ratio=0.85,
                advancers=20,
                decliners=30,
                unchanged=0,
                sample_size=50,
            )

        provider.get_nifty50_ad_ratio = low_breadth  # type: ignore[method-assign]

        ctx = AgentContext(
            session_id="pipeline-session-05",
            open_position=OpenPosition(
                symbol="NSE:NIFTY24JUNFUT",
                strategy="nifty_futures_long",
                lots=1,
                entry_price=100.0,
            ),
        )
        pipeline = SessionPipeline(provider)
        result = await pipeline.run_tick(
            ctx,
            futures_position=FuturesPosition(
                side="long",
                entry_price=100.0,
                current_price=102.0,
                extreme_price=102.0,
            ),
            nifty_bars=_sample_bars(),
        )

        self.assertIsNone(result.ctx.open_position)
        self.assertEqual(result.exit_decision.rule_id, "regime_flip")

    async def test_run_tick_feature_error_raises_pipeline_error(self) -> None:
        pipeline = SessionPipeline(_FailingVixProvider())
        ctx = AgentContext(session_id="pipeline-session-06")

        with self.assertRaises(SessionPipelineError) as exc:
            await pipeline.run_tick(ctx)

        self.assertEqual(exc.exception.code, FeatureEngineErrorCode.MARKET_DATA)

    async def test_run_tick_blocks_when_tick_lock_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "pipeline.lock"
            holder = FileTickLock(lock_path)
            holder.acquire(blocking=False)
            try:
                pipeline = SessionPipeline(
                    _FakeProvider(),
                    tick_lock=FileTickLock(lock_path),
                )
                ctx = AgentContext(session_id="pipeline-session-08")
                with self.assertRaises(SessionPipelineError) as exc:
                    await pipeline.run_tick(ctx)
                self.assertEqual(exc.exception.code, "TICK_LOCK")
            finally:
                holder.release()

    async def test_run_tick_uses_null_lock_when_disabled(self) -> None:
        pipeline = SessionPipeline(_FakeProvider(), tick_lock=NullTickLock())
        ctx = AgentContext(session_id="pipeline-session-09")
        result = await pipeline.run_tick(ctx)
        self.assertEqual(result.ctx.opening_regime.vix, 14.5)

    async def test_run_tick_requires_exit_inputs_for_open_position(self) -> None:
        ctx = AgentContext(
            session_id="pipeline-session-07",
            open_position=OpenPosition(
                symbol="NSE:NIFTY-OPT",
                strategy="iron_condor",
                lots=1,
                entry_price=100.0,
            ),
        )
        pipeline = SessionPipeline(_FakeProvider(), tick_lock=NullTickLock())

        with self.assertRaises(SessionPipelineError):
            await pipeline.run_tick(ctx)


if __name__ == "__main__":
    unittest.main()
