"""Tests for SessionPipeline deterministic tick loop."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from src.core.context import (
    SESSION_CIRCUIT_BREAKER_PNL,
    AgentContext,
    OpenPosition,
    StrategyDecision,
    StrategyName,
)
from src.agents.symbol_resolver import StrikeSelectionError, ExpirySelectionError
from src.core.context import CriticStatus
from src.data.base_provider import BreadthSnapshot, MarketDataError, OptionChainPcr, OptionGreeks, Quote
from src.config.risk_config import RiskConfig
from src.risk.gatekeeper import GatekeeperVerdict
from src.features.feature_engine import FeatureEngineErrorCode
from src.execution.mock_port import MockExecutionPort
from src.orchestration.session_pipeline import SessionPipeline, SessionPipelineError
from src.orchestration.session_clock import IST
from src.orchestration.tick_lock import FileTickLock, NullTickLock
from src.risk.exit_engine import (
    CreditSpreadPosition,
    ExitAction,
    ExitEngine,
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


class _MultiLegQuoteProvider(_FakeProvider):
    def __init__(
        self,
        *,
        ask_by_symbol: dict[str, float] | None = None,
        default_ask: float = 80.0,
        bid_ask_error: Exception | None = None,
    ) -> None:
        super().__init__()
        self._ask_by_symbol = ask_by_symbol or {}
        self._default_ask = default_ask
        self._bid_ask_error = bid_ask_error

    async def get_bid_ask(self, symbol: str) -> Quote:
        if self._bid_ask_error is not None:
            raise self._bid_ask_error
        ask = self._ask_by_symbol.get(symbol, self._default_ask)
        return Quote(
            symbol=symbol,
            bid=ask - 1.0,
            ask=ask,
            ltp=ask - 0.5,
            spread_pct=0.02,
        )


def _iron_condor_open_position() -> OpenPosition:
    legs = [
        OpenPosition(
            symbol="NSE:NIFTY24JUN24000PE",
            strategy="iron_condor",
            lots=1,
            entry_price=80.0,
            leg_id="NSE:NIFTY24JUN24000PE",
            strategy_id="iron_condor",
        ),
        OpenPosition(
            symbol="NSE:NIFTY24JUN24100PE",
            strategy="iron_condor",
            lots=1,
            entry_price=120.0,
            leg_id="NSE:NIFTY24JUN24100PE",
            strategy_id="iron_condor",
        ),
        OpenPosition(
            symbol="NSE:NIFTY24JUN25000CE",
            strategy="iron_condor",
            lots=1,
            entry_price=90.0,
            leg_id="NSE:NIFTY24JUN25000CE",
            strategy_id="iron_condor",
        ),
        OpenPosition(
            symbol="NSE:NIFTY24JUN25100CE",
            strategy="iron_condor",
            lots=1,
            entry_price=60.0,
            leg_id="NSE:NIFTY24JUN25100CE",
            strategy_id="iron_condor",
        ),
    ]
    return OpenPosition(
        symbol="iron_condor_summary",
        strategy="iron_condor",
        lots=1,
        entry_price=87.5,
        strategy_id="iron_condor",
        legs=legs,
    )


class _RecordingPaperLogger:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def log_paper_row(self, row: dict) -> None:
        self.rows.append(row)


def _sample_bars() -> list[dict]:
    return [
        {"timestamp": 1, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        {"timestamp": 2, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1},
        {"timestamp": 3, "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1},
    ]


def _iron_condor_greeks_for_pipeline() -> list[OptionGreeks]:
    return [
        OptionGreeks(
            symbol="NSE:NIFTY24JUN24500PE",
            strike=24500.0,
            option_type="PE",
            delta=-0.15,
            gamma=0.01,
            confidence="high",
        ),
        OptionGreeks(
            symbol="NSE:NIFTY24JUN24700PE",
            strike=24700.0,
            option_type="PE",
            delta=-0.31,
            gamma=0.01,
            confidence="high",
        ),
        OptionGreeks(
            symbol="NSE:NIFTY24JUN25300CE",
            strike=25300.0,
            option_type="CE",
            delta=0.31,
            gamma=0.01,
            confidence="high",
        ),
        OptionGreeks(
            symbol="NSE:NIFTY24JUN25500CE",
            strike=25500.0,
            option_type="CE",
            delta=0.15,
            gamma=0.01,
            confidence="high",
        ),
    ]


class _EntryChainProvider(_FakeProvider):
    def __init__(self) -> None:
        super().__init__(index_ltp=102.0)

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

    async def get_option_chain_greeks(self, symbol: str, expiry_ts: int) -> list[OptionGreeks]:
        return _iron_condor_greeks_for_pipeline()


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

    async def test_run_tick_holds_healthy_bull_call_spread_position(self) -> None:
        ctx = AgentContext(
            session_id="pipeline-session-03",
            open_position=OpenPosition(
                symbol="NSE:NIFTY24JUN24900CE",
                strategy="bull_call_spread",
                lots=1,
                entry_price=100.0,
            ),
        )
        pipeline = SessionPipeline(_FakeProvider(), tick_lock=NullTickLock())
        result = await pipeline.run_tick(
            ctx,
            credit_spread_position=CreditSpreadPosition(
                entry_credit=100.0,
                current_close_cost=80.0,
            ),
        )

        self.assertIsNotNone(result.ctx.open_position)
        self.assertEqual(result.exit_decision.action, ExitAction.HOLD)

    async def test_run_tick_single_leg_does_not_populate_exit_leg_intents(self) -> None:
        ctx = AgentContext(
            session_id="pipeline-session-03b",
            open_position=OpenPosition(
                symbol="NSE:NIFTY24JUN24900CE",
                strategy="bull_call_spread",
                lots=1,
                entry_price=100.0,
            ),
        )
        pipeline = SessionPipeline(_FakeProvider(), tick_lock=NullTickLock())
        result = await pipeline.run_tick(
            ctx,
            credit_spread_position=CreditSpreadPosition(
                entry_credit=100.0,
                current_close_cost=80.0,
            ),
        )

        self.assertIsNone(result.ctx.exit_leg_intents)
        self.assertEqual(result.exit_decision.leg_action_intents, [])

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

    async def test_run_tick_exits_multi_leg_iron_condor_with_all_leg_intents(self) -> None:
        open_position = _iron_condor_open_position()
        short_leg = "NSE:NIFTY24JUN24100PE"
        provider = _MultiLegQuoteProvider(
            ask_by_symbol={short_leg: 40.0},
            default_ask=80.0,
        )
        ctx = AgentContext(
            session_id="pipeline-session-04b",
            open_position=open_position,
        )
        pipeline = SessionPipeline(provider, session_open_vix=14.0, tick_lock=NullTickLock())
        result = await pipeline.run_tick(ctx)

        self.assertIsNone(result.ctx.open_position)
        self.assertEqual(result.exit_decision.action, ExitAction.EXIT_MARKET)
        self.assertEqual(result.exit_decision.rule_id, "theta_capture")
        self.assertEqual(len(result.exit_decision.leg_action_intents), 4)
        self.assertTrue(
            all(intent.action == "EXIT_MARKET" for intent in result.exit_decision.leg_action_intents)
        )
        self.assertEqual(
            {intent.symbol for intent in result.exit_decision.leg_action_intents},
            {leg.symbol for leg in open_position.legs},
        )

    async def test_run_tick_with_multi_leg_position_populates_exit_leg_intents(self) -> None:
        ctx = AgentContext(
            session_id="pipeline-session-04c",
            open_position=_iron_condor_open_position(),
        )
        pipeline = SessionPipeline(
            _MultiLegQuoteProvider(default_ask=80.0),
            session_open_vix=14.0,
            tick_lock=NullTickLock(),
        )
        result = await pipeline.run_tick(ctx)

        self.assertIsNotNone(result.ctx.exit_leg_intents)
        self.assertEqual(len(result.ctx.exit_leg_intents), 4)
        self.assertTrue(all(intent.action == "HOLD" for intent in result.ctx.exit_leg_intents))

    async def test_run_tick_multi_leg_broker_error_triggers_emergency_flatten_intents(self) -> None:
        ctx = AgentContext(
            session_id="pipeline-session-04d",
            open_position=_iron_condor_open_position(),
        )
        provider = _MultiLegQuoteProvider(
            bid_ask_error=MarketDataError("simulated quote failure"),
        )
        pipeline = SessionPipeline(provider, session_open_vix=14.0, tick_lock=NullTickLock())
        result = await pipeline.run_tick(ctx)

        self.assertIsNone(result.ctx.open_position)
        self.assertEqual(result.exit_decision.rule_id, "broker_error_emergency_flatten")
        self.assertEqual(len(result.exit_decision.leg_action_intents), 4)
        self.assertTrue(
            all(intent.action == "EXIT_MARKET" for intent in result.exit_decision.leg_action_intents)
        )
        self.assertTrue(
            all(intent.action == "EXIT_MARKET" for intent in result.ctx.exit_leg_intents)
        )

    async def test_run_tick_multi_leg_open_position_uses_evaluate_position_path(self) -> None:
        ctx = AgentContext(
            session_id="pipeline-session-04e",
            open_position=_iron_condor_open_position(),
        )
        engine = ExitEngine()
        pipeline = SessionPipeline(
            _MultiLegQuoteProvider(default_ask=80.0),
            exit_engine=engine,
            session_open_vix=14.0,
            tick_lock=NullTickLock(),
        )

        with (
            patch.object(engine, "evaluate_position", wraps=engine.evaluate_position) as mock_position,
            patch.object(engine, "evaluate", wraps=engine.evaluate) as mock_evaluate,
        ):
            await pipeline.run_tick(ctx)

        mock_position.assert_called_once()
        mock_evaluate.assert_not_called()

    async def test_run_tick_with_disallowed_strategy_in_ctx_fails_validation(self) -> None:
        with self.assertRaises(ValidationError):
            AgentContext(
                session_id="pipeline-session-05",
                strategy_decision=StrategyDecision(
                    strategy="short_strangle",
                    supporting_signals=["ad_ratio=1.10", "vix=15.00"],
                ),
            )

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
        pipeline = SessionPipeline(_EntryChainProvider(), tick_lock=NullTickLock())
        ctx = AgentContext(session_id="pipeline-entry-chain-01", dte=3)
        result = await pipeline.run_tick(ctx)

        self.assertIsNotNone(result.ctx.regime_decision)
        self.assertIsNotNone(result.ctx.strategy_decision)
        self.assertEqual(result.ctx.strategy_decision.strategy, "iron_condor")
        self.assertIsNotNone(result.ctx.critic_decision)
        self.assertIsNotNone(result.ctx.gatekeeper_decision)
        self.assertEqual(result.ctx.gatekeeper_decision.verdict, GatekeeperVerdict.APPROVE)

    async def test_run_tick_uses_real_strike_selection_not_first_greeks(self) -> None:
        bid_ask_symbols: list[str] = []

        class _StrikeSelectionProvider(_EntryChainProvider):
            async def get_bid_ask(self, symbol: str) -> Quote:
                bid_ask_symbols.append(symbol)
                return await super().get_bid_ask(symbol)

        pipeline = SessionPipeline(_StrikeSelectionProvider(), tick_lock=NullTickLock())
        ctx = AgentContext(session_id="pipeline-entry-chain-02", dte=3)
        await pipeline.run_tick(ctx)

        self.assertEqual(len(bid_ask_symbols), 4)
        self.assertTrue(all(sym.endswith(("CE", "PE")) for sym in bid_ask_symbols))
        self.assertNotIn("NSE:NIFTY50-INDEX", bid_ask_symbols)

    async def test_run_tick_strike_selection_error_becomes_critic_reject(self) -> None:
        pipeline = SessionPipeline(_EntryChainProvider(), tick_lock=NullTickLock())
        ctx = AgentContext(session_id="pipeline-entry-chain-03", dte=3)

        with patch(
            "src.orchestration.session_pipeline.select_strategy_symbols",
            side_effect=StrikeSelectionError("delta_out_of_tolerance"),
        ):
            result = await pipeline.run_tick(ctx)

        self.assertEqual(result.ctx.critic_decision.status, CriticStatus.REJECT)
        self.assertIn("selection_error:StrikeSelectionError", result.ctx.critic_decision.reason)

    async def test_run_tick_expiry_selection_error_becomes_critic_reject(self) -> None:
        tight_dte_config = RiskConfig(min_dte_for_entry=0, max_dte_for_entry=0)
        pipeline = SessionPipeline(
            _EntryChainProvider(),
            risk_config=tight_dte_config,
            tick_lock=NullTickLock(),
        )
        ctx = AgentContext(session_id="pipeline-entry-chain-04", dte=3)
        result = await pipeline.run_tick(ctx)

        self.assertEqual(result.ctx.critic_decision.status, CriticStatus.REJECT)
        self.assertIn("selection_error:ExpirySelectionError", result.ctx.critic_decision.reason)

    async def test_run_tick_multi_leg_iron_condor_validates_per_leg_spreads(self) -> None:
        captured_spreads: list[float] = []

        class _SpreadProvider(_EntryChainProvider):
            async def get_bid_ask(self, symbol: str) -> Quote:
                spread = 0.01 if "24700PE" in symbol else 0.08
                captured_spreads.append(spread)
                return Quote(
                    symbol=symbol,
                    bid=100.0,
                    ask=100.0 + spread,
                    ltp=100.0,
                    spread_pct=spread,
                )

        pipeline = SessionPipeline(_SpreadProvider(), tick_lock=NullTickLock())
        ctx = AgentContext(session_id="pipeline-entry-chain-05", dte=3)
        result = await pipeline.run_tick(ctx)

        self.assertEqual(len(captured_spreads), 4)
        self.assertEqual(result.ctx.critic_decision.status, CriticStatus.REJECT)
        self.assertEqual(result.ctx.critic_decision.reason, "spread_too_wide")

    async def test_run_tick_initializes_baseline_when_not_set(self) -> None:
        pipeline = SessionPipeline(_FakeProvider(), tick_lock=NullTickLock())
        ctx = AgentContext(session_id="bootstrap-baseline-04")
        result = await pipeline.run_tick(ctx)

        self.assertTrue(result.ctx.baseline_initialized)
        self.assertEqual(result.ctx.feature_snapshot_price, 102.0)

    async def test_run_tick_dry_run_logs_paper_approve(self) -> None:
        rows: list[dict] = []

        class _Logger:
            def log_paper_row(self, row: dict) -> None:
                rows.append(row)

        pipeline = SessionPipeline(
            _EntryChainProvider(),
            tick_lock=NullTickLock(),
            dry_run=True,
            paper_logger=_Logger(),
        )
        ctx = AgentContext(session_id="pipeline-dry-run-01", dte=3)
        result = await pipeline.run_tick(ctx)

        self.assertIsNotNone(result.ctx.open_position)
        self.assertEqual(result.ctx.open_position.strategy, StrategyName.IRON_CONDOR)
        self.assertEqual(len(result.ctx.open_position.legs or []), 4)
        self.assertIn("PAPER_APPROVE", [row["event"] for row in rows])
        self.assertEqual(result.ctx.gatekeeper_decision.verdict, GatekeeperVerdict.APPROVE)

    async def test_run_tick_dry_run_clears_position_on_exit_market(self) -> None:
        """In dry_run, EXIT_MARKET should still clear open_position so the
        next tick evaluates from a flat state."""
        short_leg = "NSE:NIFTY24JUN24100PE"
        provider = _MultiLegQuoteProvider(
            ask_by_symbol={short_leg: 40.0},
            default_ask=80.0,
        )
        paper_logger = _RecordingPaperLogger()
        pipeline = SessionPipeline(
            provider,
            tick_lock=NullTickLock(),
            dry_run=True,
            paper_logger=paper_logger,
            session_open_vix=14.0,
        )
        ctx = AgentContext(
            session_id="dry-run-exit-01",
            open_position=_iron_condor_open_position(),
        )
        result = await pipeline.run_tick(ctx)

        self.assertIsNone(result.ctx.open_position)
        paper_rows = [row for row in paper_logger.rows if row["event"] == "PAPER_EXIT"]
        self.assertEqual(len(paper_rows), 1)

        second = await pipeline.run_tick(result.ctx)
        self.assertIsNone(second.ctx.open_position)
        paper_rows_after_second = [
            row for row in paper_logger.rows if row["event"] == "PAPER_EXIT"
        ]
        self.assertEqual(len(paper_rows_after_second), 1)

    async def test_run_tick_dry_run_execution_failure_halts_session(self) -> None:
        port = MockExecutionPort()
        port.configure_failure_at(1)
        pipeline = SessionPipeline(
            _EntryChainProvider(),
            execution_port=port,
            tick_lock=NullTickLock(),
            dry_run=True,
        )
        ctx = AgentContext(session_id="pipeline-exec-fail-01", dte=3)
        result = await pipeline.run_tick(ctx)

        self.assertTrue(result.ctx.execution_halted)
        self.assertIsNone(result.ctx.open_position)

    async def test_run_tick_exit_flatten_failure_retains_open_position(self) -> None:
        port = MockExecutionPort()
        port.configure_flatten_failure()
        short_leg = "NSE:NIFTY24JUN24100PE"
        provider = _MultiLegQuoteProvider(
            ask_by_symbol={short_leg: 40.0},
            default_ask=80.0,
        )
        pipeline = SessionPipeline(
            provider,
            execution_port=port,
            tick_lock=NullTickLock(),
            dry_run=True,
            session_open_vix=14.0,
        )
        ctx = AgentContext(
            session_id="pipeline-flatten-fail-01",
            open_position=_iron_condor_open_position(),
        )
        result = await pipeline.run_tick(ctx)

        self.assertIsNotNone(result.ctx.open_position)
        self.assertTrue(result.ctx.execution_halted)
        self.assertEqual(len(port.flatten_calls), 1)

    async def test_run_tick_does_not_call_evaluate_exit_when_flat(self) -> None:
        pipeline = SessionPipeline(_FakeProvider(), tick_lock=NullTickLock())
        ctx = AgentContext(session_id="pipeline-dry-run-02", dte=3)

        with patch.object(pipeline, "_evaluate_exit", new_callable=AsyncMock) as mock_exit:
            await pipeline.run_tick(ctx)

        mock_exit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
