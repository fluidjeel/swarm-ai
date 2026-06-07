"""Runtime health guards for the intraday tick loop (v4.1)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("a2a.runtime_guards")

try:
    import psutil as _psutil
except ImportError:
    _psutil = None

DEFAULT_RAM_HALT_PCT = 85.0


class MemoryGuardError(RuntimeError):
    """Raised when host memory exceeds the configured halt threshold."""


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    percent_used: float
    threshold_pct: float


def check_memory_usage(*, threshold_pct: float = DEFAULT_RAM_HALT_PCT) -> MemorySnapshot:
    """Fail-closed when resident memory exceeds threshold (requires psutil)."""
    if _psutil is None:
        logger.debug("psutil not installed; skipping memory guard")
        return MemorySnapshot(percent_used=0.0, threshold_pct=threshold_pct)

    percent = float(_psutil.virtual_memory().percent)
    if percent > threshold_pct:
        raise MemoryGuardError(
            f"Host memory {percent:.1f}% exceeds halt threshold {threshold_pct:.1f}%"
        )
    return MemorySnapshot(percent_used=percent, threshold_pct=threshold_pct)


class HeartbeatWriter(Protocol):
    def write(self, row: dict[str, object]) -> None: ...


class JsonlHeartbeatWriter:
    """Append-only heartbeat for ops absence detection (mockable without DDB)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, row: dict[str, object]) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")


def default_heartbeat_path() -> Path:
    custom = os.getenv("A2A_HEARTBEAT_PATH")
    if custom:
        return Path(custom)
    return Path("logs") / "heartbeat.jsonl"


def write_tick_heartbeat(
    writer: HeartbeatWriter,
    *,
    session_id: str,
    tick_number: int,
    memory_pct: float,
    elapsed_ms: float,
) -> None:
    writer.write(
        {
            "event": "tick_heartbeat",
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tick_number": tick_number,
            "memory_pct": round(memory_pct, 2),
            "elapsed_ms": round(elapsed_ms, 3),
        }
    )
