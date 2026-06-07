"""Per-tick deterministic trace rows (SEBI audit path; JSONL default)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from src.core.context import AgentContext


class TickTraceWriter(Protocol):
    def write_tick(self, row: dict[str, Any]) -> None: ...


class JsonlTickTraceWriter:
    """Append-only per-tick trace (local stand-in until DynamoDB TraceLogger ships)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write_tick(self, row: dict[str, Any]) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")


class DynamoDBTickTraceWriter:
    """Optional DDB sink when A2A_TRACES_TABLE is configured."""

    def __init__(
        self,
        *,
        table_name: str | None = None,
        region_name: str | None = None,
    ) -> None:
        self.table_name = table_name or os.getenv("A2A_TRACES_TABLE", "A2A_Traces")
        self.region_name = region_name or os.getenv("AWS_REGION", "ap-south-1")
        self._table = None

    @property
    def table(self):
        if self._table is None:
            import boto3

            resource = boto3.resource("dynamodb", region_name=self.region_name)
            self._table = resource.Table(self.table_name)
        return self._table

    def write_tick(self, row: dict[str, Any]) -> None:
        session_id = str(row.get("session_id", "unknown"))
        ts = row.get("timestamp")
        sort_key = int(datetime.now(timezone.utc).timestamp() * 1000)
        if isinstance(ts, str):
            try:
                sort_key = int(
                    datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000
                )
            except ValueError:
                pass
        self.table.put_item(
            Item={
                "session_id": session_id,
                "timestamp": sort_key,
                "event": row.get("event", "tick_trace"),
                "payload_json": json.dumps(row, default=str),
            }
        )


def build_tick_trace_row(
    *,
    session_id: str,
    tick_number: int,
    ctx: AgentContext,
    elapsed_ms: float,
    phase: str | None = None,
) -> dict[str, Any]:
    gatekeeper = ctx.gatekeeper_decision
    return {
        "event": "tick_trace",
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tick_number": tick_number,
        "phase": phase,
        "regime": ctx.regime_decision.value if ctx.regime_decision else None,
        "strategy": (
            ctx.strategy_decision.strategy if ctx.strategy_decision else None
        ),
        "critic_status": (
            ctx.critic_decision.status.value if ctx.critic_decision else None
        ),
        "gatekeeper_verdict": gatekeeper.verdict.value if gatekeeper else None,
        "has_open_position": ctx.has_open_position,
        "execution_halted": ctx.execution_halted,
        "circuit_status": ctx.circuit_status,
        "elapsed_ms": round(elapsed_ms, 3),
    }


def default_tick_trace_path(session_id: str) -> Path:
    return Path("logs") / "traces" / f"{session_id}.jsonl"
