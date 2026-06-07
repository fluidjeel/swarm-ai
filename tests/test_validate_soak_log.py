"""Tests for scripts/validate_soak_log.py."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_soak_log import validate_soak_log


class ValidateSoakLogTests(unittest.TestCase):
    def _write_log(self, path: Path, rows: list[dict]) -> None:
        path.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )

    def test_smoke_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "smoke.jsonl"
            self._write_log(
                path,
                [
                    {"event": "paper_tick"},
                    {"event": "PAPER_APPROVE"},
                    {"event": "paper_soak_complete"},
                ],
            )
            passed, issues = validate_soak_log(path, smoke=True)
        self.assertTrue(passed)
        self.assertEqual(issues, [])

    def test_smoke_fail_on_duplicate_approves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "smoke.jsonl"
            self._write_log(
                path,
                [
                    {"event": "PAPER_APPROVE"},
                    {"event": "PAPER_APPROVE"},
                ],
            )
            passed, issues = validate_soak_log(path, smoke=True)
        self.assertFalse(passed)
        self.assertTrue(any("PAPER_APPROVE" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
