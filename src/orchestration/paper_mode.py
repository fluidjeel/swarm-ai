"""Paper-mode soak runner: live Fyers data, no orders (Step 4.6)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from src.config.secrets import get_fyers_credentials, load_project_env
from src.core.context import AgentContext, CriticStatus
from src.data.fyers_provider import FyersMarketDataProvider
from src.execution.fyers_port import FyersExecutionPort
from src.execution.noop_port import NoOpExecutionPort
from src.observability.tick_trace import (
    JsonlTickTraceWriter,
    build_tick_trace_row,
    default_tick_trace_path,
)
from src.orchestration.runtime_guards import (
    MemoryGuardError,
    JsonlHeartbeatWriter,
    check_memory_usage,
    default_heartbeat_path,
    write_tick_heartbeat,
)
from src.orchestration.session_clock import IST, current_phase, is_trading_day
from src.orchestration.session_pipeline import SessionPipeline, SessionPipelineError
from src.orchestration.tick_lock import FileTickLock
from src.risk.exit_engine import ExitAction
from src.risk.gatekeeper import GatekeeperVerdict

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_DIR = ROOT / "logs" / "paper_soak"
DEFAULT_TICK_SECONDS = 300
DEFAULT_SOAK_HOURS = 4.0


class PaperEventLogger(Protocol):
    def log_paper_row(self, row: dict[str, Any]) -> None: ...


class JsonlPaperLogger:
    """Append-only JSONL logger for paper soak ticks."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log_paper_row(self, row: dict[str, Any]) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")


@dataclass
class PaperSoakStats:
    total_ticks: int = 0
    skipped_non_trading_days: int = 0
    paper_approves: int = 0
    paper_exits: int = 0
    critic_rejects: dict[str, int] = field(default_factory=dict)
    gatekeeper_rejects: dict[str, int] = field(default_factory=dict)
    max_stale_quote_distance: float = 0.0
    total_elapsed_ms: float = 0.0
    broker_errors: int = 0

    def record_critic(self, reason: str | None) -> None:
        if not reason:
            return
        self.critic_rejects[reason] = self.critic_rejects.get(reason, 0) + 1

    def record_gatekeeper(self, rule_id: str | None, reason: str | None) -> None:
        key = rule_id or reason or "unknown"
        self.gatekeeper_rejects[key] = self.gatekeeper_rejects.get(key, 0) + 1


def default_paper_tick_lock_path() -> Path:
    custom = os.getenv("PAPER_TICK_LOCK_PATH")
    if custom:
        return Path(custom)
    if sys.platform == "win32":
        return Path(tempfile.gettempdir()) / "a2a-paper-tick.lock"
    return Path("/var/lock/a2a-paper-tick.lock")


def _serialize_critic(ctx: AgentContext) -> dict[str, Any] | None:
    if ctx.critic_decision is None:
        return None
    return {
        "status": ctx.critic_decision.status.value,
        "reason": ctx.critic_decision.reason,
    }


def _serialize_gatekeeper(ctx: AgentContext) -> dict[str, Any] | None:
    decision = ctx.gatekeeper_decision
    if decision is None:
        return None
    rule_id = getattr(decision, "rule_id", None)
    return {
        "verdict": decision.verdict.value,
        "rule_id": rule_id.value if rule_id is not None else None,
        "expected_round_trip_cost": getattr(decision, "expected_round_trip_cost", 0.0),
        "reason": getattr(decision, "reason", ""),
    }


def _serialize_open_position(ctx: AgentContext) -> dict[str, Any] | None:
    position = ctx.open_position
    if position is None:
        return None
    return {
        "symbol": position.symbol,
        "strategy": position.strategy,
        "lots": position.lots,
        "leg_count": len(position.legs) if position.legs else 1,
    }


def build_paper_tick_row(
    *,
    session_id: str,
    tick_number: int,
    ctx: AgentContext,
    elapsed_ms: float,
    live_underlying_ltp: float | None,
    exit_action: str | None = None,
) -> dict[str, Any]:
    stale_distance: float | None = None
    if live_underlying_ltp is not None and ctx.feature_snapshot_price is not None:
        stale_distance = abs(live_underlying_ltp - ctx.feature_snapshot_price)

    return {
        "event": "paper_tick",
        "session_id": session_id,
        "timestamp": datetime.now(IST).isoformat(),
        "tick_number": tick_number,
        "phase": current_phase(datetime.now(IST)).value,
        "regime_decision": ctx.regime_decision.value if ctx.regime_decision else None,
        "strategy_decision": (
            ctx.strategy_decision.strategy if ctx.strategy_decision else None
        ),
        "critic_decision": _serialize_critic(ctx),
        "gatekeeper_decision": _serialize_gatekeeper(ctx),
        "open_position": _serialize_open_position(ctx),
        "baseline_initialized": ctx.baseline_initialized,
        "feature_snapshot_price": ctx.feature_snapshot_price,
        "stale_quote_distance": (
            round(stale_distance, 3) if stale_distance is not None else None
        ),
        "exit_action": exit_action,
        "elapsed_ms": round(elapsed_ms, 3),
    }


def build_paper_soak_summary(stats: PaperSoakStats, *, session_id: str) -> dict[str, Any]:
    return {
        "event": "paper_soak_complete",
        "session_id": session_id,
        "timestamp": datetime.now(IST).isoformat(),
        "total_ticks": stats.total_ticks,
        "skipped_non_trading_days": stats.skipped_non_trading_days,
        "paper_approves": stats.paper_approves,
        "paper_exits": stats.paper_exits,
        "critic_rejects": stats.critic_rejects,
        "gatekeeper_rejects": stats.gatekeeper_rejects,
        "max_stale_quote_distance": round(stats.max_stale_quote_distance, 3),
        "total_elapsed_ms": round(stats.total_elapsed_ms, 3),
        "broker_errors": stats.broker_errors,
        "would_have_traded_count": stats.paper_approves,
    }


def print_paper_soak_summary(summary: dict[str, Any]) -> None:
    print("\n=== Paper Soak Summary ===")
    print(f"Session: {summary['session_id']}")
    print(f"Total ticks: {summary['total_ticks']}")
    print(f"Paper approves (would-have-traded): {summary['paper_approves']}")
    print(f"Paper exits: {summary['paper_exits']}")
    print(f"Broker errors: {summary['broker_errors']}")
    print(f"Max stale quote distance: {summary['max_stale_quote_distance']}")
    if summary["critic_rejects"]:
        print(f"Critic rejects: {summary['critic_rejects']}")
    if summary["gatekeeper_rejects"]:
        print(f"Gatekeeper rejects: {summary['gatekeeper_rejects']}")


class PaperSoakRunner:
    """Runs SessionPipeline in dry-run mode against live Fyers data."""

    def __init__(
        self,
        pipeline: SessionPipeline,
        *,
        session_id: str,
        logger: PaperEventLogger,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        duration_hours: float = DEFAULT_SOAK_HOURS,
        sleep_fn: Callable[[float], Any] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        wall_now_fn: Callable[[], datetime] | None = None,
        memory_guard_enabled: bool = True,
    ) -> None:
        self._pipeline = pipeline
        self._session_id = session_id
        self._logger = logger
        self._tick_seconds = tick_seconds
        self._duration = timedelta(hours=duration_hours)
        self._sleep = sleep_fn or asyncio.sleep
        self._now = now_fn or (lambda: datetime.now(IST))
        self._wall_now = wall_now_fn or (lambda: datetime.now(IST))
        self._stats = PaperSoakStats()
        self._stop = False
        self._tick_number = 0
        self._started_at: datetime | None = None
        self._started_at_wall: datetime | None = None
        self._heartbeat: JsonlHeartbeatWriter | None = None
        self._tick_trace: JsonlTickTraceWriter | None = None
        self._memory_guard_enabled = memory_guard_enabled

    def configure_observability(
        self,
        *,
        heartbeat: JsonlHeartbeatWriter | None = None,
        tick_trace: JsonlTickTraceWriter | None = None,
    ) -> None:
        self._heartbeat = heartbeat
        self._tick_trace = tick_trace

    def request_stop(self) -> None:
        self._stop = True

    @property
    def stats(self) -> PaperSoakStats:
        return self._stats

    async def run(self, ctx: AgentContext | None = None) -> dict[str, Any]:
        ctx = ctx or AgentContext(session_id=self._session_id)
        self._started_at = self._now()
        self._started_at_wall = self._wall_now()

        try:
            if is_trading_day(self._started_at):
                try:
                    ctx = await self._pipeline.bootstrap_session(ctx, now_ist=self._started_at)
                except SessionPipelineError as exc:
                    self._logger.log_paper_row(
                        {
                            "event": "paper_bootstrap_failed",
                            "session_id": self._session_id,
                            "timestamp": self._now().isoformat(),
                            "detail": str(exc),
                            "code": exc.code,
                        }
                    )
            else:
                self._stats.skipped_non_trading_days += 1

            while not self._stop and self._within_duration():
                now = self._now()
                if not is_trading_day(now):
                    self._stats.skipped_non_trading_days += 1
                    await self._sleep(self._tick_seconds)
                    continue

                tick_start = time.perf_counter()
                exit_action: str | None = None
                live_ltp: float | None = None
                memory_pct = 0.0
                if self._memory_guard_enabled:
                    try:
                        memory_pct = check_memory_usage().percent_used
                    except MemoryGuardError as exc:
                        self._stats.broker_errors += 1
                        self._logger.log_paper_row(
                            {
                                "event": "paper_tick_error",
                                "session_id": self._session_id,
                                "timestamp": now.isoformat(),
                                "detail": str(exc),
                                "code": "MEMORY_GUARD",
                            }
                        )
                        self.request_stop()
                        break
                try:
                    result = await self._pipeline.run_tick(ctx)
                    ctx = result.ctx
                    live_ltp = result.live_underlying_ltp
                    if result.exit_decision is not None:
                        exit_action = result.exit_decision.action.value
                        if result.exit_decision.action == ExitAction.EXIT_MARKET:
                            self.record_exit()
                    elapsed_ms = result.elapsed_ms or (
                        (time.perf_counter() - tick_start) * 1000
                    )
                    self._record_tick_stats(ctx, elapsed_ms, live_ltp)
                    self._tick_number += 1
                    self._stats.total_ticks += 1
                    self._logger.log_paper_row(
                        build_paper_tick_row(
                            session_id=self._session_id,
                            tick_number=self._tick_number,
                            ctx=ctx,
                            elapsed_ms=elapsed_ms,
                            live_underlying_ltp=live_ltp,
                            exit_action=exit_action,
                        )
                    )
                    if self._tick_trace is not None:
                        self._tick_trace.write_tick(
                            build_tick_trace_row(
                                session_id=self._session_id,
                                tick_number=self._tick_number,
                                ctx=ctx,
                                elapsed_ms=elapsed_ms,
                                phase=current_phase(now).value,
                            )
                        )
                    if self._heartbeat is not None:
                        write_tick_heartbeat(
                            self._heartbeat,
                            session_id=self._session_id,
                            tick_number=self._tick_number,
                            memory_pct=memory_pct,
                            elapsed_ms=elapsed_ms,
                        )
                except SessionPipelineError as exc:
                    self._stats.broker_errors += 1
                    self._logger.log_paper_row(
                        {
                            "event": "paper_tick_error",
                            "session_id": self._session_id,
                            "timestamp": now.isoformat(),
                            "detail": str(exc),
                            "code": exc.code,
                        }
                    )

                await self._sleep(self._tick_seconds)
        finally:
            self._pipeline.release_tick_lock()

        summary = build_paper_soak_summary(self._stats, session_id=self._session_id)
        self._logger.log_paper_row(summary)
        print_paper_soak_summary(summary)
        return summary

    def _within_duration(self) -> bool:
        if self._started_at_wall is None:
            return True
        return self._wall_now() - self._started_at_wall < self._duration

    def _record_tick_stats(
        self,
        ctx: AgentContext,
        elapsed_ms: float,
        live_ltp: float | None,
    ) -> None:
        self._stats.total_elapsed_ms += elapsed_ms
        if live_ltp is not None and ctx.feature_snapshot_price is not None:
            distance = abs(live_ltp - ctx.feature_snapshot_price)
            self._stats.max_stale_quote_distance = max(
                self._stats.max_stale_quote_distance,
                distance,
            )

        if ctx.critic_decision and ctx.critic_decision.status == CriticStatus.REJECT:
            self._stats.record_critic(ctx.critic_decision.reason)

        gatekeeper = ctx.gatekeeper_decision
        if gatekeeper is not None:
            if gatekeeper.verdict == GatekeeperVerdict.APPROVE:
                self._stats.paper_approves += 1
            elif gatekeeper.verdict == GatekeeperVerdict.REJECT:
                rule_id = getattr(gatekeeper, "rule_id", None)
                self._stats.record_gatekeeper(
                    rule_id.value if rule_id is not None else None,
                    getattr(gatekeeper, "reason", None),
                )

    def record_exit(self) -> None:
        self._stats.paper_exits += 1

    @classmethod
    def from_env(
        cls,
        *,
        logger: PaperEventLogger | None = None,
        exercise_broker: bool = False,
    ) -> PaperSoakRunner:
        load_project_env()
        app_id, access_token = get_fyers_credentials()
        provider = FyersMarketDataProvider(
            app_id=app_id,
            access_token=access_token,
            request_timeout_sec=60.0,
        )
        session_id = os.getenv("PAPER_SESSION_ID", f"paper-{uuid.uuid4().hex[:12]}")
        tick_seconds = float(os.getenv("PAPER_TICK_SECONDS", str(DEFAULT_TICK_SECONDS)))
        duration_hours = float(os.getenv("PAPER_SOAK_HOURS", str(DEFAULT_SOAK_HOURS)))
        log_dir = Path(os.getenv("PAPER_LOG_DIR", str(DEFAULT_LOG_DIR)))
        log_path = log_dir / f"{session_id}.jsonl"
        paper_logger = logger or JsonlPaperLogger(log_path)

        execution_port = (
            FyersExecutionPort(app_id=app_id, access_token=access_token)
            if exercise_broker
            else NoOpExecutionPort()
        )
        pipeline = SessionPipeline(
            provider,
            tick_lock=FileTickLock(default_paper_tick_lock_path()),
            dry_run=True,
            paper_logger=paper_logger,
            execution_port=execution_port,
            broker_sync=exercise_broker,
            memory_guard_enabled=True,
        )
        runner = cls(
            pipeline,
            session_id=session_id,
            logger=paper_logger,
            tick_seconds=tick_seconds,
            duration_hours=duration_hours,
        )
        runner.configure_observability(
            heartbeat=JsonlHeartbeatWriter(default_heartbeat_path()),
            tick_trace=JsonlTickTraceWriter(default_tick_trace_path(session_id)),
        )
        return runner


async def _async_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A2A paper-mode soak (no orders).")
    parser.add_argument(
        "--hours",
        type=float,
        default=None,
        help="Soak duration in hours (default: PAPER_SOAK_HOURS or 4).",
    )
    parser.add_argument(
        "--tick-seconds",
        type=float,
        default=None,
        help="Seconds between ticks (default: PAPER_TICK_SECONDS or 300).",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Offline mock soak (fake provider, no Fyers network). For CI / pre-flight.",
    )
    parser.add_argument(
        "--broker",
        action="store_true",
        help=(
            "Wire FyersExecutionPort: real orders + per-tick position reconcile. "
            "Default is NoOp (quotes/greeks only)."
        ),
    )
    args = parser.parse_args(argv)

    if args.mock:
        from src.orchestration.mock_soak import build_mock_runner

        runner = build_mock_runner(exercise_broker=args.broker)
    else:
        runner = PaperSoakRunner.from_env(exercise_broker=args.broker)
    if args.hours is not None:
        runner._duration = timedelta(hours=args.hours)
    if args.tick_seconds is not None:
        runner._tick_seconds = args.tick_seconds

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, runner.request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda _s, _f: runner.request_stop())

    await runner.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
