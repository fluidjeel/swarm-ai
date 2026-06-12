"""Tests for post-soak trace analyzer friction model."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze_soak_traces.py"


class AnalyzeSoakTracesTests(unittest.TestCase):
    def _run_analyzer(self, rows: list[dict]) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--file",
                    str(path),
                    "--expected-hours",
                    "0.5",
                    "--tick-interval",
                    "300",
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=ROOT,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            return result.stdout

    def test_aborted_trades_do_not_incur_friction(self) -> None:
        output = self._run_analyzer(
            [
                {
                    "event": "PAPER_APPROVE",
                    "session_id": "aborted-01",
                    "timestamp": "2026-06-09T11:00:50+05:30",
                    "strategy_decision": "iron_condor",
                },
                {
                    "event": "PAPER_EXIT",
                    "session_id": "aborted-01",
                    "timestamp": "2026-06-09T11:00:50+05:30",
                    "exit_reason": "broker_error_emergency_flatten",
                },
                {
                    "event": "PAPER_APPROVE",
                    "session_id": "aborted-01",
                    "timestamp": "2026-06-09T11:05:50+05:30",
                    "strategy_decision": "cash_no_trade",
                },
            ]
        )
        self.assertIn("Total trades     : 0", output)
        self.assertIn("Net paper PnL    : INR 0.00", output)
        self.assertIn("No executed trades", output)

    def test_executed_trade_applies_friction(self) -> None:
        output = self._run_analyzer(
            [
                {
                    "event": "PAPER_APPROVE",
                    "session_id": "exec-01",
                    "timestamp": "2026-06-09T11:00:50+05:30",
                    "strategy_decision": "iron_condor",
                },
                {
                    "event": "PAPER_ORDER_ACK",
                    "session_id": "exec-01",
                    "timestamp": "2026-06-09T11:00:51+05:30",
                    "symbol": "NSE:NIFTY26JUN25000CE",
                },
                {
                    "event": "PAPER_EXIT",
                    "session_id": "exec-01",
                    "timestamp": "2026-06-09T11:30:50+05:30",
                    "exit_reason": "take_profit",
                },
            ]
        )
        self.assertIn("Total trades     : 1", output)
        self.assertIn("Net paper PnL    : INR 110.00", output)


if __name__ == "__main__":
    unittest.main()
