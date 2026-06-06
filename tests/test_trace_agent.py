"""Tests for @trace_agent observability decorator."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from src.core.context import AgentContext, RegimeLabel
from src.observability.trace_agent import (
    AgentTraceResult,
    TraceRecord,
    trace_agent,
)


@dataclass
class InMemoryTraceWriter:
    records: list[TraceRecord] = field(default_factory=list)

    def write(self, record: TraceRecord) -> None:
        self.records.append(record)


class TraceAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.writer = InMemoryTraceWriter()
        self.ctx = AgentContext(session_id="session-trace-001", dte=5)

    def test_sync_agent_writes_complete_record(self) -> None:
        @trace_agent(
            agent_name="dummy_regime_classifier",
            prompt_version="regime_classifier/v1",
            downstream_action="ROUTE_TO_AGENT_2",
            writer=self.writer,
        )
        def classify_regime(ctx: AgentContext) -> AgentTraceResult:
            return AgentTraceResult(
                output={"regime_decision": RegimeLabel.RANGE.value},
                input_tokens=120,
                output_tokens=18,
                downstream_action="ROUTE_TO_AGENT_2",
                validation_passed=True,
            )

        result = classify_regime(self.ctx)

        self.assertEqual(len(self.writer.records), 1)
        record = self.writer.records[0]
        self.assertEqual(record.session_id, "session-trace-001")
        self.assertEqual(record.agent_name, "dummy_regime_classifier")
        self.assertEqual(record.prompt_version, "regime_classifier/v1")
        self.assertEqual(record.input_tokens, 120)
        self.assertEqual(record.output_tokens, 18)
        self.assertGreaterEqual(record.latency_ms, 0)
        self.assertEqual(len(record.input_hash), 64)
        self.assertIn("RANGE", record.output_json)
        self.assertTrue(record.validation_passed)
        self.assertEqual(record.downstream_action, "ROUTE_TO_AGENT_2")
        self.assertIsInstance(result, AgentTraceResult)

    def test_failed_agent_logs_validation_false(self) -> None:
        @trace_agent(
            agent_name="failing_agent",
            prompt_version="v1",
            writer=self.writer,
        )
        def broken_agent(ctx: AgentContext) -> None:
            raise ValueError("schema mismatch")

        with self.assertRaises(ValueError):
            broken_agent(self.ctx)

        self.assertEqual(len(self.writer.records), 1)
        record = self.writer.records[0]
        self.assertFalse(record.validation_passed)
        self.assertEqual(record.downstream_action, "ERROR")

    def test_requires_agent_context_first_argument(self) -> None:
        @trace_agent(agent_name="bad_signature", prompt_version="v1", writer=self.writer)
        def bad_agent(not_ctx: str) -> str:
            return "ok"

        with self.assertRaises(TypeError):
            bad_agent("not-a-context")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
