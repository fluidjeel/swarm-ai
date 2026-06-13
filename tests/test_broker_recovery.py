"""Tests for broker state recovery and session clock (Step 2)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from src.core.context import AgentContext, OpenPosition
from src.data.base_provider import FyersAuthError, MarketDataError, OptionGreeks, Quote
from src.data.fyers_provider import _parse_positions
from src.orchestration.broker_recovery import OrphanLegError, PartialFillError, rebuild_from_fyers
from src.orchestration.session_clock import MarketPhase, current_phase, is_trading_day
from src.orchestration.session_pipeline import SessionPipeline, SessionPipelineError

IST = timezone(timedelta(hours=5, minutes=30))


class _RecordingBootLogger:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def log_boot_row(self, row: dict) -> None:
        self.rows.append(row)


class _FakeProvider:
    def __init__(
        self,
        *,
        positions: list[OpenPosition] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self._positions = positions if positions is not None else []
        self._fail_with = fail_with

    async def get_index_ltp(self, symbol: str) -> float:
        return 24850.5

    async def get_vix(self) -> float:
        return 14.5

    async def get_option_chain_pcr(self, symbol: str = "NSE:NIFTY50-INDEX", *, strikecount: int = 50):
        from datetime import timedelta, timezone

        from src.data.base_provider import OptionChainPcr

        expiry = datetime.now(timezone.utc) + timedelta(days=7)
        return OptionChainPcr(
            pcr=1.05,
            call_oi=1000,
            put_oi=1050,
            expiry_timestamp=int(expiry.timestamp()),
            symbol=symbol,
        )

    async def get_nifty50_ad_ratio(self):
        from src.data.base_provider import BreadthSnapshot

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
        ]

    async def get_positions(self) -> list[OpenPosition]:
        if self._fail_with is not None:
            raise self._fail_with
        return list(self._positions)

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
            bid=99.0,
            ask=101.0,
            ltp=100.0,
            spread_pct=0.02,
            underlying_ltp=25000.0,
        )


def _ist(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=IST)


def _iron_condor_response() -> dict:
    return {
        "s": "ok",
        "netPositions": [
            {
                "symbol": "NSE:NIFTY24JUN24000PE",
                "netQty": 50,
                "avgPrice": 80.0,
                "option_type": "PE",
                "strike_price": 24000,
                "expiry": "24JUN24",
            },
            {
                "symbol": "NSE:NIFTY24JUN24100PE",
                "netQty": -50,
                "avgPrice": 120.0,
                "option_type": "PE",
                "strike_price": 24100,
                "expiry": "24JUN24",
            },
            {
                "symbol": "NSE:NIFTY24JUN25000CE",
                "netQty": -50,
                "avgPrice": 90.0,
                "option_type": "CE",
                "strike_price": 25000,
                "expiry": "24JUN24",
            },
            {
                "symbol": "NSE:NIFTY24JUN25100CE",
                "netQty": 50,
                "avgPrice": 60.0,
                "option_type": "CE",
                "strike_price": 25100,
                "expiry": "24JUN24",
            },
        ],
    }


class BrokerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_rebuild_sets_open_position_from_broker(self) -> None:
        legs = [
            OpenPosition(
                symbol="NSE:NIFTY24JUN24900CE",
                strategy="bull_call_spread",
                lots=1,
                entry_price=100.0,
                leg_id="NSE:NIFTY24JUN24900CE",
                strategy_id="bull_call_spread",
            ),
            OpenPosition(
                symbol="NSE:NIFTY24JUN25200CE",
                strategy="bull_call_spread",
                lots=1,
                entry_price=80.0,
                leg_id="NSE:NIFTY24JUN25200CE",
                strategy_id="bull_call_spread",
            ),
        ]
        provider = _FakeProvider(positions=legs)
        boot = _RecordingBootLogger()
        ctx = AgentContext(session_id="recovery-session-01")

        result = await rebuild_from_fyers(provider, ctx, boot_logger=boot)

        self.assertEqual(result.open_position.strategy, "bull_call_spread")
        self.assertEqual(len(result.open_position.legs), 2)
        self.assertEqual(boot.rows[-1]["outcome"], "position_recovered")

    async def test_rebuild_with_empty_positions_clears_ctx(self) -> None:
        ctx = AgentContext(
            session_id="recovery-session-02",
            open_position=OpenPosition(
                symbol="NSE:OLD",
                strategy="bull_call_spread",
                lots=1,
                entry_price=50.0,
            ),
        )
        boot = _RecordingBootLogger()
        result = await rebuild_from_fyers(_FakeProvider(positions=[]), ctx, boot_logger=boot)

        self.assertIsNone(result.open_position)
        self.assertEqual(boot.rows[-1]["outcome"], "no_positions")

    async def test_rebuild_raises_on_broker_5xx(self) -> None:
        provider = _FakeProvider(fail_with=MarketDataError("positions broker unavailable (code=503)"))
        boot = _RecordingBootLogger()
        ctx = AgentContext(session_id="recovery-session-03")

        with self.assertRaises(MarketDataError):
            await rebuild_from_fyers(provider, ctx, boot_logger=boot)

        self.assertEqual(boot.rows[-1]["outcome"], "broker_error")

    async def test_rebuild_detects_orphan_leg_for_iron_condor(self) -> None:
        position = OpenPosition(
            symbol="NSE:NIFTY-OPT-CE",
            strategy="iron_condor",
            lots=1,
            entry_price=100.0,
            leg_id="NSE:NIFTY-OPT-CE",
            strategy_id="iron_condor",
        )
        boot = _RecordingBootLogger()
        ctx = AgentContext(session_id="recovery-session-04")

        with self.assertRaises(OrphanLegError):
            await rebuild_from_fyers(_FakeProvider(positions=[position]), ctx, boot_logger=boot)

        self.assertEqual(boot.rows[-1]["outcome"], "orphan_leg_detected")

    async def test_rebuild_handles_multi_position_gracefully(self) -> None:
        full_condor = _parse_positions(_iron_condor_response())
        orphan = OpenPosition(
            symbol="NSE:NIFTY24JUN25200CE",
            strategy="iron_condor",
            lots=1,
            entry_price=200.0,
            leg_id="NSE:NIFTY24JUN25200CE",
            strategy_id="iron_condor_alt",
        )
        boot = _RecordingBootLogger()
        ctx = AgentContext(session_id="recovery-session-05")

        result = await rebuild_from_fyers(
            _FakeProvider(positions=[*full_condor, orphan]),
            ctx,
            boot_logger=boot,
        )

        self.assertEqual(result.open_position.strategy, "iron_condor")
        self.assertEqual(len(result.open_position.legs), 4)
        dropped_rows = [row for row in boot.rows if row.get("outcome") == "multi_group_kept_largest"]
        self.assertEqual(len(dropped_rows), 1)
        self.assertIn("iron_condor_alt", dropped_rows[0]["detail"])
        self.assertEqual(boot.rows[-1]["outcome"], "position_recovered")

    async def test_rebuild_aggregates_4_legs_iron_condor(self) -> None:
        parsed = _parse_positions(_iron_condor_response())
        boot = _RecordingBootLogger()
        ctx = AgentContext(session_id="recovery-session-07")

        result = await rebuild_from_fyers(_FakeProvider(positions=parsed), ctx, boot_logger=boot)

        self.assertIsNotNone(result.open_position)
        self.assertEqual(result.open_position.strategy, "iron_condor")
        self.assertEqual(result.open_position.symbol, "iron_condor_summary")
        self.assertIsNotNone(result.open_position.legs)
        self.assertEqual(len(result.open_position.legs), 4)
        self.assertEqual(boot.rows[-1]["outcome"], "position_recovered")

    async def test_recovered_multi_leg_position_legs_are_iterable(self) -> None:
        parsed = _parse_positions(_iron_condor_response())
        boot = _RecordingBootLogger()
        ctx = AgentContext(session_id="recovery-session-07b")

        result = await rebuild_from_fyers(_FakeProvider(positions=parsed), ctx, boot_logger=boot)

        legs = list(result.open_position.legs)
        self.assertEqual(len(legs), 4)
        self.assertTrue(all(leg.symbol for leg in legs))
        self.assertTrue(all(leg.entry_price > 0 for leg in legs))
        self.assertTrue(all(leg.strategy == "iron_condor" for leg in legs))

    async def test_rebuild_raises_partial_fill_for_2_iron_condor_legs(self) -> None:
        legs = _parse_positions(_iron_condor_response())[:2]
        boot = _RecordingBootLogger()
        ctx = AgentContext(session_id="recovery-session-08")

        with self.assertRaises(PartialFillError):
            await rebuild_from_fyers(_FakeProvider(positions=legs), ctx, boot_logger=boot)

        self.assertEqual(boot.rows[-1]["outcome"], "partial_fill_detected")

    async def test_rebuild_keeps_one_leg_when_two_single_leg_groups(self) -> None:
        legs = [
            OpenPosition(
                symbol="NSE:NIFTY24JUN24900CE",
                strategy="bull_call_spread",
                lots=1,
                entry_price=90.0,
                leg_id="NSE:NIFTY24JUN24900CE",
                strategy_id="bull_call_spread_a",
            ),
            OpenPosition(
                symbol="NSE:NIFTY24JUN25200CE",
                strategy="bull_call_spread",
                lots=1,
                entry_price=80.0,
                leg_id="NSE:NIFTY24JUN25200CE",
                strategy_id="bull_call_spread_b",
            ),
        ]
        boot = _RecordingBootLogger()
        ctx = AgentContext(session_id="recovery-session-09")

        with self.assertRaises(OrphanLegError):
            await rebuild_from_fyers(_FakeProvider(positions=legs), ctx, boot_logger=boot)

        dropped_rows = [row for row in boot.rows if row.get("outcome") == "multi_group_kept_largest"]
        self.assertEqual(len(dropped_rows), 1)
        self.assertEqual(boot.rows[-1]["outcome"], "orphan_leg_detected")

    async def test_rebuild_keeps_largest_group(self) -> None:
        single_leg = OpenPosition(
            symbol="NSE:NIFTY24JUN24900CE",
            strategy="bull_call_spread",
            lots=1,
            entry_price=100.0,
            leg_id="NSE:NIFTY24JUN24900CE",
            strategy_id="bull_call_spread",
        )
        iron_condor_legs = _parse_positions(_iron_condor_response())
        boot = _RecordingBootLogger()
        ctx = AgentContext(session_id="recovery-session-10")

        result = await rebuild_from_fyers(
            _FakeProvider(positions=[single_leg, *iron_condor_legs]),
            ctx,
            boot_logger=boot,
        )

        self.assertEqual(result.open_position.strategy, "iron_condor")
        self.assertEqual(len(result.open_position.legs), 4)
        dropped_rows = [row for row in boot.rows if row.get("outcome") == "multi_group_kept_largest"]
        self.assertEqual(len(dropped_rows), 1)
        self.assertIn("bull_call_spread", dropped_rows[0]["detail"])

    async def test_rebuild_end_to_end_untagged_iron_condor(self) -> None:
        parsed = _parse_positions(_iron_condor_response())
        boot = _RecordingBootLogger()
        ctx = AgentContext(session_id="recovery-session-11")

        result = await rebuild_from_fyers(_FakeProvider(positions=parsed), ctx, boot_logger=boot)

        self.assertTrue(all(leg.strategy_id == "iron_condor" for leg in parsed))
        self.assertIsNotNone(result.open_position)
        self.assertEqual(len(result.open_position.legs), 4)


class SessionClockTests(unittest.TestCase):
    def test_session_clock_blocks_pre_open(self) -> None:
        now = _ist(2026, 6, 9, 8, 30)  # Tuesday
        self.assertEqual(current_phase(now), MarketPhase.PRE_OPEN)
        self.assertTrue(is_trading_day(now))

    def test_session_clock_allows_intraday(self) -> None:
        now = _ist(2026, 6, 9, 10, 0)
        self.assertEqual(current_phase(now), MarketPhase.INTRADAY)
        self.assertTrue(is_trading_day(now))

    def test_session_clock_blocks_closed(self) -> None:
        after_hours = _ist(2026, 6, 9, 16, 0)
        self.assertEqual(current_phase(after_hours), MarketPhase.CLOSED)

        weekend = _ist(2026, 6, 7, 10, 0)  # Saturday
        self.assertFalse(is_trading_day(weekend))
        self.assertEqual(current_phase(weekend), MarketPhase.CLOSED)


class BootstrapSessionTests(unittest.IsolatedAsyncioTestCase):
    async def _run_bootstrap_failure_test(self, exc: Exception) -> None:
        boot = _RecordingBootLogger()
        pipeline = SessionPipeline(_FakeProvider(), boot_logger=boot)
        ctx = AgentContext(session_id="bootstrap-failure-session")
        intraday = _ist(2026, 6, 9, 10, 0)

        with patch(
            "src.orchestration.session_pipeline.rebuild_from_fyers",
            new_callable=AsyncMock,
        ) as mock_rebuild:
            mock_rebuild.side_effect = exc
            with self.assertRaises(type(exc)):
                await pipeline.bootstrap_session(ctx, now_ist=intraday, boot_writer=boot)

        failed_rows = [row for row in boot.rows if row.get("outcome") == "bootstrap_failed"]
        self.assertEqual(len(failed_rows), 1)
        self.assertIn(type(exc).__name__, failed_rows[0]["detail"])
        self.assertIn(str(exc), failed_rows[0]["detail"])

    async def test_bootstrap_logs_failure_on_orphan_leg(self) -> None:
        await self._run_bootstrap_failure_test(
            OrphanLegError("Orphan leg: strategy iron_condor expects 4 legs, broker returned 1.")
        )

    async def test_bootstrap_logs_failure_on_broker_error(self) -> None:
        await self._run_bootstrap_failure_test(
            MarketDataError("positions broker unavailable (code=503)")
        )

    async def test_bootstrap_logs_failure_on_auth_error(self) -> None:
        await self._run_bootstrap_failure_test(
            FyersAuthError("positions auth failed (code=401): token expired")
        )

    async def test_untagged_4_legs_inferred_as_iron_condor(self) -> None:
        parsed = _parse_positions(_iron_condor_response())
        self.assertEqual(len(parsed), 4)
        self.assertTrue(all(pos.strategy == "iron_condor" for pos in parsed))
        self.assertTrue(all(pos.strategy_id == "iron_condor" for pos in parsed))

        boot = _RecordingBootLogger()
        ctx = AgentContext(session_id="recovery-session-06")
        result = await rebuild_from_fyers(_FakeProvider(positions=parsed), ctx, boot_logger=boot)

        self.assertEqual(len(result.open_position.legs), 4)
        self.assertEqual(result.open_position.strategy, "iron_condor")
        self.assertEqual(boot.rows[-1]["outcome"], "position_recovered")

    async def test_bootstrap_logs_baseline_init_failure(self) -> None:
        boot = _RecordingBootLogger()

        class _FailingLtpProvider(_FakeProvider):
            async def get_index_ltp(self, symbol: str) -> float:
                raise MarketDataError("ltp unavailable")

        pipeline = SessionPipeline(_FailingLtpProvider(), boot_logger=boot)
        ctx = AgentContext(session_id="bootstrap-baseline-fail")
        intraday = _ist(2026, 6, 9, 10, 0)

        with patch(
            "src.orchestration.session_pipeline.rebuild_from_fyers",
            new_callable=AsyncMock,
        ) as mock_rebuild:
            mock_rebuild.return_value = ctx
            result = await pipeline.bootstrap_session(ctx, now_ist=intraday, boot_writer=boot)

        self.assertFalse(result.baseline_initialized)
        failed_rows = [row for row in boot.rows if row.get("outcome") == "baseline_init_failed"]
        self.assertEqual(len(failed_rows), 1)
        self.assertIn("MarketDataError", failed_rows[0]["detail"])

    async def test_bootstrap_session_calls_recovery(self) -> None:
        position = OpenPosition(
            symbol="NSE:NIFTY24JUN24900CE",
            strategy="bull_call_spread",
            lots=1,
            entry_price=100.0,
        )
        provider = _FakeProvider(positions=[position])
        boot = _RecordingBootLogger()
        pipeline = SessionPipeline(provider, boot_logger=boot)
        ctx = AgentContext(session_id="bootstrap-session-01")
        intraday = _ist(2026, 6, 9, 10, 0)

        with patch(
            "src.orchestration.session_pipeline.rebuild_from_fyers",
            new_callable=AsyncMock,
        ) as mock_rebuild:
            mock_rebuild.return_value = ctx.update(open_position=position)
            result = await pipeline.bootstrap_session(ctx, now_ist=intraday, boot_writer=boot)

        mock_rebuild.assert_awaited_once()
        self.assertEqual(result.open_position, position)
        success_rows = [row for row in boot.rows if row.get("outcome") == "bootstrap_success"]
        self.assertEqual(len(success_rows), 1)


if __name__ == "__main__":
    unittest.main()
