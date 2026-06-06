#!/usr/bin/env python3
"""
Create or update AWS Secrets Manager secret for LLM API keys.

Secret JSON shape:
  {
    "OPENAI_API_KEY": "...",
    "GROK_API_KEY": "...",
    "DEEPSEEK_API_KEY": "..."
  }

Requires local AWS credentials with secretsmanager:CreateSecret/PutSecretValue.
EC2 runtime only needs secretsmanager:GetSecretValue (see infrastructure policy).
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

from src.config.key_file import REQUIRED_KEYS, parse_key_file

DEFAULT_SOURCE = Path(r"C:\Manasjit\api_keys.txt")
DEFAULT_SECRET_NAME = "a2a/llm-api-keys"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync LLM keys to AWS Secrets Manager")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--secret-name",
        default=os.getenv("A2A_LLM_SECRET_NAME", DEFAULT_SECRET_NAME),
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

    payload = {key: values[key] for key in REQUIRED_KEYS}
    secret_string = json.dumps(payload)

    if args.dry_run:
        print(f"DRY RUN secret: {args.secret_name}")
        print(f"Region: {args.region}")
        print(f"Keys: {', '.join(REQUIRED_KEYS)}")
        return 0

    try:
        import boto3
        from botocore.exceptions import ClientError

        client = boto3.client("secretsmanager", region_name=args.region)
        try:
            client.create_secret(
                Name=args.secret_name,
                SecretString=secret_string,
                Description="A2A Trading Engine LLM API keys (OpenAI, Grok, DeepSeek)",
            )
            print(f"Created secret: {args.secret_name}")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "ResourceExistsException":
                raise
            client.put_secret_value(
                SecretId=args.secret_name,
                SecretString=secret_string,
            )
            print(f"Updated secret: {args.secret_name}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("Stored keys:", ", ".join(REQUIRED_KEYS))
    print("No secret values were printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
