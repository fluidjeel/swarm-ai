"""Agent 7: Parameter tuner stub (Phase 5.2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config.absolute_limits import clamp_to_absolute
from src.config.risk_config import load_risk_config


@dataclass(frozen=True, slots=True)
class TunerProposal:
    proposal_id: str
    field: str
    current_value: float
    proposed_value: float
    reason: str
    clamped: bool


def propose_risk_config_patch(
    *,
    analyzer_summary: dict[str, Any] | None = None,
    config_path: Path | None = None,
) -> TunerProposal:
    """
    Mock weekend tuner: proposes a single bounded tweak for HITL review.

    Production: LLM reads DynamoDB traces, proposes diff, Agent 5 approves.
    """
    config = load_risk_config(config_path)
    current = config.range_divergence_band
    proposed_raw = current + 0.01
    proposed = clamp_to_absolute("range_divergence_band", proposed_raw)
    return TunerProposal(
        proposal_id="tuner-stub-001",
        field="range_divergence_band",
        current_value=current,
        proposed_value=proposed,
        reason=(
            analyzer_summary.get("notes", "stub proposal from analyzer")
            if analyzer_summary
            else "stub — widen range band after paper soak review"
        ),
        clamped=proposed != proposed_raw,
    )


def proposal_to_hitl_payload(proposal: TunerProposal) -> dict[str, Any]:
    return {
        "proposal_id": proposal.proposal_id,
        "field": proposal.field,
        "patch": {proposal.field: proposal.proposed_value},
        "reason": proposal.reason,
        "clamped": proposal.clamped,
    }
