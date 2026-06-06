#!/usr/bin/env python3
"""Smoke-test configured LLM providers using gitignored .env secrets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.secrets import load_project_env, mask_secret
from src.evals.llm_client import LLMClientError, call_llm, supported_providers
from src.evals.parser import parse_agent_output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify LLM providers")
    parser.add_argument(
        "--provider",
        choices=supported_providers(),
        default="",
        help="Test one provider (default: all configured)",
    )
    return parser.parse_args()


def _fixture() -> dict:
    return {
        "feature_payload": {
            "NIFTY_500_AD_Ratio": 1.4,
            "vix": 14.0,
            "VIX_ATR_Divergence": 0.2,
            "Expiry_Weighted_PCR_Momentum": 0.1,
            "dte": 7,
        },
        "context": {},
    }


def _default_model(provider: str) -> str:
    return {
        "openai": "gpt-4o-mini",
        "gemini": "gemini-2.0-flash",
        "grok": "grok-4.3",
        "deepseek": "deepseek-chat",
    }[provider]


def _system_prompt() -> str:
    return (
        "Return JSON only with keys: regime_decision, rationale. "
        "regime_decision must be one of TREND_UP,TREND_DOWN,RANGE,CHOPPY,UNCERTAIN."
    )


def main() -> int:
    env_path = load_project_env()
    if not env_path:
        print("ERROR: .env not found. Run: python scripts/setup_local_env.py", file=sys.stderr)
        return 1

    args = parse_args()
    providers = [args.provider] if args.provider else supported_providers()

    print("LLM Provider Smoke Test")
    print(f"  Env file: {env_path}")
    print()

    failures = 0
    for provider in providers:
        import os

        env_map = {
            "openai": "OPENAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "grok": "GROK_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }
        key = os.getenv(env_map[provider], "")
        print(f"[{provider}] key={mask_secret(key)}")
        try:
            raw = call_llm(
                provider=provider,
                model=_default_model(provider),
                system_prompt=_system_prompt(),
                fixture=_fixture(),
            )
            parsed = parse_agent_output("regime_classifier", raw)
            print(
                f"[PASS] {provider} -> {parsed.regime_decision.value} "
                f"({json.dumps(parsed.model_dump(), ensure_ascii=True)[:120]}...)"
            )
        except Exception as exc:
            failures += 1
            print(f"[FAIL] {provider} -> {exc}")

    print()
    if failures:
        print(f"{failures} provider(s) failed.")
        return 1
    print("All tested providers passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
