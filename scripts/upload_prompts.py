#!/usr/bin/env python3
"""Upload local prompt registry files to s3://a2a-prompts/."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "prompts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload A2A prompts to S3")
    parser.add_argument(
        "--bucket",
        default=os.getenv("A2A_S3_BUCKET", "a2a-prompts"),
        help="Target S3 bucket",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION", "ap-south-1"),
        help="AWS region",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print files that would be uploaded",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = sorted(PROMPTS_DIR.rglob("*.md"))
    if not files:
        print(f"No prompt files found under {PROMPTS_DIR}")
        return 1

    if args.dry_run:
        for path in files:
            key = path.relative_to(PROMPTS_DIR).as_posix()
            print(f"DRY RUN upload: s3://{args.bucket}/{key}")
        return 0

    client = boto3.client("s3", region_name=args.region)
    uploaded = 0
    for path in files:
        key = path.relative_to(PROMPTS_DIR).as_posix()
        try:
            client.upload_file(
                Filename=str(path),
                Bucket=args.bucket,
                Key=key,
                ExtraArgs={"ContentType": "text/markdown"},
            )
            print(f"Uploaded s3://{args.bucket}/{key}")
            uploaded += 1
        except (ClientError, BotoCoreError) as exc:
            print(f"ERROR uploading {key}: {exc}", file=sys.stderr)
            return 1

    print(f"Done. Uploaded {uploaded} prompt file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
