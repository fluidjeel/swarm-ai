"""Agent 5: Telegram HITL gateway stub (Phase 3.3 / 5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

HITLAction = Literal["APPROVE", "VETO"]


@dataclass(frozen=True, slots=True)
class HITLResult:
    action: HITLAction
    proposal_id: str
    applied: bool
    detail: str


def process_hitl_callback(
    payload: dict[str, Any],
    *,
    dry_run: bool = True,
) -> HITLResult:
    """
    Mock Telegram webhook handler. Production: Lambda Function URL + DynamoDB state.

    Expected payload keys: action (APPROVE|VETO), proposal_id.
    """
    action_raw = str(payload.get("action", "VETO")).upper()
    action: HITLAction = "APPROVE" if action_raw == "APPROVE" else "VETO"
    proposal_id = str(payload.get("proposal_id", "unknown"))
    if dry_run:
        return HITLResult(
            action=action,
            proposal_id=proposal_id,
            applied=False,
            detail="dry_run — no DynamoDB write",
        )
    return HITLResult(
        action=action,
        proposal_id=proposal_id,
        applied=action == "APPROVE",
        detail="mock applied flag only; wire DynamoDB in production",
    )
