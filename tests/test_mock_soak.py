"""Offline mock paper soak integration test."""

from __future__ import annotations

import unittest

from src.orchestration.mock_soak import build_mock_runner


class MockSoakTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_soak_completes_with_summary(self) -> None:
        runner = build_mock_runner()
        summary = await runner.run()
        self.assertEqual(summary["event"], "paper_soak_complete")
        self.assertGreaterEqual(summary["total_ticks"], 1)


if __name__ == "__main__":
    unittest.main()
