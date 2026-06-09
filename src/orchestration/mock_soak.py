"""Offline mock paper soak (no Fyers network)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config.index_contracts import resolve_index_contract, risk_config_for_contract
from src.config.risk_config import RiskConfig, load_risk_config
from src.core.context import AgentContext, OpenPosition
from src.data.base_provider import BreadthSnapshot, MarketDataProvider, OptionChainPcr, OptionGreeks, Quote
from src.execution.mock_port import MockExecutionPort
from src.execution.noop_port import NoOpExecutionPort
from src.orchestration.paper_mode import JsonlPaperLogger, PaperSoakRunner
from src.orchestration.session_pipeline import SessionPipeline
from src.orchestration.session_clock import IST
from src.orchestration.tick_lock import NullTickLock

# Monday 10:30 IST — inside INTRADAY for bootstrap/ticks in offline mock runs.
MOCK_SOAK_NOW = datetime(2026, 6, 8, 10, 30, tzinfo=IST)


class MockMarketDataProvider:
    """Deterministic fake provider for offline soak / CI."""

    def __init__(self, *, index_ltp: float = 24850.5) -> None:
        self._index_ltp = index_ltp
        self._risk_config: RiskConfig | None = None

    def bind_risk_config(self, config: RiskConfig) -> None:
        self._risk_config = config

    async def get_index_ltp(self, symbol: str) -> float:
        return self._index_ltp

    async def get_vix(self) -> float:
        return 14.5

    async def get_option_chain_pcr(
        self,
        symbol: str = "NSE:NIFTY50-INDEX",
        *,
        strikecount: int = 50,
    ) -> OptionChainPcr:
        expiry = datetime.now(timezone.utc) + timedelta(days=5)
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

    async def get_index_ohlcv(
        self,
        symbol: str,
        *,
        resolution: str = "5",
        lookback_bars: int = 50,
    ):
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
                symbol="NSE:NIFTY26JUN24800PE",
                strike=24800,
                option_type="PE",
                delta=-0.31,
                gamma=0.01,
                confidence="high",
            ),
            OptionGreeks(
                symbol="NSE:NIFTY26JUN24900PE",
                strike=24900,
                option_type="PE",
                delta=-0.15,
                gamma=0.01,
                confidence="high",
            ),
            OptionGreeks(
                symbol="NSE:NIFTY26JUN25100CE",
                strike=25100,
                option_type="CE",
                delta=0.31,
                gamma=0.01,
                confidence="high",
            ),
            OptionGreeks(
                symbol="NSE:NIFTY26JUN25200CE",
                strike=25200,
                option_type="CE",
                delta=0.15,
                gamma=0.01,
                confidence="high",
            ),
        ]

    async def get_bid_ask(self, symbol: str) -> Quote:
        return Quote(symbol=symbol, bid=100.0, ask=101.0, ltp=100.5, spread_pct=0.01)


def build_mock_runner(
    *,
    exercise_broker: bool = False,
    index_symbol: str | None = None,
) -> PaperSoakRunner:
    session_id = f"mock-{uuid.uuid4().hex[:12]}"
    log_dir = Path("logs") / "paper_soak"
    log_path = log_dir / f"{session_id}.jsonl"
    paper_logger = JsonlPaperLogger(log_path)
    provider: MarketDataProvider = MockMarketDataProvider()  # type: ignore[assignment]
    execution_port = MockExecutionPort() if exercise_broker else NoOpExecutionPort()
    index_contract = resolve_index_contract(index_symbol or "nifty")
    risk_config = risk_config_for_contract(index_contract, load_risk_config())
    pipeline = SessionPipeline(
        provider,
        index_symbol=index_contract.symbol,
        risk_config=risk_config,
        tick_lock=NullTickLock(),
        dry_run=True,
        paper_logger=paper_logger,
        execution_port=execution_port,
        broker_sync=exercise_broker,
        enforce_tick_lock=False,
        memory_guard_enabled=False,
    )
    return PaperSoakRunner(
        pipeline,
        session_id=session_id,
        logger=paper_logger,
        tick_seconds=0.05,
        duration_hours=0.0002,
        now_fn=lambda: MOCK_SOAK_NOW,
        memory_guard_enabled=False,
        index_contract=index_contract,
    )
