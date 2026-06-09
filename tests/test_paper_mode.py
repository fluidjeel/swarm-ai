"""Tests for paper-mode soak runner."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from unittest.mock import AsyncMock, patch

from src.core.context import AgentContext, CriticDecision, CriticStatus, RegimeLabel, StrategyDecision
from src.data.base_provider import BreadthSnapshot, OptionChainPcr, OptionGreeks, Quote
from src.orchestration.paper_mode import (
    JsonlPaperLogger,
    MultiIndexPaperSoakRunner,
    PaperSoakRunner,
    build_paper_soak_summary,
    build_paper_tick_row,
    build_soak_runner,
    default_paper_tick_lock_path,
    paper_tick_lock_path_for,
)
from src.orchestration.session_clock import IST, NSE_HOLIDAYS
from src.orchestration.session_pipeline import SessionPipeline
from src.orchestration.tick_lock import NullTickLock
from src.risk.exit_engine import CreditSpreadPosition, ExitAction, ExitDecision
from src.risk.gatekeeper import GatekeeperDecision, GatekeeperRule, GatekeeperVerdict


class _PaperFakeProvider:
    async def get_index_ltp(self, symbol: str) -> float:
        return 102.0

    async def get_vix(self) -> float:
        return 14.5

    async def get_option_chain_pcr(self, symbol: str = "NSE:NIFTY50-INDEX", *, strikecount: int = 50):
        from datetime import timedelta, timezone

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
            ad_ratio=1.05,
            advancers=28,
            decliners=27,
            unchanged=5,
            sample_size=50,
        )

    async def get_index_ohlcv(self, symbol: str, *, resolution: str = "5", lookback_bars: int = 50):
        bar = {
            "timestamp": 1,
            "open": 102.0,
            "high": 102.0,
            "low": 102.0,
            "close": 102.0,
            "volume": 0,
        }
        return [bar, bar, bar]

    async def get_option_chain_greeks(self, symbol: str, expiry_ts: int) -> list[OptionGreeks]:
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

    async def get_bid_ask(self, symbol: str) -> Quote:
        return Quote(
            symbol=symbol,
            bid=101.0,
            ask=103.0,
            ltp=102.0,
            spread_pct=0.02,
        )

    async def get_positions(self) -> list:
        return []


class _RecordingPaperLogger:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def log_paper_row(self, row: dict) -> None:
        self.rows.append(row)


def _advancing_clock(start: datetime) -> tuple[Callable[[], datetime], Any]:
    state = {"now": start}

    def now_fn() -> datetime:
        return state["now"]

    async def sleep_fn(seconds: float) -> None:
        state["now"] += timedelta(seconds=seconds)

    return now_fn, sleep_fn


class PaperModeTests(unittest.IsolatedAsyncioTestCase):
    def test_paper_mode_dry_run_flag_propagates_to_pipeline(self) -> None:
        logger = _RecordingPaperLogger()
        pipeline = SessionPipeline(
            _PaperFakeProvider(),
            tick_lock=NullTickLock(),
            dry_run=True,
            paper_logger=logger,
        )
        self.assertTrue(pipeline._dry_run)
        self.assertIs(pipeline._paper_logger, logger)

    async def test_paper_mode_logs_paper_approve_row_on_gatekeeper_approve(self) -> None:
        logger = _RecordingPaperLogger()
        pipeline = SessionPipeline(
            _PaperFakeProvider(),
            tick_lock=NullTickLock(),
            dry_run=True,
            paper_logger=logger,
        )
        ctx = AgentContext(session_id="paper-approve-01", dte=3)
        await pipeline.run_tick(ctx)
        events = [row["event"] for row in logger.rows]
        self.assertIn("PAPER_APPROVE", events)

    async def test_paper_mode_logs_paper_exit_row_on_exit_market(self) -> None:
        from src.core.context import OpenPosition

        logger = _RecordingPaperLogger()
        pipeline = SessionPipeline(
            _PaperFakeProvider(),
            tick_lock=NullTickLock(),
            dry_run=True,
            paper_logger=logger,
        )
        ctx = AgentContext(
            session_id="paper-exit-01",
            open_position=OpenPosition(
                symbol="NSE:NIFTY-OPT",
                strategy="iron_condor",
                lots=1,
                entry_price=100.0,
            ),
        )

        exit_decision = ExitDecision(
            action=ExitAction.EXIT_MARKET,
            reason="theta capture",
            rule_id="theta_capture",
        )

        with patch.object(pipeline, "_evaluate_exit", new_callable=AsyncMock) as mock_exit:
            mock_exit.return_value = exit_decision
            await pipeline.run_tick(
                ctx,
                credit_spread_position=CreditSpreadPosition(
                    entry_credit=100.0,
                    current_close_cost=35.0,
                ),
            )

        self.assertIn("PAPER_EXIT", [row["event"] for row in logger.rows])

    async def test_paper_mode_skips_ticks_on_weekend(self) -> None:
        logger = _RecordingPaperLogger()
        pipeline = SessionPipeline(_PaperFakeProvider(), tick_lock=NullTickLock(), dry_run=True)
        now_fn, sleep_fn = _advancing_clock(datetime(2026, 6, 6, 10, 0, tzinfo=IST))
        runner = PaperSoakRunner(
            pipeline,
            session_id="paper-weekend-01",
            logger=logger,
            tick_seconds=60.0,
            duration_hours=0.05,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
        )
        await runner.run()
        self.assertGreater(runner.stats.skipped_non_trading_days, 0)
        self.assertEqual(runner.stats.total_ticks, 0)

    async def test_paper_mode_skips_ticks_on_nse_holiday(self) -> None:
        logger = _RecordingPaperLogger()
        pipeline = SessionPipeline(_PaperFakeProvider(), tick_lock=NullTickLock(), dry_run=True)
        holiday = date(2026, 3, 26)
        self.assertIn(holiday, NSE_HOLIDAYS)
        now_fn, sleep_fn = _advancing_clock(
            datetime(holiday.year, holiday.month, holiday.day, 10, 0, tzinfo=IST)
        )
        runner = PaperSoakRunner(
            pipeline,
            session_id="paper-holiday-01",
            logger=logger,
            tick_seconds=60.0,
            duration_hours=0.05,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
        )
        await runner.run()
        self.assertGreater(runner.stats.skipped_non_trading_days, 0)
        self.assertEqual(runner.stats.total_ticks, 0)

    async def test_paper_mode_releases_tick_lock_on_ctrl_c(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "paper.lock"
            logger = _RecordingPaperLogger()
            pipeline = SessionPipeline(
                _PaperFakeProvider(),
                tick_lock=__import__(
                    "src.orchestration.tick_lock", fromlist=["FileTickLock"]
                ).FileTickLock(lock_path),
                dry_run=True,
            )
            runner = PaperSoakRunner(
                pipeline,
                session_id="paper-stop-01",
                logger=logger,
                tick_seconds=60.0,
                duration_hours=10.0,
                now_fn=lambda: datetime(2026, 6, 2, 10, 0, tzinfo=IST),
                sleep_fn=lambda _s: asyncio.sleep(0),
            )
            runner.request_stop()
            await runner.run()
            second = __import__(
                "src.orchestration.tick_lock", fromlist=["FileTickLock"]
            ).FileTickLock(lock_path)
            second.acquire(blocking=False)
            second.release()

    async def test_paper_mode_writes_summary_row_on_completion(self) -> None:
        logger = _RecordingPaperLogger()
        pipeline = SessionPipeline(
            _PaperFakeProvider(),
            tick_lock=NullTickLock(),
            dry_run=True,
            paper_logger=logger,
        )
        now_fn, sleep_fn = _advancing_clock(datetime(2026, 6, 2, 10, 0, tzinfo=IST))
        runner = PaperSoakRunner(
            pipeline,
            session_id="paper-summary-01",
            logger=logger,
            tick_seconds=60.0,
            duration_hours=0.05,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
        )
        summary = await runner.run(AgentContext(session_id="paper-summary-01", dte=3))
        self.assertEqual(summary["event"], "paper_soak_complete")
        self.assertIn("paper_soak_complete", [row["event"] for row in logger.rows])


class PaperModeHelperTests(unittest.TestCase):
    def test_build_paper_tick_row_contains_required_fields(self) -> None:
        ctx = AgentContext(
            session_id="paper-row-01",
            baseline_initialized=True,
            feature_snapshot_price=102.0,
            regime_decision=RegimeLabel.RANGE,
            strategy_decision=StrategyDecision(
                strategy="iron_condor",
                supporting_signals=["ad_ratio=1.05", "vix=14.00"],
            ),
            critic_decision=CriticDecision(
                status=CriticStatus.APPROVE,
                reason="math_checks_passed",
            ),
            gatekeeper_decision=GatekeeperDecision(
                verdict=GatekeeperVerdict.APPROVE,
                reason="approved",
                expected_round_trip_cost=40.0,
            ),
        )
        row = build_paper_tick_row(
            session_id="paper-row-01",
            tick_number=1,
            ctx=ctx,
            elapsed_ms=142.3,
            live_underlying_ltp=103.2,
        )
        self.assertEqual(row["event"], "paper_tick")
        self.assertAlmostEqual(row["stale_quote_distance"], 1.2)
        self.assertEqual(row["final_outcome"], "WOULD_TRADE")
        self.assertIn("WOULD_TRADE", row["decision_summary"])
        self.assertIn("iron_condor", row["decision_summary"])

    def test_jsonl_logger_writes_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.jsonl"
            logger = JsonlPaperLogger(path)
            logger.log_paper_row({"event": "paper_tick", "tick_number": 1})
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["tick_number"], 1)

    def test_build_paper_soak_summary(self) -> None:
        from src.orchestration.paper_mode import PaperSoakStats

        stats = PaperSoakStats(total_ticks=3, paper_approves=1)
        summary = build_paper_soak_summary(stats, session_id="paper-01")
        self.assertEqual(summary["would_have_traded_count"], 1)

    def test_default_paper_tick_lock_path_windows_safe(self) -> None:
        path = default_paper_tick_lock_path()
        self.assertTrue(str(path).endswith("a2a-paper-tick.lock"))

    def test_paper_tick_lock_path_per_index(self) -> None:
        path = paper_tick_lock_path_for("sensex")
        self.assertIn("sensex", str(path))

    @patch("src.orchestration.paper_mode.get_fyers_credentials", return_value=("app", "token"))
    @patch("src.orchestration.paper_mode.FyersMarketDataProvider")
    def test_build_soak_runner_all_creates_multi_runner(
        self,
        _provider_cls: Any,
        _creds: Any,
    ) -> None:
        runner = build_soak_runner(
            index_symbol="all",
            session_id="paper-multi-test01",
            log_dir=Path(tempfile.gettempdir()),
        )
        self.assertIsInstance(runner, MultiIndexPaperSoakRunner)
        self.assertEqual(len(runner._lanes), 3)
        keys = {lane.contract.key for lane in runner._lanes}
        self.assertEqual(keys, {"nifty", "banknifty", "sensex"})


if __name__ == "__main__":
    unittest.main()
