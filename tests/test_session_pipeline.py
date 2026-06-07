"""Tests for SessionPipeline deterministic tick loop."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.core.context import (
    SESSION_CIRCUIT_BREAKER_PNL,
    AgentContext,
    OpenPosition,
)
from src.data.base_provider import BreadthSnapshot, MarketDataError, OptionChainPcr, OptionGreeks, Quote
from src.risk.gatekeeper import GatekeeperVerdict
from src.features.feature_engine import FeatureEngineErrorCode
from src.orchestration.session_pipeline import SessionPipeline, SessionPipelineError
from src.orchestration.session_clock import IST
from src.orchestration.tick_lock import FileTickLock, NullTickLock
from src.risk.exit_engine import (
    CreditSpreadPosition,
    ExitAction,
    FuturesPosition,
)


class _FakeProvider:
    def __init__(self, *, index_ltp: float = 24850.5, ltp_error: Exception | None = None) -> None:
        self._index_ltp = index_ltp
        self._ltp_error = ltp_error

    async def get_index_ltp(self, symbol: str) -> float:
        if self._ltp_error is not None:
            raise self._ltp_error
        return self._index_ltp

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
        return Quote(
            symbol=symbol,
            bid=101.0,
            ask=103.0,
            ltp=102.0,
            spread_pct=0.02,
            underlying_ltp=102.0,
        )


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
        self.assertTrue(result.ctx.baseline_initialized)
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


def _intraday_ist() -> datetime:
    return datetime(2026, 6, 9, 10, 0, tzinfo=IST)


class BootstrapBaselineTests(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_initializes_baseline_from_ltp(self) -> None:
        boot_rows: list[dict] = []

        class _Writer:
            def log_boot_row(self, row: dict) -> None:
                boot_rows.append(row)

        provider = _FakeProvider(index_ltp=24850.5)
        pipeline = SessionPipeline(provider, tick_lock=NullTickLock(), boot_logger=_Writer())
        ctx = AgentContext(session_id="bootstrap-baseline-01")

        with patch(
            "src.orchestration.session_pipeline.rebuild_from_fyers",
            new_callable=AsyncMock,
        ) as mock_rebuild:
            mock_rebuild.return_value = ctx
            result = await pipeline.bootstrap_session(ctx, now_ist=_intraday_ist())

        self.assertTrue(result.baseline_initialized)
        self.assertEqual(result.feature_snapshot_price, 24850.5)
        success_rows = [row for row in boot_rows if row.get("outcome") == "bootstrap_success"]
        self.assertEqual(len(success_rows), 1)
        self.assertTrue(success_rows[0]["baseline_initialized"])

    async def test_bootstrap_continues_when_ltp_provider_fails(self) -> None:
        boot_rows: list[dict] = []

        class _Writer:
            def log_boot_row(self, row: dict) -> None:
                boot_rows.append(row)

        provider = _FakeProvider(ltp_error=MarketDataError("ltp unavailable"))
        pipeline = SessionPipeline(provider, tick_lock=NullTickLock(), boot_logger=_Writer())
        ctx = AgentContext(session_id="bootstrap-baseline-02")

        with patch(
            "src.orchestration.session_pipeline.rebuild_from_fyers",
            new_callable=AsyncMock,
        ) as mock_rebuild:
            mock_rebuild.return_value = ctx
            result = await pipeline.bootstrap_session(ctx, now_ist=_intraday_ist())

        self.assertFalse(result.baseline_initialized)
        self.assertIsNone(result.feature_snapshot_price)
        failed_rows = [row for row in boot_rows if row.get("outcome") == "baseline_init_failed"]
        self.assertEqual(len(failed_rows), 1)

    async def test_first_tick_after_bootstrap_does_not_overwrite_baseline_unnecessarily(self) -> None:
        provider = _FakeProvider(index_ltp=24850.5)
        pipeline = SessionPipeline(provider, tick_lock=NullTickLock())
        ctx = AgentContext(session_id="bootstrap-baseline-03")

        with patch(
            "src.orchestration.session_pipeline.rebuild_from_fyers",
            new_callable=AsyncMock,
        ) as mock_rebuild:
            mock_rebuild.return_value = ctx
            ctx = await pipeline.bootstrap_session(ctx, now_ist=_intraday_ist())

        self.assertEqual(ctx.feature_snapshot_price, 24850.5)
        result = await pipeline.run_tick(ctx)
        self.assertTrue(result.ctx.baseline_initialized)
        self.assertEqual(result.ctx.feature_snapshot_price, 102.0)

    async def test_run_tick_entry_chain_populates_agent_decisions(self) -> None:
        class _EntryChainProvider(_FakeProvider):
            async def get_nifty50_ad_ratio(self) -> BreadthSnapshot:
                return BreadthSnapshot(
                    ad_ratio=1.05,
                    advancers=28,
                    decliners=27,
                    unchanged=5,
                    sample_size=50,
                )

            async def get_index_ohlcv(self, symbol: str, *, resolution: str = "5", lookback_bars: int = 50):
                flat_bar = {
                    "timestamp": 1,
                    "open": 102.0,
                    "high": 102.0,
                    "low": 102.0,
                    "close": 102.0,
                    "volume": 0,
                }
                if "VIX" in symbol.upper():
                    return [
                        {**flat_bar, "timestamp": 1, "close": 14.0},
                        {**flat_bar, "timestamp": 2, "close": 14.1},
                        {**flat_bar, "timestamp": 3, "close": 14.2},
                    ]
                return [
                    {**flat_bar, "timestamp": 1},
                    {**flat_bar, "timestamp": 2},
                    {**flat_bar, "timestamp": 3},
                ]

        pipeline = SessionPipeline(_EntryChainProvider(), tick_lock=NullTickLock())
        ctx = AgentContext(session_id="pipeline-entry-chain-01")
        result = await pipeline.run_tick(ctx)

        self.assertIsNotNone(result.ctx.regime_decision)
        self.assertIsNotNone(result.ctx.strategy_decision)
        self.assertEqual(result.ctx.strategy_decision.strategy, "iron_condor")
        self.assertIsNotNone(result.ctx.critic_decision)
        self.assertIsNotNone(result.ctx.gatekeeper_decision)
        self.assertEqual(result.ctx.gatekeeper_decision.verdict, GatekeeperVerdict.APPROVE)

    async def test_run_tick_initializes_baseline_when_not_set(self) -> None:
        pipeline = SessionPipeline(_FakeProvider(), tick_lock=NullTickLock())
        ctx = AgentContext(session_id="bootstrap-baseline-04")
        result = await pipeline.run_tick(ctx)

        self.assertTrue(result.ctx.baseline_initialized)
        self.assertEqual(result.ctx.feature_snapshot_price, 102.0)


if __name__ == "__main__":
    unittest.main()
