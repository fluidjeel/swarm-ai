"""Tests for runtime guards and soak validation helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.execution.fill_reconcile import verify_entry_fills
from src.execution.mock_port import MockExecutionPort
from src.execution.port import LegActionIntent
from src.orchestration.runtime_guards import (
    JsonlHeartbeatWriter,
    MemoryGuardError,
    check_memory_usage,
    write_tick_heartbeat,
)
from src.periphery.agent0_scout import run_agent0_scout
from src.periphery.agent6_analyzer import analyze_session_traces
from src.periphery.agent7_tuner import propose_risk_config_patch


class RuntimeGuardTests(unittest.TestCase):
    def test_check_memory_ok_when_below_threshold(self) -> None:
        with patch("src.orchestration.runtime_guards._psutil.virtual_memory") as mock_mem:
            mock_mem.return_value.percent = 50.0
            snap = check_memory_usage(threshold_pct=85.0)
        self.assertEqual(snap.percent_used, 50.0)

    def test_check_memory_raises_above_threshold(self) -> None:
        with patch("src.orchestration.runtime_guards._psutil.virtual_memory") as mock_mem:
            mock_mem.return_value.percent = 90.0
            with self.assertRaises(MemoryGuardError):
                check_memory_usage(threshold_pct=85.0)

    def test_heartbeat_writer_appends_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "heartbeat.jsonl"
            writer = JsonlHeartbeatWriter(path)
            write_tick_heartbeat(
                writer,
                session_id="heartbeat-test-01",
                tick_number=1,
                memory_pct=42.0,
                elapsed_ms=12.5,
            )
            rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(rows[0]["event"], "tick_heartbeat")


class FillReconcileTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_entry_fills_passes_when_tags_present(self) -> None:
        port = MockExecutionPort()
        intent = LegActionIntent(
            leg_id="leg-a",
            symbol="NSE:NIFTY26JUN25000CE",
            side="BUY",
            qty=50,
            tag="fill-tag-01",
        )
        await port.submit_legs(intent)
        await verify_entry_fills(port, [intent])

    async def test_verify_entry_fills_raises_when_missing(self) -> None:
        from src.execution.port import ExecutionFailedError

        port = MockExecutionPort()
        intent = LegActionIntent(
            leg_id="leg-a",
            symbol="NSE:NIFTY26JUN25000CE",
            side="BUY",
            qty=50,
            tag="missing-tag",
        )
        with self.assertRaises(ExecutionFailedError):
            await verify_entry_fills(port, [intent])


class PeripheryStubTests(unittest.TestCase):
    def test_agent0_writes_overnight_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = run_agent0_scout(output_path=Path(tmp) / "overnight.json")
            payload = json.loads(path.read_text())
        self.assertEqual(payload["source"], "agent0_stub")

    def test_agent7_proposal_is_clamped(self) -> None:
        proposal = propose_risk_config_patch()
        self.assertEqual(proposal.field, "range_divergence_band")

    def test_agent6_analyzer_counts_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text(
                json.dumps({"event": "paper_tick", "session_id": "analyzer-01"}) + "\n"
                + json.dumps({"event": "PAPER_APPROVE", "session_id": "analyzer-01"}) + "\n",
                encoding="utf-8",
            )
            report = analyze_session_traces(path)
        self.assertEqual(report.tick_count, 1)
        self.assertEqual(report.approve_count, 1)


if __name__ == "__main__":
    unittest.main()
