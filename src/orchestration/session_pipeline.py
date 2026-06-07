"""Session orchestration: Feature Engine → AgentContext → Exit Engine (v4.1)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

from src.agents.pre_trade_critic import validate_pre_trade
from src.agents.regime_classifier import classify_regime
from src.agents.strategy_selector import select_strategy
from src.agents.symbol_resolver import (
    ExpirySelectionError,
    NIFTY_INDEX_SYMBOL,
    StrikeSelectionError,
    select_expiry,
    select_strategy_symbols,
)
from src.config.risk_config import RiskConfig, load_risk_config
from src.core.context import AgentContext, CriticDecision, CriticStatus, RegimeLabel, StrategyName
from src.data.base_provider import OptionGreeks
from src.execution.noop_port import NoOpExecutionPort
from src.execution.port import ExecutionPort, LegActionIntent, OrderAck, idem_key
from src.data.base_provider import FyersAuthError, MarketDataError, MarketDataProvider, OhlcvBar, Quote
from src.risk.gatekeeper import GatekeeperVerdict, evaluate_from_context
from src.features.feature_engine import (
    FeatureEngineError,
    FeatureEngineErrorCode,
    compute_feature_payload,
)
from src.orchestration.broker_recovery import BootLogger, rebuild_from_fyers
from src.orchestration.context_adapters import (
    apply_feature_payload,
    opening_regime_to_feature_payload,
    sync_circuit_breaker,
)
from src.orchestration.session_clock import IST, is_session_start_allowed
from src.orchestration.tick_lock import FileTickLock, NullTickLock, TickLock, TickLockError
from src.risk.exit_engine import (
    CreditSpreadPosition,
    ExitAction,
    ExitDecision,
    ExitEngine,
)


NIFTY_LOT_SIZE = 50
_ENTRY_LEG_SIDES: dict[StrategyName, tuple[str, ...]] = {
    StrategyName.IRON_CONDOR: ("BUY", "SELL", "SELL", "BUY"),
    StrategyName.BULL_CALL_SPREAD: ("BUY", "SELL"),
    StrategyName.BEAR_PUT_SPREAD: ("BUY", "SELL"),
}


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
        pcr_history_path: Path | None = None,
        nifty_symbol: str = "NSE:NIFTY50-INDEX",
        session_open_vix: float | None = None,
        tick_lock: TickLock | None = None,
        enforce_tick_lock: bool = True,
        boot_logger: BootLogger | None = None,
        risk_config: RiskConfig | None = None,
        dry_run: bool = False,
        paper_logger: PaperEventLogger | None = None,
        execution_port: ExecutionPort | None = None,
    ) -> None:
        self._provider = provider
        self._exit_engine = exit_engine or ExitEngine()
        self._execution_port = execution_port or NoOpExecutionPort()
        self._risk_config = risk_config or load_risk_config()
        bind_risk = getattr(self._provider, "bind_risk_config", None)
        if callable(bind_risk):
            bind_risk(self._risk_config)
        self._request_timeout_sec = request_timeout_sec
        self._pcr_history_path = pcr_history_path
        self._nifty_symbol = nifty_symbol
        self._session_open_vix = session_open_vix
        self._boot_logger = boot_logger
        self._dry_run = dry_run
        self._paper_logger = paper_logger
        if tick_lock is not None:
            self._tick_lock = tick_lock
        elif enforce_tick_lock:
            self._tick_lock = FileTickLock()
        else:
            self._tick_lock = NullTickLock()

    def release_tick_lock(self) -> None:
        """Release the tick lock (used by paper-mode shutdown)."""
        self._tick_lock.release()

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
            ltp = await self._provider.get_index_ltp(self._nifty_symbol)
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
    ) -> SessionTickResult:
        """
        Execute one deterministic pipeline tick.

        1. Acquire tick lock (blocks concurrent 5-minute loops).
        2. Refresh feature payload from market data into AgentContext.
        3. If halted with no open position, return (no new entries).
        4. If an open position exists, run Exit Engine; flatten on EXIT_MARKET.
        """
        tick_start = time.perf_counter()
        try:
            self._tick_lock.acquire(blocking=False)
        except TickLockError as exc:
            raise SessionPipelineError(
                "Concurrent pipeline tick blocked by tick lock.",
                code="TICK_LOCK",
            ) from exc

        live_underlying_ltp: float | None = None
        tick_timestamp = datetime.now(IST).isoformat()
        try:
            ctx = sync_circuit_breaker(ctx)
            ctx = await self._refresh_features(ctx)

            if ctx.is_halted and not ctx.has_open_position:
                return SessionTickResult(
                    ctx=ctx,
                    elapsed_ms=(time.perf_counter() - tick_start) * 1000,
                )

            if not ctx.has_open_position and not ctx.is_halted:
                ctx, live_underlying_ltp = await self._run_entry_chain(
                    ctx,
                    tick_timestamp=tick_timestamp,
                )
                self._maybe_log_paper_approve(ctx)

            if not ctx.has_open_position:
                return SessionTickResult(
                    ctx=ctx,
                    live_underlying_ltp=live_underlying_ltp,
                    elapsed_ms=(time.perf_counter() - tick_start) * 1000,
                )

            exit_decision = await self._evaluate_exit(
                ctx,
                credit_spread_position=credit_spread_position,
                nifty_bars=nifty_bars,
                session_open_vix=session_open_vix,
            )

            if exit_decision.leg_action_intents and not self._dry_run:
                ctx = ctx.update(
                    exit_leg_intents=list(exit_decision.leg_action_intents),
                )

            if exit_decision.action == ExitAction.EXIT_MARKET:
                self._maybe_log_paper_exit(ctx, exit_decision)
                ctx = ctx.update(open_position=None)
                self._session_open_vix = None

            return SessionTickResult(
                ctx=ctx,
                exit_decision=exit_decision,
                live_underlying_ltp=live_underlying_ltp,
                elapsed_ms=(time.perf_counter() - tick_start) * 1000,
            )
        finally:
            self._tick_lock.release()

    async def _run_entry_chain(
        self,
        ctx: AgentContext,
        *,
        tick_timestamp: str,
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
            try:
                expiry_ts = select_expiry(ctx, config=self._risk_config)
                greeks_list = await self._provider.get_option_chain_greeks(
                    NIFTY_INDEX_SYMBOL,
                    expiry_ts,
                )
                selected_legs = select_strategy_symbols(
                    ctx,
                    greeks_list=greeks_list,
                    config=self._risk_config,
                )
                live_underlying_ltp = float(
                    await self._provider.get_index_ltp(NIFTY_INDEX_SYMBOL)
                )

                per_leg_spreads: dict[str, float] = {}
                for leg_greek in selected_legs:
                    leg_quote = await self._provider.get_bid_ask(leg_greek.symbol)
                    per_leg_spreads[leg_greek.symbol] = leg_quote.spread_pct
                max_spread = max(per_leg_spreads.values()) if per_leg_spreads else 0.0

                ctx = validate_pre_trade(
                    ctx,
                    live_underlying_ltp=live_underlying_ltp,
                    bid_ask_spread_pct=max_spread,
                    greeks_confidence=min(leg_g.confidence for leg_g in selected_legs),
                    greeks_delta=sum(leg_g.delta for leg_g in selected_legs),
                    greeks_gamma=sum(leg_g.gamma for leg_g in selected_legs),
                    config=self._risk_config,
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
            ctx = evaluate_from_context(ctx, config=self._risk_config)
            if self._dry_run and selected_legs:
                await self._submit_approved_entry_legs(
                    ctx,
                    selected_legs=selected_legs,
                    tick_timestamp=tick_timestamp,
                )

        return ctx, live_underlying_ltp

    async def _submit_approved_entry_legs(
        self,
        ctx: AgentContext,
        *,
        selected_legs: list[OptionGreeks],
        tick_timestamp: str,
    ) -> None:
        gatekeeper = ctx.gatekeeper_decision
        if gatekeeper is None or gatekeeper.verdict != GatekeeperVerdict.APPROVE:
            return
        strategy = ctx.strategy_decision.strategy if ctx.strategy_decision else None
        if strategy is None:
            return
        sides = _ENTRY_LEG_SIDES.get(strategy)
        if sides is None or len(sides) != len(selected_legs):
            return

        for leg_greek, side in zip(selected_legs, sides, strict=True):
            leg_id = leg_greek.symbol
            intent = LegActionIntent(
                leg_id=leg_id,
                symbol=leg_greek.symbol,
                side=side,  # type: ignore[arg-type]
                qty=NIFTY_LOT_SIZE,
                tag=idem_key(
                    tick_timestamp=tick_timestamp,
                    leg_id=leg_id,
                    symbol=leg_greek.symbol,
                    side=side,
                ),
            )
            ack = await self._execution_port.submit_legs(intent)
            self._maybe_log_order_ack(ctx, intent=intent, ack=ack)

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

    def _maybe_log_paper_exit(self, ctx: AgentContext, exit_decision: ExitDecision) -> None:
        if not self._dry_run or self._paper_logger is None:
            return
        self._paper_logger.log_paper_row(
            {
                "event": "PAPER_EXIT",
                "session_id": ctx.session_id,
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
        )

    async def _refresh_features(self, ctx: AgentContext) -> AgentContext:
        try:
            payload = await compute_feature_payload(
                self._provider,
                nifty_symbol=self._nifty_symbol,
                request_timeout_sec=self._request_timeout_sec,
                pcr_history_path=self._pcr_history_path,
            )
        except FeatureEngineError as exc:
            raise SessionPipelineError(
                str(exc),
                code=exc.code,
            ) from exc

        bars = await self._provider.get_index_ohlcv(
            self._nifty_symbol,
            resolution="5",
            lookback_bars=2,
        )
        if not bars:
            raise SessionPipelineError(
                "Cannot capture feature_snapshot_price: no NIFTY OHLCV bars.",
                code="MARKET_DATA",
            )

        snapshot_price = float(bars[-1]["close"])
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
    ) -> ExitDecision:
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

        if position.legs is not None and len(position.legs) >= 2:
            try:
                per_leg_quotes: dict[str, Quote] = {}
                for leg in position.legs:
                    per_leg_quotes[leg.symbol] = await self._provider.get_bid_ask(leg.symbol)
            except (MarketDataError, FyersAuthError):
                return self._exit_engine.build_emergency_flatten_decision(position)

            open_vix = session_open_vix or self._session_open_vix
            if open_vix is None:
                vix = ctx.opening_regime.vix
                if vix is None:
                    raise SessionPipelineError(
                        "session_open_vix required for credit spread exit evaluation."
                    )
                open_vix = vix
                self._session_open_vix = open_vix

            return self._exit_engine.evaluate_position(
                position,
                feature_payload=feature_payload,
                nifty_bars=nifty_bars,
                session_open_vix=open_vix,
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
        return self._exit_engine.evaluate(
            strategy=strategy_key,
            position=credit_spread_position,
            feature_payload=feature_payload,
            session_open_vix=open_vix,
        )
