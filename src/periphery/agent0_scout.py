"""Agent 0: Pre-market scout stub (Phase 5.1 — writes overnight_context.json)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run_agent0_scout(
    *,
    output_path: Path | None = None,
    macro_snapshot: dict[str, Any] | None = None,
) -> Path:
    """
    Mock Agent 0 for offline/dev. Production replaces body with Lambda + LLM.

    Writes a static overnight context blob suitable for AgentContext.overnight_context.
    """
    path = output_path or Path("data") / "overnight_context.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = macro_snapshot or {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "agent0_stub",
        "gift_nifty_bias": "neutral",
        "fii_dii_bias": "neutral",
        "headline_risk": "low",
        "notes": "Stub context — replace with Lambda scrape + LLM summary.",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
