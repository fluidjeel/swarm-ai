"""Session orchestration: Feature Engine → AgentContext → Exit Engine (v4.1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

from src.agents.pre_trade_critic import validate_pre_trade
from src.agents.regime_classifier import classify_regime
from src.agents.strategy_selector import select_strategy
from src.agents.symbol_resolver import expiry_ts_for_context, quote_symbol_for_strategy
from src.config.risk_config import RiskConfig, load_risk_config
from src.core.context import AgentContext, CriticDecision, CriticStatus, RegimeLabel
from src.data.base_provider import FyersAuthError, MarketDataError, MarketDataProvider, OhlcvBar
from src.risk.gatekeeper import evaluate_from_context
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
    CREDIT_SPREAD_STRATEGIES,
    FUTURES_STRATEGIES,
    CreditSpreadPosition,
    ExitAction,
    ExitDecision,
    ExitEngine,
    FuturesPosition,
)


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


class BootEventWriter(Protocol):
    def log_boot_row(self, row: dict[str, Any]) -> None: ...


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
    ) -> None:
        self._provider = provider
        self._exit_engine = exit_engine or ExitEngine()
        self._risk_config = risk_config or load_risk_config()
        self._request_timeout_sec = request_timeout_sec
        self._pcr_history_path = pcr_history_path
        self._nifty_symbol = nifty_symbol
        self._session_open_vix = session_open_vix
        self._boot_logger = boot_logger
        if tick_lock is not None:
            self._tick_lock = tick_lock
        elif enforce_tick_lock:
            self._tick_lock = FileTickLock()
        else:
            self._tick_lock = NullTickLock()

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
        futures_position: FuturesPosition | None = None,
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
        try:
            self._tick_lock.acquire(blocking=False)
        except TickLockError as exc:
            raise SessionPipelineError(
                "Concurrent pipeline tick blocked by tick lock.",
                code="TICK_LOCK",
            ) from exc

        try:
            ctx = sync_circuit_breaker(ctx)
            ctx = await self._refresh_features(ctx)

            if ctx.is_halted and not ctx.has_open_position:
                return SessionTickResult(ctx=ctx)

            if not ctx.has_open_position and not ctx.is_halted:
                ctx = await self._run_entry_chain(ctx)

            if not ctx.has_open_position:
                return SessionTickResult(ctx=ctx)

            exit_decision = await self._evaluate_exit(
                ctx,
                futures_position=futures_position,
                credit_spread_position=credit_spread_position,
                nifty_bars=nifty_bars,
                session_open_vix=session_open_vix,
            )

            if exit_decision.action == ExitAction.EXIT_MARKET:
                ctx = ctx.update(open_position=None)
                self._session_open_vix = None

            return SessionTickResult(ctx=ctx, exit_decision=exit_decision)
        finally:
            self._tick_lock.release()

    async def _run_entry_chain(self, ctx: AgentContext) -> AgentContext:
        ctx = classify_regime(ctx, config=self._risk_config)
        ctx = select_strategy(ctx)
        strategy = ctx.strategy_decision.strategy if ctx.strategy_decision else "cash_no_trade"

        if strategy != "cash_no_trade" and ctx.regime_decision != RegimeLabel.UNCERTAIN:
            symbol = quote_symbol_for_strategy(ctx)
            try:
                quote = await self._provider.get_bid_ask(symbol)
                greeks_list = await self._provider.get_option_chain_greeks(
                    symbol,
                    expiry_ts_for_context(ctx),
                )
                greek = greeks_list[0]
                live_ltp = quote.underlying_ltp if quote.underlying_ltp is not None else quote.ltp
                ctx = validate_pre_trade(
                    ctx,
                    live_underlying_ltp=live_ltp,
                    bid_ask_spread_pct=quote.spread_pct,
                    greeks_confidence=greek.confidence,
                    greeks_delta=greek.delta,
                    greeks_gamma=greek.gamma,
                    config=self._risk_config,
                )
            except (MarketDataError, FyersAuthError) as exc:
                ctx = ctx.update(
                    critic_decision=CriticDecision(
                        status=CriticStatus.REJECT,
                        reason=f"broker_error:{type(exc).__name__}",
                    )
                )
            ctx = evaluate_from_context(ctx, config=self._risk_config)

        return ctx

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
        futures_position: FuturesPosition | None,
        credit_spread_position: CreditSpreadPosition | None,
        nifty_bars: Sequence[OhlcvBar] | None,
        session_open_vix: float | None,
    ) -> ExitDecision:
        position = ctx.open_position
        if position is None:
            raise SessionPipelineError("Exit evaluation requested without open_position.")

        strategy_key = position.strategy.strip().lower()
        feature_payload = opening_regime_to_feature_payload(ctx)

        if strategy_key in FUTURES_STRATEGIES:
            if futures_position is None:
                raise SessionPipelineError(
                    f"futures_position required for exit evaluation ({position.strategy})."
                )
            bars = nifty_bars
            if bars is None:
                bars = await self._provider.get_index_ohlcv(
                    self._nifty_symbol,
                    resolution="5",
                    lookback_bars=20,
                )
            return self._exit_engine.evaluate(
                strategy=position.strategy,
                position=futures_position,
                feature_payload=feature_payload,
                nifty_bars=bars,
            )

        if strategy_key in CREDIT_SPREAD_STRATEGIES:
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
                strategy=position.strategy,
                position=credit_spread_position,
                feature_payload=feature_payload,
                session_open_vix=open_vix,
            )

        raise SessionPipelineError(
            f"Unsupported strategy for exit evaluation: {position.strategy}"
        )
