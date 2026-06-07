"""End-of-day archiver stub (Phase 5 — JSONL to archive dir)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    source: Path
    destination: Path
    bytes_copied: int


def archive_session_logs(
    session_id: str,
    *,
    log_dir: Path | None = None,
    archive_dir: Path | None = None,
) -> list[ArchiveResult]:
    """Copy paper soak + tick trace logs to archive (S3 upload deferred)."""
    base_logs = log_dir or Path("logs")
    dest_root = archive_dir or Path.home() / "soak-archive"
    dest_root.mkdir(parents=True, exist_ok=True)

    results: list[ArchiveResult] = []
    candidates = [
        base_logs / "paper_soak" / f"{session_id}.jsonl",
        base_logs / "traces" / f"{session_id}.jsonl",
        base_logs / "heartbeat.jsonl",
    ]
    for source in candidates:
        if not source.exists():
            continue
        destination = dest_root / source.name
        shutil.copy2(source, destination)
        results.append(
            ArchiveResult(
                source=source,
                destination=destination,
                bytes_copied=destination.stat().st_size,
            )
        )
    return results
