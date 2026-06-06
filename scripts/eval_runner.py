#!/usr/bin/env python3
"""
Offline eval harness for A2A prompt + schema validation.

Modes:
  --schema-only   Validate golden_output in fixtures (no LLM/API spend)
  default         Load prompt from S3 (fallback local), call OpenAI/Anthropic, validate schema

Epic 1.3 DoD:
  Invoke LLM APIs, read S3 prompt, output Pass/Fail based on Pydantic schema validation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evals.llm_client import LLMClientError, call_llm
from src.evals.parser import parse_agent_output
from src.evals.prompt_loader import load_prompt
from src.evals.report import EvalCaseResult, EvalReport
from src.security.sanitizer import SanitizerError, sanitize_feature_payload

FIXTURES_DIR = ROOT / "evals" / "fixtures"
AGENT_PROMPT_MAP = {
    "regime_classifier": "regime_classifier",
    "strategy_selector": "strategy_selector",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run A2A offline eval suite")
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Validate fixture golden_output only (no LLM calls)",
    )
    parser.add_argument(
        "--agent",
        choices=["regime_classifier", "strategy_selector", "all"],
        default="all",
        help="Which agent fixtures to run",
    )
    parser.add_argument(
        "--provider",
        default=os.getenv("EVAL_LLM_PROVIDER", "openai"),
        choices=["openai", "anthropic"],
        help="LLM provider for live eval mode",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("EVAL_LLM_MODEL", ""),
        help="Model name (defaults: gpt-4o-mini / claude-3-5-haiku-latest)",
    )
    parser.add_argument(
        "--prompt-version",
        default=os.getenv("EVAL_PROMPT_VERSION", "v1"),
        help="Prompt version suffix (v1, v2, ...)",
    )
    parser.add_argument(
        "--prefer-local-prompts",
        action="store_true",
        help="Load prompts from ./prompts instead of S3",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max fixtures to run (0 = all)",
    )
    return parser.parse_args()


def _default_model(provider: str) -> str:
    if provider == "anthropic":
        return "claude-3-5-haiku-latest"
    return "gpt-4o-mini"


def _load_fixtures(agent_filter: str) -> list[dict]:
    fixtures: list[dict] = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if agent_filter != "all" and payload.get("agent") != agent_filter:
            continue
        fixtures.append(payload)
    if not fixtures:
        raise RuntimeError(f"No fixtures found in {FIXTURES_DIR}")
    return fixtures


def _validate_behavior(agent: str, parsed: object, expected: dict) -> tuple[bool, str]:
    if not expected:
        return True, "no behavioral expectation"

    if agent == "regime_classifier":
        actual = getattr(parsed, "regime_decision", None)
        exp = expected.get("regime_decision")
        if actual and exp and actual.value == exp:
            return True, f"regime matched ({exp})"
        return False, f"regime mismatch expected={exp} actual={getattr(actual, 'value', actual)}"

    if agent == "strategy_selector":
        actual = getattr(parsed, "strategy", None)
        exp = expected.get("strategy")
        if actual and exp and actual == exp:
            return True, f"strategy matched ({exp})"
        return False, f"strategy mismatch expected={exp} actual={actual}"

    return True, "behavior check skipped"


def _run_schema_only(fixtures: list[dict]) -> EvalReport:
    report = EvalReport()
    for fixture in fixtures:
        agent = fixture["agent"]
        fixture_id = fixture["id"]
        try:
            sanitize_feature_payload(fixture.get("feature_payload", {}))
            golden = json.dumps(fixture["golden_output"])
            parsed = parse_agent_output(agent, golden)
            behavior_ok, behavior_details = _validate_behavior(
                agent, parsed, fixture.get("expected", {})
            )
            passed = behavior_ok
            details = f"schema ok; {behavior_details}"
        except (SanitizerError, ValueError, KeyError, json.JSONDecodeError) as exc:
            passed = False
            details = str(exc)

        report.results.append(
            EvalCaseResult(
                fixture_id=fixture_id,
                agent=agent,
                passed=passed,
                schema_valid="schema ok" in details,
                behavior_valid=passed,
                details=details,
            )
        )
    return report


def _run_live(
    fixtures: list[dict],
    *,
    provider: str,
    model: str,
    prompt_version: str,
    prefer_local_prompts: bool,
) -> EvalReport:
    report = EvalReport()
    prompt_cache: dict[str, tuple[str, str]] = {}

    for fixture in fixtures:
        agent = fixture["agent"]
        fixture_id = fixture["id"]
        prompt_name = AGENT_PROMPT_MAP[agent]

        if prompt_name not in prompt_cache:
            prompt_cache[prompt_name] = load_prompt(
                prompt_name,
                prompt_version,
                prefer_local=prefer_local_prompts,
            )
        system_prompt, prompt_source = prompt_cache[prompt_name]

        try:
            sanitized = sanitize_feature_payload(fixture.get("feature_payload", {}))
            fixture_for_llm = dict(fixture)
            fixture_for_llm["feature_payload"] = sanitized

            raw = call_llm(
                provider=provider,
                model=model,
                system_prompt=system_prompt,
                fixture=fixture_for_llm,
            )
            parsed = parse_agent_output(agent, raw)
            behavior_ok, behavior_details = _validate_behavior(
                agent, parsed, fixture.get("expected", {})
            )
            passed = behavior_ok
            details = f"schema ok; {behavior_details}; prompt={prompt_source}"
            schema_valid = True
            behavior_valid = behavior_ok
        except (SanitizerError, LLMClientError, ValueError) as exc:
            passed = False
            schema_valid = "Schema validation failed" not in str(exc)
            behavior_valid = False
            details = str(exc)

        report.results.append(
            EvalCaseResult(
                fixture_id=fixture_id,
                agent=agent,
                passed=passed,
                schema_valid=schema_valid,
                behavior_valid=behavior_valid,
                details=details,
            )
        )
    return report


def main() -> int:
    args = parse_args()
    fixtures = _load_fixtures(args.agent)
    if args.limit > 0:
        fixtures = fixtures[: args.limit]

    print("A2A Eval Runner")
    print(f"  Fixtures: {len(fixtures)}")
    print(f"  Mode:     {'schema-only' if args.schema_only else 'live-llm'}")
    print()

    if args.schema_only:
        report = _run_schema_only(fixtures)
    else:
        model = args.model or _default_model(args.provider)
        print(f"  Provider: {args.provider}")
        print(f"  Model:    {model}")
        print()
        report = _run_live(
            fixtures,
            provider=args.provider,
            model=model,
            prompt_version=args.prompt_version,
            prefer_local_prompts=args.prefer_local_prompts,
        )

    print()
    report.print_summary()
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
