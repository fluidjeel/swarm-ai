"""Local PCR history store for momentum calculations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parents[2] / "data" / "pcr_history.json"
DEFAULT_LOOKBACK_HOURS = 2


@dataclass(frozen=True, slots=True)
class PcrSnapshot:
    pcr: float
    captured_at_iso: str

    @property
    def captured_at(self) -> datetime:
        return datetime.fromisoformat(self.captured_at_iso)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_pcr_history(path: Path = DEFAULT_HISTORY_PATH) -> list[PcrSnapshot]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []

    snapshots: list[PcrSnapshot] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            snapshots.append(
                PcrSnapshot(
                    pcr=float(row["pcr"]),
                    captured_at_iso=str(row["captured_at_iso"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return snapshots


def save_pcr_snapshot(
    pcr: float,
    *,
    path: Path = DEFAULT_HISTORY_PATH,
    max_entries: int = 200,
) -> PcrSnapshot:
    snapshot = PcrSnapshot(pcr=pcr, captured_at_iso=_utc_now_iso())
    history = load_pcr_history(path)
    history.append(snapshot)
    trimmed = history[-max_entries:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [{"pcr": item.pcr, "captured_at_iso": item.captured_at_iso} for item in trimmed],
            indent=2,
        ),
        encoding="utf-8",
    )
    return snapshot


def find_pcr_near_hours_ago(
    history: list[PcrSnapshot],
    *,
    hours: float = DEFAULT_LOOKBACK_HOURS,
    now: datetime | None = None,
) -> float | None:
    if not history:
        return None

    now = now or datetime.now(timezone.utc)
    target = now - timedelta(hours=hours)
    candidates = [snap for snap in history if snap.captured_at <= now]
    if not candidates:
        return None

    prior = min(candidates, key=lambda snap: abs((snap.captured_at - target).total_seconds()))
    return prior.pcr
