#!/usr/bin/env python3
"""
Epic 1.2 demo: run a dummy traced agent and write telemetry to DynamoDB.

Usage (on EC2 with A2A-Trading-EC2-Role attached):
  python scripts/demo_trace_agent.py
"""

from __future__ import annotations

import sys

from src.core.context import AgentContext
from src.observability.trace_agent import AgentTraceResult, trace_agent


@trace_agent(
    agent_name="dummy_regime_classifier",
    prompt_version="regime_classifier/v1",
    downstream_action="ROUTE_TO_AGENT_2",
)
def dummy_regime_classifier(ctx: AgentContext) -> AgentTraceResult:
    return AgentTraceResult(
        output={"regime_decision": "RANGE", "confidence_signals": 2},
        input_tokens=100,
        output_tokens=20,
        downstream_action="ROUTE_TO_AGENT_2",
    )


def main() -> int:
    ctx = AgentContext(session_id="demo-session-001", dte=7)
    result = dummy_regime_classifier(ctx)
    print("Trace written for session:", ctx.session_id)
    print("Agent output:", result.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
