"""Session orchestration: Feature Engine → AgentContext → Exit Engine (v4.1)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

logger = logging.getLogger(__name__)

from src.agents.pre_trade_critic import validate_pre_trade
from src.features.math_utils import compute_atr
from src.agents.regime_classifier import classify_regime
from src.agents.strategy_selector import select_strategy
from src.agents.symbol_resolver import (
    ExpirySelectionError,
    NIFTY_INDEX_SYMBOL,
    StrikeSelectionError,
    leg_dte_for_expiry,
    select_expiry,
    select_strategy_symbols,
)
from src.config.index_contracts import IndexContract, resolve_index_contract
from src.config.risk_config import RiskConfig, load_risk_config
from src.core.context import (
    AgentContext,
    CriticDecision,
    CriticStatus,
    OpenPosition,
    RegimeLabel,
    StrategyName,
)
from src.data.base_provider import OptionGreeks
from src.execution.fill_reconcile import verify_entry_fills
from src.execution.noop_port import NoOpExecutionPort
from src.execution.port import ExecutionFailedError, ExecutionPort, LegActionIntent, OrderAck, idem_key
from src.orchestration.runtime_guards import MemoryGuardError, check_memory_usage
from src.data.base_provider import FyersAuthError, MarketDataError, MarketDataProvider, OhlcvBar, Quote
from src.risk.friction import (
    ENTRY_LEG_SIDES,
    compute_entry_credit_inr,
    compute_exit_close_cost_inr,
    estimate_max_profit_inr,
)
from src.risk.gatekeeper import GatekeeperVerdict, evaluate_from_context
from src.features.feature_engine import (
    FeatureEngineError,
    FeatureEngineErrorCode,
    SharedMarketSnapshot,
    compute_feature_payload,
    compute_index_feature_payload,
)
from src.orchestration.broker_recovery import (
    BootLogger,
    rebuild_from_fyers,
    reconcile_broker_state,
    sync_position_from_broker,
)
from src.orchestration.context_adapters import (
    apply_feature_payload,
    opening_regime_to_feature_payload,
    sync_circuit_breaker,
)
from src.orchestration.session_clock import IST, current_phase, is_session_start_allowed
from src.orchestration.tick_lock import FileTickLock, NullTickLock, TickLock, TickLockError
from src.observability.tick_trace import (
    TickTraceWriter,
    build_tick_trace_row,
    DynamoDBTickTraceWriter,
)
from src.risk.exit_engine import (
    CreditSpreadPosition,
    ExitAction,
    ExitDecision,
    ExitEngine,
)


DEFAULT_INDEX_SYMBOL = NIFTY_INDEX_SYMBOL
DURATION_LIMIT_FLATTEN_RULE = "duration_limit_emergency_flatten"
DURATION_LIMIT_FLATTEN_REASON = "Duration-Limit Emergency Flatten."
DURATION_LIMIT_FLATTEN_EVENT = "duration_limit_emergency_flatten"


def _default_tick_trace_writer() -> TickTraceWriter | None:
    if os.getenv("A2A_DISABLE_DDB_TRACES", "").lower() in ("1", "true", "yes"):
        return None
    if os.getenv("PYTEST_CURRENT_TEST"):
        return None
    return DynamoDBTickTraceWriter()


class SessionPipelineError(RuntimeError):
    """Raised when the session pipeline cannot complete a tick."""

    def __init__(
        self,
        message: str,
        *,
        code: FeatureEngineErrorCode | str = "PIPELINE",
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SessionTickResult:
    """Outcome of a single pipeline tick."""

    ctx: AgentContext
    exit_decision: ExitDecision | None = None
    live_underlying_ltp: float | None = None
    elapsed_ms: float | None = None


@dataclass(frozen=True, slots=True)
class ExitEvaluationResult:
    decision: ExitDecision
    per_leg_quotes: dict[str, Quote] | None = None


class BootEventWriter(Protocol):
    def log_boot_row(self, row: dict[str, Any]) -> None: ...


class PaperEventLogger(Protocol):
    def log_paper_row(self, row: dict[str, Any]) -> None: ...


class SessionPipeline:
    """
    Deterministic intraday loop (v4.1 core).

    Refreshes features, evaluates exits when a position is open, and enforces
    a cross-process tick lock. Agents 1–3 and broker execution are not wired yet.
    """

    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        exit_engine: ExitEngine | None = None,
        request_timeout_sec: float = 30.0,
        tick_timeout_sec: float = 120.0,
        pcr_history_path: Path | None = None,
        index_symbol: str = DEFAULT_INDEX_SYMBOL,
        nifty_symbol: str | None = None,
        session_open_vix: float | None = None,
        tick_lock: TickLock | None = None,
        enforce_tick_lock: bool = True,
        boot_logger: BootLogger | None = None,
        risk_config: RiskConfig | None = None,
        dry_run: bool = False,
        paper_logger: PaperEventLogger | None = None,
        execution_port: ExecutionPort | None = None,
        broker_sync: bool = False,
        reconcile_on_boot: bool = False,
        memory_guard_enabled: bool = False,
        tick_trace_writer: TickTraceWriter | None = None,
        enable_ddb_tick_trace: bool = True,
    ) -> None:
        self._provider = provider
        self._risk_config = risk_config or load_risk_config()
        self._exit_engine = exit_engine or ExitEngine(
            credit_stop_multiplier=self._risk_config.credit_stop_multiplier,
        )
        self._execution_port = execution_port or NoOpExecutionPort()
        bind_risk = getattr(self._provider, "bind_risk_config", None)
        if callable(bind_risk):
            bind_risk(self._risk_config)
        self._request_timeout_sec = request_timeout_sec
        self._tick_timeout_sec = tick_timeout_sec
        self._pcr_history_path = pcr_history_path
        resolved_symbol = index_symbol if nifty_symbol is None else nifty_symbol
        self._index_contract: IndexContract = resolve_index_contract(resolved_symbol)
        self._index_symbol = self._index_contract.symbol
        self._session_open_vix = session_open_vix
        self._boot_logger = boot_logger
        self._dry_run = dry_run
        self._paper_logger = paper_logger
        self._broker_sync = broker_sync
        self._reconcile_on_boot = reconcile_on_boot
        self._memory_guard_enabled = memory_guard_enabled
        self._tick_trace_writer = tick_trace_writer
        if self._tick_trace_writer is None and enable_ddb_tick_trace:
            self._tick_trace_writer = _default_tick_trace_writer()
        self._tick_number = 0
        if tick_lock is not None:
            self._tick_lock = tick_lock
        elif enforce_tick_lock:
            self._tick_lock = FileTickLock()
        else:
            self._tick_lock = NullTickLock()

    def release_tick_lock(self) -> None:
        """Release the tick lock (used by paper-mode shutdown)."""
        self._tick_lock.release()

    async def flatten_open_position_for_shutdown(self, ctx: AgentContext) -> AgentContext:
        """
        Mandatory flatten before session teardown (soak duration limit / graceful stop).

        Ensures no simulated risk remains in memory when the process exits.
        """
        position = ctx.open_position
        if position is None:
            return ctx

        exit_decision = self._build_duration_limit_flatten_decision(position)
        self._log_duration_limit_flatten(ctx, exit_decision)
        try:
            await self._execution_port.flatten_position(position)
        except LegFailedError:
            logger.warning(
                "Duration-limit flatten failed for session=%s symbol=%s",
                ctx.session_id,
                position.symbol,
                exc_info=True,
            )
            return ctx.update(execution_halted=True)

        self._session_open_vix = None
        return ctx.update(open_position=None)

    def _build_duration_limit_flatten_decision(self, position: OpenPosition) -> ExitDecision:
        base = self._exit_engine.build_emergency_flatten_decision(position)
        return ExitDecision(
            action=base.action,
            reason=DURATION_LIMIT_FLATTEN_REASON,
            rule_id=DURATION_LIMIT_FLATTEN_RULE,
            leg_action_intents=base.leg_action_intents,
        )

    def _log_duration_limit_flatten(
        self,
        ctx: AgentContext,
        exit_decision: ExitDecision,
    ) -> None:
        if self._paper_logger is None:
            return
        self._paper_logger.log_paper_row(
            {
                "event": DURATION_LIMIT_FLATTEN_EVENT,
                "session_id": ctx.session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "detail": DURATION_LIMIT_FLATTEN_REASON,
                "open_position": (
                    {
                        "symbol": ctx.open_position.symbol,
                        "strategy": ctx.open_position.strategy,
                    }
                    if ctx.open_position
                    else None
                ),
                "exit_action": exit_decision.action.value,
                "exit_reason": exit_decision.reason,
                "rule_id": exit_decision.rule_id,
                "leg_action_intents": [
                    {
                        "symbol": intent.symbol,
                        "action": intent.action,
                        "leg_id": intent.leg_id,
                    }
                    for intent in exit_decision.leg_action_intents
                ],
            }
        )
        self._maybe_log_paper_exit(ctx, exit_decision, per_leg_quotes=None)

    async def _persist_tick_trace(
        self,
        ctx: AgentContext,
        *,
        elapsed_ms: float,
        exit_decision: ExitDecision | None,
    ) -> None:
        if self._tick_trace_writer is None:
            return

        self._tick_number += 1
        row = build_tick_trace_row(
            session_id=ctx.session_id,
            tick_number=self._tick_number,
            ctx=ctx,
            elapsed_ms=elapsed_ms,
            phase=current_phase(datetime.now(IST)).value,
        )
        if exit_decision is not None:
            row["exit_action"] = exit_decision.action.value
            row["exit_reason"] = exit_decision.reason
            row["exit_rule_id"] = exit_decision.rule_id

        try:
            await asyncio.to_thread(self._tick_trace_writer.write_tick, row)
        except Exception:
            logger.warning(
                "Failed to persist tick trace for session=%s tick=%s",
                ctx.session_id,
                self._tick_number,
                exc_info=True,
            )

    async def bootstrap_session(
        self,
        ctx: AgentContext,
        *,
        now_ist: datetime | None = None,
        boot_writer: BootEventWriter | None = None,
    ) -> AgentContext:
        """
        Call once at 09:15 IST before the first tick.

        1. Rebuild open_position from Fyers GET /positions.
        2. Refuse to start outside INTRADAY / SQUARE_OFF.
        3. Log boot row with session_id and open_position state.
        """
        ist_now = now_ist or datetime.now(IST)
        if not is_session_start_allowed(ist_now):
            raise SessionPipelineError(
                f"Session bootstrap refused outside INTRADAY/SQUARE_OFF "
                f"(now={ist_now.isoformat()}).",
                code="SESSION_CLOCK",
            )

        writer = boot_writer or self._boot_logger

        try:
            ctx = await rebuild_from_fyers(
                self._provider,
                ctx,
                boot_logger=self._boot_logger,
            )
        except Exception as exc:
            if writer is not None:
                writer.log_boot_row(
                    {
                        "event": "session_bootstrap",
                        "session_id": ctx.session_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "phase_allowed": True,
                        "outcome": "bootstrap_failed",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
            raise

        # Baseline from broker LTP at boot — not the next 5-min bar close.
        # If LTP fetch fails, boot continues; first tick will retry baseline init.
        try:
            ltp = await self._provider.get_index_ltp(self._index_symbol)
            ctx = ctx.update(
                feature_snapshot_price=float(ltp),
                baseline_initialized=True,
            )
        except (MarketDataError, FyersAuthError) as exc:
            if writer is not None:
                writer.log_boot_row(
                    {
                        "event": "session_bootstrap",
                        "session_id": ctx.session_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "phase_allowed": True,
                        "outcome": "baseline_init_failed",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )

        if self._reconcile_on_boot:
            ctx, recon = await reconcile_broker_state(
                self._provider,
                ctx,
                boot_logger=self._boot_logger,
            )
            if not recon.ok and writer is not None:
                writer.log_boot_row(
                    {
                        "event": "session_bootstrap",
                        "session_id": ctx.session_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "phase_allowed": True,
                        "outcome": "reconciliation_halt",
                        "detail": "; ".join(recon.mismatches),
                    }
                )

        if writer is not None:
            writer.log_boot_row(
                {
                    "event": "session_bootstrap",
                    "session_id": ctx.session_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "phase_allowed": True,
                    "outcome": "bootstrap_success",
                    "open_position_symbol": (
                        ctx.open_position.symbol if ctx.open_position else None
                    ),
                    "has_open_position": ctx.has_open_position,
                    "baseline_initialized": ctx.baseline_initialized,
                    "feature_snapshot_price": ctx.feature_snapshot_price,
                    "reconciliation_halt": ctx.reconciliation_halt,
                }
            )

        return ctx

    async def run_tick(
        self,
        ctx: AgentContext,
        *,
        credit_spread_position: CreditSpreadPosition | None = None,
        nifty_bars: Sequence[OhlcvBar] | None = None,
        session_open_vix: float | None = None,
        shared_market: SharedMarketSnapshot | None = None,
    ) -> SessionTickResult:
        """
        Execute one deterministic pipeline tick.

        1. Acquire tick lock (blocks concurrent 5-minute loops).
        2. Refresh feature payload from market data into AgentContext.
        3. If halted with no open position, return (no new entries).
        4. If an open position exists, run Exit Engine; flatten on EXIT_MARKET.
        """
        tick_start = time.perf_counter()
        exit_decision: ExitDecision | None = None
        live_underlying_ltp: float | None = None
        tick_timestamp = datetime.now(IST).isoformat()
        try:
            self._tick_lock.acquire(blocking=False)
        except TickLockError as exc:
            raise SessionPipelineError(
                "Concurrent pipeline tick blocked by tick lock.",
                code="TICK_LOCK",
            ) from exc

        if self._memory_guard_enabled:
            try:
                check_memory_usage()
            except MemoryGuardError as exc:
                raise SessionPipelineError(str(exc), code="MEMORY_GUARD") from exc

        ctx = ctx.update(trace_id=uuid4().hex)

        async def _tick_body() -> SessionTickResult:
            # nonlocal so the `finally` trace + timeout path see the latest state
            # even if the body is cancelled mid-flight by the tick deadline.
            nonlocal ctx, exit_decision, live_underlying_ltp
            if ctx.reconciliation_halt:
                # Hard, human-gated stop: broker reality disagrees with our
                # belief. Take no automated action (no entries, no exits) until
                # an operator clears the flag.
                return SessionTickResult(
                    ctx=ctx,
                    elapsed_ms=(time.perf_counter() - tick_start) * 1000,
                )
            ctx = sync_circuit_breaker(ctx)
            ctx = await self._refresh_features(ctx, shared_market=shared_market)
            if self._broker_sync:
                prior_symbol = ctx.open_position.symbol if ctx.open_position else None
                ctx = await sync_position_from_broker(self._provider, ctx)
                if self._paper_logger is not None:
                    current_symbol = ctx.open_position.symbol if ctx.open_position else None
                    if prior_symbol != current_symbol:
                        self._paper_logger.log_paper_row(
                            {
                                "event": "BROKER_POSITION_SYNC",
                                "session_id": ctx.session_id,
                                "trace_id": ctx.trace_id,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "prior_symbol": prior_symbol,
                                "current_symbol": current_symbol,
                                "execution_halted": ctx.execution_halted,
                            }
                        )

            if ctx.is_halted and not ctx.has_open_position:
                return SessionTickResult(
                    ctx=ctx,
                    elapsed_ms=(time.perf_counter() - tick_start) * 1000,
                )

            if not ctx.has_open_position and not ctx.is_halted:
                ctx, live_underlying_ltp = await self._run_entry_chain(
                    ctx,
                    tick_timestamp=tick_timestamp,
                    nifty_bars=nifty_bars,
                )
                self._maybe_log_paper_approve(ctx)

            if not ctx.has_open_position:
                return SessionTickResult(
                    ctx=ctx,
                    live_underlying_ltp=live_underlying_ltp,
                    elapsed_ms=(time.perf_counter() - tick_start) * 1000,
                )

            exit_eval = await self._evaluate_exit(
                ctx,
                credit_spread_position=credit_spread_position,
                nifty_bars=nifty_bars,
                session_open_vix=session_open_vix,
            )
            exit_decision = exit_eval.decision

            if exit_decision.leg_action_intents and not self._dry_run:
                ctx = ctx.update(
                    exit_leg_intents=list(exit_decision.leg_action_intents),
                )

            if exit_decision.action == ExitAction.EXIT_MARKET:
                self._maybe_log_paper_exit(
                    ctx,
                    exit_decision,
                    per_leg_quotes=exit_eval.per_leg_quotes,
                )
                try:
                    if ctx.open_position is not None:
                        await self._execution_port.flatten_position(ctx.open_position)
                except ExecutionFailedError:
                    ctx = ctx.update(execution_halted=True)
                else:
                    ctx = ctx.update(open_position=None)
                    self._session_open_vix = None

            return SessionTickResult(
                ctx=ctx,
                exit_decision=exit_decision,
                live_underlying_ltp=live_underlying_ltp,
                elapsed_ms=(time.perf_counter() - tick_start) * 1000,
            )

        try:
            return await asyncio.wait_for(
                _tick_body(),
                timeout=self._tick_timeout_sec,
            )
        except asyncio.TimeoutError as exc:
            # A hung-but-alive call cannot orphan the tick lock: the deadline
            # cancels the body, the `finally` releases the lock, and we fail
            # closed by surfacing a typed error to the daemon loop.
            raise SessionPipelineError(
                f"Pipeline tick exceeded {self._tick_timeout_sec:.0f}s deadline; "
                "aborted and released tick lock.",
                code="TICK_TIMEOUT",
            ) from exc
        finally:
            elapsed_ms = (time.perf_counter() - tick_start) * 1000
            await self._persist_tick_trace(
                ctx,
                elapsed_ms=elapsed_ms,
                exit_decision=exit_decision,
            )
            self._tick_lock.release()

    async def _run_entry_chain(
        self,
        ctx: AgentContext,
        *,
        tick_timestamp: str,
        nifty_bars: Sequence[OhlcvBar] | None = None,
    ) -> tuple[AgentContext, float | None]:
        ctx = classify_regime(ctx, config=self._risk_config)
        ctx = select_strategy(ctx)
        strategy = (
            ctx.strategy_decision.strategy
            if ctx.strategy_decision
            else StrategyName.CASH_NO_TRADE
        )
        live_underlying_ltp: float | None = None

        if strategy == StrategyName.CASH_NO_TRADE:
            return ctx, live_underlying_ltp

        if ctx.regime_decision != RegimeLabel.UNCERTAIN:
            selected_legs: list[OptionGreeks] = []
            per_leg_quotes: dict[str, Quote] = {}
            try:
                expiry_ts = select_expiry(
                    ctx,
                    config=self._risk_config,
                    index_symbol=self._index_symbol,
                )
                trade_dte = leg_dte_for_expiry(expiry_ts)
                ctx = ctx.update(dte=trade_dte)
                greeks_list = await self._provider.get_option_chain_greeks(
                    self._index_symbol,
                    expiry_ts,
                )
                selected_legs = select_strategy_symbols(
                    ctx,
                    greeks_list=greeks_list,
                    config=self._risk_config,
                )
                live_underlying_ltp = float(
                    await self._provider.get_index_ltp(self._index_symbol)
                )

                for leg_greek in selected_legs:
                    leg_quote = await self._provider.get_bid_ask(leg_greek.symbol)
                    per_leg_quotes[leg_greek.symbol] = leg_quote
                max_spread = (
                    max(quote.spread_pct for quote in per_leg_quotes.values())
                    if per_leg_quotes
                    else 0.0
                )

                atr_5m: float | None = None
                if nifty_bars and len(nifty_bars) >= 2:
                    try:
                        atr_5m = compute_atr(nifty_bars)
                    except ValueError:
                        atr_5m = None

                ctx = validate_pre_trade(
                    ctx,
                    live_underlying_ltp=live_underlying_ltp,
                    bid_ask_spread_pct=max_spread,
                    greeks_confidence=min(leg_g.confidence for leg_g in selected_legs),
                    leg_deltas=[leg_g.delta for leg_g in selected_legs],
                    leg_gammas=[leg_g.gamma for leg_g in selected_legs],
                    config=self._risk_config,
                    atr_5m=atr_5m,
                )
            except (ExpirySelectionError, StrikeSelectionError) as exc:
                ctx = ctx.update(
                    critic_decision=CriticDecision(
                        status=CriticStatus.REJECT,
                        reason=f"selection_error:{type(exc).__name__}",
                    )
                )
            except (MarketDataError, FyersAuthError) as exc:
                ctx = ctx.update(
                    critic_decision=CriticDecision(
                        status=CriticStatus.REJECT,
                        reason=f"broker_error:{type(exc).__name__}",
                    )
                )
            ctx = evaluate_from_context(
                ctx,
                config=self._risk_config,
                estimated_max_profit_inr=(
                    estimate_max_profit_inr(
                        strategy,
                        selected_legs=selected_legs,
                        per_leg_quotes=per_leg_quotes,
                        lot_size=self._index_contract.lot_size,
                    )
                    if selected_legs and per_leg_quotes
                    else None
                ),
                leg_count=len(selected_legs) if selected_legs else None,
            )
            gatekeeper = ctx.gatekeeper_decision
            if (
                self._dry_run
                and selected_legs
                and gatekeeper is not None
                and gatekeeper.verdict == GatekeeperVerdict.APPROVE
            ):
                try:
                    ctx = await self._submit_approved_entry_legs(
                        ctx,
                        selected_legs=selected_legs,
                        per_leg_quotes=per_leg_quotes,
                        tick_timestamp=tick_timestamp,
                    )
                except ExecutionFailedError:
                    ctx = ctx.update(execution_halted=True)

        return ctx, live_underlying_ltp

    async def _submit_approved_entry_legs(
        self,
        ctx: AgentContext,
        *,
        selected_legs: list[OptionGreeks],
        per_leg_quotes: dict[str, Quote],
        tick_timestamp: str,
    ) -> AgentContext:
        gatekeeper = ctx.gatekeeper_decision
        if gatekeeper is None or gatekeeper.verdict != GatekeeperVerdict.APPROVE:
            return ctx
        strategy = ctx.strategy_decision.strategy if ctx.strategy_decision else None
        if strategy is None:
            return ctx
        sides = ENTRY_LEG_SIDES.get(strategy)
        if sides is None or len(sides) != len(selected_legs):
            return ctx

        submitted_intents: list[LegActionIntent] = []
        for leg_greek, side in zip(selected_legs, sides, strict=True):
            leg_id = leg_greek.symbol
            intent = LegActionIntent(
                leg_id=leg_id,
                symbol=leg_greek.symbol,
                side=side,  # type: ignore[arg-type]
                qty=self._index_contract.lot_size,
                tag=idem_key(
                    tick_timestamp=tick_timestamp,
                    leg_id=leg_id,
                    symbol=leg_greek.symbol,
                    side=side,
                ),
            )
            submitted_intents.append(intent)
            ack = await self._execution_port.submit_legs(intent)
            self._maybe_log_order_ack(ctx, intent=intent, ack=ack)

        if self._broker_sync:
            await verify_entry_fills(self._execution_port, submitted_intents)

        # v4.1: OpenPosition is built from submit intent (selected_legs + quotes),
        # not broker fills. Phase 4.2 must reconcile via get_positions() each tick.
        open_position = _build_open_position_from_entry(
            strategy=strategy,
            selected_legs=selected_legs,
            per_leg_quotes=per_leg_quotes,
            lots=gatekeeper.allowed_lots,
            lot_size=self._index_contract.lot_size,
        )
        return ctx.update(open_position=open_position)

    def _maybe_log_order_ack(
        self,
        ctx: AgentContext,
        *,
        intent: LegActionIntent,
        ack: OrderAck,
    ) -> None:
        if not self._dry_run or self._paper_logger is None:
            return
        self._paper_logger.log_paper_row(
            {
                "event": "PAPER_ORDER_ACK",
                "session_id": ctx.session_id,
                "trace_id": ctx.trace_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "leg_id": intent.leg_id,
                "symbol": intent.symbol,
                "side": intent.side,
                "qty": intent.qty,
                "tag": intent.tag,
                "order_id": ack.order_id,
                "status": ack.status,
                "reason": ack.reason,
            }
        )

    def _maybe_log_paper_approve(self, ctx: AgentContext) -> None:
        if not self._dry_run or self._paper_logger is None:
            return
        gatekeeper = ctx.gatekeeper_decision
        if gatekeeper is None or gatekeeper.verdict != GatekeeperVerdict.APPROVE:
            return
        self._paper_logger.log_paper_row(
            {
                "event": "PAPER_APPROVE",
                "session_id": ctx.session_id,
                "trace_id": ctx.trace_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "regime_decision": (
                    ctx.regime_decision.value if ctx.regime_decision else None
                ),
                "strategy_decision": (
                    ctx.strategy_decision.strategy if ctx.strategy_decision else None
                ),
                "critic_decision": (
                    {
                        "status": ctx.critic_decision.status.value,
                        "reason": ctx.critic_decision.reason,
                    }
                    if ctx.critic_decision
                    else None
                ),
                "gatekeeper_decision": {
                    "verdict": gatekeeper.verdict.value,
                    "rule_id": (
                        gatekeeper.rule_id.value if gatekeeper.rule_id else None
                    ),
                    "expected_round_trip_cost": gatekeeper.expected_round_trip_cost,
                    "reason": gatekeeper.reason,
                },
            }
        )

    def _maybe_log_paper_exit(
        self,
        ctx: AgentContext,
        exit_decision: ExitDecision,
        *,
        per_leg_quotes: dict[str, Quote] | None = None,
    ) -> None:
        if not self._dry_run or self._paper_logger is None:
            return
        row: dict[str, Any] = {
            "event": "PAPER_EXIT",
            "session_id": ctx.session_id,
            "trace_id": ctx.trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "open_position": (
                {
                    "symbol": ctx.open_position.symbol,
                    "strategy": ctx.open_position.strategy,
                }
                if ctx.open_position
                else None
            ),
            "exit_action": exit_decision.action.value,
            "exit_reason": exit_decision.reason,
            "rule_id": exit_decision.rule_id,
            "leg_action_intents": [
                {
                    "symbol": intent.symbol,
                    "action": intent.action,
                    "leg_id": intent.leg_id,
                }
                for intent in exit_decision.leg_action_intents
            ],
        }
        if ctx.open_position is not None and per_leg_quotes is not None:
            from src.execution.noop_port import compute_paper_mtm

            mtm = compute_paper_mtm(
                ctx.open_position,
                per_leg_quotes=per_leg_quotes,
                lot_size=self._index_contract.lot_size,
            )
            row.update(mtm)
        elif ctx.open_position is not None:
            from src.execution.noop_port import expected_friction_for_position

            row["friction_inr"] = expected_friction_for_position(ctx.open_position)
        self._paper_logger.log_paper_row(row)

    async def _refresh_features(
        self,
        ctx: AgentContext,
        *,
        shared_market: SharedMarketSnapshot | None = None,
    ) -> AgentContext:
        try:
            if shared_market is not None:
                payload, index_bars = await compute_index_feature_payload(
                    self._provider,
                    option_symbol=self._index_symbol,
                    index_symbol=self._index_symbol,
                    shared=shared_market,
                    request_timeout_sec=self._request_timeout_sec,
                    pcr_history_path=self._pcr_history_path,
                )
            else:
                payload = await compute_feature_payload(
                    self._provider,
                    option_symbol=self._index_symbol,
                    nifty_symbol=self._index_symbol,
                    request_timeout_sec=self._request_timeout_sec,
                    pcr_history_path=self._pcr_history_path,
                )
                index_bars = await self._provider.get_index_ohlcv(
                    self._index_symbol,
                    resolution="5",
                    lookback_bars=2,
                )
        except FeatureEngineError as exc:
            raise SessionPipelineError(
                str(exc),
                code=exc.code,
            ) from exc

        if not index_bars:
            raise SessionPipelineError(
                "Cannot capture feature_snapshot_price: no index OHLCV bars.",
                code="MARKET_DATA",
            )

        snapshot_price = float(index_bars[-1]["close"])
        captured_at = datetime.now(timezone.utc).isoformat()

        if not ctx.baseline_initialized:
            ctx = ctx.update(baseline_initialized=True)
            if self._boot_logger is not None:
                self._boot_logger.log_boot_row(
                    {
                        "event": "first_tick_baseline",
                        "session_id": ctx.session_id,
                        "timestamp": captured_at,
                        "feature_snapshot_price": snapshot_price,
                    }
                )

        return apply_feature_payload(
            ctx,
            payload,
            captured_at_iso=captured_at,
            feature_snapshot_price=snapshot_price,
        )

    async def _evaluate_exit(
        self,
        ctx: AgentContext,
        *,
        credit_spread_position: CreditSpreadPosition | None,
        nifty_bars: Sequence[OhlcvBar] | None,
        session_open_vix: float | None,
    ) -> ExitEvaluationResult:
        position = ctx.open_position
        if position is None:
            raise SessionPipelineError("Exit evaluation requested without open_position.")

        strategy_key = position.strategy.value
        feature_payload = opening_regime_to_feature_payload(ctx)
        allowed_exit_strategies = {
            StrategyName.IRON_CONDOR.value,
            StrategyName.BULL_CALL_SPREAD.value,
            StrategyName.BEAR_PUT_SPREAD.value,
        }
        if strategy_key not in allowed_exit_strategies:
            raise SessionPipelineError(
                f"Unsupported strategy for exit evaluation: {position.strategy}"
            )

        per_leg_quotes: dict[str, Quote] | None = None
        if position.legs is not None and len(position.legs) >= 2:
            try:
                per_leg_quotes = {}
                for leg in position.legs:
                    per_leg_quotes[leg.symbol] = await self._provider.get_bid_ask(leg.symbol)
            except (MarketDataError, FyersAuthError):
                return ExitEvaluationResult(
                    decision=self._exit_engine.build_emergency_flatten_decision(position),
                )

            open_vix = session_open_vix or self._session_open_vix
            if open_vix is None:
                vix = ctx.opening_regime.vix
                if vix is None:
                    raise SessionPipelineError(
                        "session_open_vix required for credit spread exit evaluation."
                    )
                open_vix = vix
                self._session_open_vix = open_vix

            return ExitEvaluationResult(
                decision=self._exit_engine.evaluate_position(
                    position,
                    feature_payload=feature_payload,
                    nifty_bars=nifty_bars,
                    session_open_vix=open_vix,
                    per_leg_quotes=per_leg_quotes,
                    lot_size=self._index_contract.lot_size,
                ),
                per_leg_quotes=per_leg_quotes,
            )

        if credit_spread_position is None:
            raise SessionPipelineError(
                f"credit_spread_position required for exit evaluation ({position.strategy})."
            )
        open_vix = session_open_vix or self._session_open_vix
        if open_vix is None:
            vix = ctx.opening_regime.vix
            if vix is None:
                raise SessionPipelineError(
                    "session_open_vix required for credit spread exit evaluation."
                )
            open_vix = vix
            self._session_open_vix = open_vix
        return ExitEvaluationResult(
            decision=self._exit_engine.evaluate(
                strategy=strategy_key,
                position=credit_spread_position,
                feature_payload=feature_payload,
                session_open_vix=open_vix,
            ),
        )


def _build_open_position_from_entry(
    *,
    strategy: StrategyName,
    selected_legs: list[OptionGreeks],
    per_leg_quotes: dict[str, Quote],
    lots: int,
    lot_size: int,
) -> OpenPosition:
    """Construct in-memory position from submitted legs (v4.1 pre-broker-sync)."""
    strategy_id = strategy.value
    leg_symbols = [leg.symbol for leg in selected_legs]
    entry_cash_flow_inr = compute_entry_credit_inr(
        strategy,
        leg_symbols=leg_symbols,
        per_leg_quotes=per_leg_quotes,
        lot_size=lot_size,
        lots=lots,
    )
    qty = lot_size * lots
    entry_per_unit = entry_cash_flow_inr / qty if qty > 0 else 0.0
    leg_positions: list[OpenPosition] = []
    for leg in selected_legs:
        quote = per_leg_quotes[leg.symbol]
        leg_positions.append(
            OpenPosition(
                symbol=leg.symbol,
                strategy=strategy,
                lots=lots,
                entry_price=quote.ltp,
                leg_id=leg.symbol,
                strategy_id=strategy_id,
            )
        )
    if len(leg_positions) >= 2:
        return OpenPosition(
            symbol=f"{strategy_id}_summary",
            strategy=strategy,
            lots=lots,
            entry_price=max(abs(entry_per_unit), 0.01),
            entry_cash_flow_inr=entry_cash_flow_inr,
            strategy_id=strategy_id,
            legs=leg_positions,
        )
    return leg_positions[0].model_copy(
        update={"entry_cash_flow_inr": entry_cash_flow_inr},
    )
