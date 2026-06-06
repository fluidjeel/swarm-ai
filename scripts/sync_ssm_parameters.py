#!/usr/bin/env python3
"""
Create or update AWS SSM SecureString parameters for LLM API keys.

Parameter layout (default prefix /a2a/llm):
  /a2a/llm/OPENAI_API_KEY
  /a2a/llm/ANTHROPIC_API_KEY
  /a2a/llm/GROK_API_KEY
  /a2a/llm/DEEPSEEK_API_KEY

Requires local AWS credentials with ssm:PutParameter on /a2a/llm/*.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.key_file import REQUIRED_KEYS, parse_key_file
from src.config.secrets import DEFAULT_SSM_PREFIX, ssm_parameter_name

DEFAULT_SOURCE = Path(r"C:\Manasjit\api_keys.txt")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync LLM keys to AWS SSM SecureString parameters")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--prefix",
        default=os.getenv("A2A_SSM_PARAM_PREFIX", DEFAULT_SSM_PREFIX),
        help="SSM path prefix (default: /a2a/llm)",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION", "ap-south-1"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing to AWS",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        values = parse_key_file(args.source)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"DRY RUN prefix: {args.prefix}")
        print(f"Region: {args.region}")
        for key in REQUIRED_KEYS:
            print(f"  {ssm_parameter_name(key, args.prefix)}")
        return 0

    try:
        import boto3

        client = boto3.client("ssm", region_name=args.region)
        for key in REQUIRED_KEYS:
            name = ssm_parameter_name(key, args.prefix)
            client.put_parameter(
                Name=name,
                Value=values[key],
                Type="SecureString",
                Overwrite=True,
                Description=f"A2A Trading Engine {key}",
            )
            print(f"Upserted {name}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("Stored keys:", ", ".join(REQUIRED_KEYS))
    print("No secret values were printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
