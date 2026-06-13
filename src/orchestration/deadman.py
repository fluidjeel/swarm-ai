"""Dead-man's switch: detect engine heartbeat absence and alert operators.

Because intraday stops are currently synthetic (held in the EC2 process, not at
the exchange), an unnoticed engine death = an unprotected live position. This
monitor is meant to run **out-of-process** from the trading engine (a cron job,
a sidecar, or a CloudWatch alarm wired to the same heartbeat file) so that the
thing detecting the death is not the thing that died.

The trading loop already emits ``tick_heartbeat`` rows via
``runtime_guards.write_tick_heartbeat``. This module reads the latest one,
decides staleness, and dispatches an ``Alert`` through any ``AlertSink``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from src.observability.alerting import Alert, AlertSeverity, AlertSink, LoggingAlertSink
from src.orchestration.runtime_guards import default_heartbeat_path

logger = logging.getLogger("a2a.deadman")

# A tick is ~5 min; allow one missed tick of slack before declaring death.
DEFAULT_HEARTBEAT_STALE_SECONDS = 360.0


class HeartbeatHealth(StrEnum):
    OK = "OK"
    STALE = "STALE"
    MISSING = "MISSING"


@dataclass(frozen=True)
class DeadManStatus:
    health: HeartbeatHealth
    age_seconds: float | None
    last_row: dict[str, Any] | None

    @property
    def alerting(self) -> bool:
        return self.health is not HeartbeatHealth.OK


def read_last_heartbeat(path: Path) -> dict[str, Any] | None:
    """Return the last JSONL heartbeat row, or None if missing/empty/corrupt."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            last_line = ""
            for line in handle:
                stripped = line.strip()
                if stripped:
                    last_line = stripped
    except (OSError, ValueError):
        return None
    if not last_line:
        return None
    try:
        row = json.loads(last_line)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) else None


def heartbeat_age_seconds(row: dict[str, Any] | None, *, now: datetime) -> float | None:
    """Seconds between the heartbeat timestamp and ``now`` (None if unparseable)."""
    if not row:
        return None
    ts = row.get("timestamp")
    if not isinstance(ts, str):
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds()


class DeadMansSwitch:
    """Heartbeat-absence detector with operator alerting."""

    def __init__(
        self,
        *,
        heartbeat_path: Path | None = None,
        alert_sink: AlertSink | None = None,
        max_age_seconds: float = DEFAULT_HEARTBEAT_STALE_SECONDS,
        session_id: str | None = None,
    ) -> None:
        self._path = heartbeat_path or default_heartbeat_path()
        self._sink = alert_sink or LoggingAlertSink()
        self._max_age = max_age_seconds
        self._session_id = session_id

    def evaluate(self, *, now: datetime | None = None) -> DeadManStatus:
        """Classify heartbeat health without sending alerts (pure-ish read)."""
        now = now or datetime.now(timezone.utc)
        row = read_last_heartbeat(self._path)
        if row is None:
            return DeadManStatus(HeartbeatHealth.MISSING, None, None)
        age = heartbeat_age_seconds(row, now=now)
        if age is None:
            return DeadManStatus(HeartbeatHealth.MISSING, None, row)
        if age > self._max_age:
            return DeadManStatus(HeartbeatHealth.STALE, age, row)
        return DeadManStatus(HeartbeatHealth.OK, age, row)

    def check(self, *, now: datetime | None = None) -> DeadManStatus:
        """Evaluate and dispatch a CRITICAL alert when the engine looks dead."""
        status = self.evaluate(now=now)
        if not status.alerting:
            return status

        if status.health is HeartbeatHealth.MISSING:
            detail = (
                "No engine heartbeat found. The trading process may have failed "
                "to start or the heartbeat file is missing. Synthetic stops are "
                "NOT active — verify open positions at the broker immediately."
            )
        else:
            detail = (
                f"Engine heartbeat is stale ({status.age_seconds:.0f}s old, "
                f"limit {self._max_age:.0f}s). The trading process is likely dead "
                "or hung. Synthetic stops are NOT firing — verify and flatten "
                "open positions at the broker immediately."
            )

        context: dict[str, Any] = {"heartbeat_path": str(self._path)}
        if self._session_id:
            context["session_id"] = self._session_id
        if status.last_row is not None:
            context["last_tick"] = status.last_row.get("tick_number")
            context["last_timestamp"] = status.last_row.get("timestamp")

        self._sink.send(
            Alert(
                severity=AlertSeverity.CRITICAL,
                title="DEAD-MAN'S SWITCH TRIPPED",
                detail=detail,
                context=context,
            )
        )
        return status


def main(argv: list[str] | None = None) -> int:
    """One-shot CLI: `python -m src.orchestration.deadman`.

    Exit code 0 = healthy, 2 = heartbeat stale/missing (alert dispatched). Wire
    to cron/systemd-timer/CloudWatch so the watcher outlives the engine.
    """
    import argparse

    from src.observability.alerting import (
        LoggingAlertSink,
        MultiAlertSink,
        TelegramAlertSink,
    )

    parser = argparse.ArgumentParser(description="A2A dead-man's switch")
    parser.add_argument("--heartbeat-path", default=None)
    parser.add_argument(
        "--max-age-seconds", type=float, default=DEFAULT_HEARTBEAT_STALE_SECONDS
    )
    parser.add_argument("--session-id", default=None)
    args = parser.parse_args(argv)

    sink = MultiAlertSink([LoggingAlertSink(), TelegramAlertSink()])
    switch = DeadMansSwitch(
        heartbeat_path=Path(args.heartbeat_path) if args.heartbeat_path else None,
        alert_sink=sink,
        max_age_seconds=args.max_age_seconds,
        session_id=args.session_id,
    )
    status = switch.check()
    logger.info("Dead-man check: health=%s age=%s", status.health, status.age_seconds)
    return 0 if not status.alerting else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
