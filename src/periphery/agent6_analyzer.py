"""Agent 6: Nightly trace analyzer stub (Phase 5.1)."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AnalyzerReport:
    session_id: str
    tick_count: int
    approve_count: int
    reject_reasons: dict[str, int]
    broker_errors: int


def analyze_session_traces(log_path: Path) -> AnalyzerReport:
    """Cluster a paper soak / tick trace JSONL into a nightly summary."""
    rows: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))

    session_id = str(rows[0].get("session_id", "unknown")) if rows else "unknown"
    tick_count = sum(1 for row in rows if row.get("event") in {"paper_tick", "tick_trace"})
    approve_count = sum(1 for row in rows if row.get("event") == "PAPER_APPROVE")
    broker_errors = sum(1 for row in rows if row.get("event") == "paper_tick_error")

    reject_counter: Counter[str] = Counter()
    for row in rows:
        critic = row.get("critic_decision")
        if isinstance(critic, dict) and critic.get("status") == "REJECT":
            reject_counter[str(critic.get("reason", "unknown"))] += 1
        gatekeeper = row.get("gatekeeper_decision")
        if isinstance(gatekeeper, dict) and gatekeeper.get("verdict") == "REJECT":
            key = str(gatekeeper.get("rule_id") or gatekeeper.get("reason") or "unknown")
            reject_counter[key] += 1

    return AnalyzerReport(
        session_id=session_id,
        tick_count=tick_count,
        approve_count=approve_count,
        reject_reasons=dict(reject_counter),
        broker_errors=broker_errors,
    )
