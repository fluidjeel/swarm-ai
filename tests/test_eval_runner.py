"""Tests for eval parser, prompt loading fallback, and schema-only mode."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.evals.parser import extract_json_object, parse_agent_output
from src.evals.prompt_loader import load_prompt

ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "evals" / "fixtures"


class EvalParserTests(unittest.TestCase):
    def test_extract_json_from_fenced_block(self) -> None:
        raw = '```json\n{"regime_decision":"RANGE","rationale":"range-bound market"}\n```'
        payload = extract_json_object(raw)
        self.assertEqual(payload["regime_decision"], "RANGE")

    def test_parse_regime_schema(self) -> None:
        raw = json.dumps(
            {
                "regime_decision": "TREND_UP",
                "rationale": "Breadth expansion with stable VIX.",
            }
        )
        parsed = parse_agent_output("regime_classifier", raw)
        self.assertEqual(parsed.regime_decision.value, "TREND_UP")

    def test_parse_strategy_requires_two_signals(self) -> None:
        raw = json.dumps(
            {
                "strategy": "iron_condor",
                "supporting_signals": ["only_one"],
                "rationale": "invalid signal count",
            }
        )
        with self.assertRaises(ValueError):
            parse_agent_output("strategy_selector", raw)


class PromptLoaderTests(unittest.TestCase):
    def test_load_local_prompt(self) -> None:
        text, source = load_prompt("regime_classifier", "v1", prefer_local=True)
        self.assertIn("Regime Classifier", text)
        self.assertTrue(source.startswith("local://"))


class SchemaOnlyRunnerTests(unittest.TestCase):
    def test_schema_only_mode_passes_all_fixtures(self) -> None:
        from scripts.eval_runner import _run_schema_only, _load_fixtures

        fixtures = _load_fixtures("all")
        self.assertGreaterEqual(len(fixtures), 20)
        report = _run_schema_only(fixtures)
        self.assertEqual(report.failed_count, 0)
        self.assertGreaterEqual(report.passed_count, 20)


if __name__ == "__main__":
    unittest.main()
