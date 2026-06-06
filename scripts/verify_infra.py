#!/usr/bin/env python3
"""
Epic 1.1 smoke test: AWS (S3 + DynamoDB) and Fyers API reachability.

Uses the default boto3 credential chain (env vars, ~/.aws/credentials, IAM role).
Operations mirror A2A-Trading-EC2-Role permissions where possible:
  - dynamodb:PutItem on A2A_Traces
  - s3:GetObject on a2a-prompts/*

Exit code 0 only if all enabled checks pass.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    print("ERROR: boto3 is required. Install with: pip install boto3", file=sys.stderr)
    sys.exit(1)

try:
    import urllib.error
    import urllib.request
except ImportError:
    pass  # stdlib always available


# ---------------------------------------------------------------------------
# Configuration (environment variables)
# ---------------------------------------------------------------------------

AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
S3_BUCKET = os.getenv("A2A_S3_BUCKET", "a2a-prompts")
DYNAMODB_TABLE = os.getenv("A2A_DYNAMODB_TABLE", "A2A_Traces")
S3_PROBE_KEY = os.getenv("A2A_S3_PROBE_KEY", "healthcheck/probe.txt")

SKIP_AWS = os.getenv("A2A_SKIP_AWS", "").lower() in ("1", "true", "yes")
SKIP_FYERS = os.getenv("A2A_SKIP_FYERS", "").lower() in ("1", "true", "yes")

FYERS_BASE_URL = os.getenv("FYERS_API_BASE_URL", "https://api.fyers.in/api/v2")
FYERS_APP_ID = os.getenv("FYERS_APP_ID", "DUMMY_APP_ID")
FYERS_ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN", "DUMMY_ACCESS_TOKEN")


def _ok(message: str) -> None:
    print(f"[PASS] {message}")


def _fail(message: str) -> None:
    print(f"[FAIL] {message}")


def verify_dynamodb_putitem() -> bool:
    """Write a single probe trace row (EC2 role: PutItem only)."""
    session_id = f"infra-verify-{uuid.uuid4().hex[:12]}"
    timestamp = int(time.time() * 1000)
    item: dict[str, Any] = {
        "session_id": {"S": session_id},
        "timestamp": {"N": str(timestamp)},
        "agent_name": {"S": "infra_verify"},
        "prompt_version": {"S": "n/a"},
        "input_tokens": {"N": "0"},
        "output_tokens": {"N": "0"},
        "latency_ms": {"N": "0"},
        "input_hash": {"S": "probe"},
        "output_json": {"S": json.dumps({"probe": True})},
        "validation_passed": {"BOOL": True},
        "downstream_action": {"S": "SMOKE_TEST"},
    }

    client = boto3.client("dynamodb", region_name=AWS_REGION)
    try:
        client.put_item(TableName=DYNAMODB_TABLE, Item=item)
        _ok(
            f"DynamoDB PutItem -> {DYNAMODB_TABLE} "
            f"(session_id={session_id}, timestamp={timestamp})"
        )
        print(
            "       NOTE: EC2 role has no DeleteItem; probe rows remain for manual cleanup."
        )
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        message = str(exc)
        _fail(f"DynamoDB PutItem ({code}): {exc}")
        if code == "ValidationException" and "timestamp" in message:
            print(
                "       HINT: A2A_Traces sort key must be Number (N), per HLDD. "
                "If the table was created with timestamp as String, recreate the table "
                "or the probe will always fail."
            )
        return False
    except BotoCoreError as exc:
        _fail(f"DynamoDB client error: {exc}")
        return False


def verify_s3_getobject() -> bool:
    """Read probe object (EC2 role: GetObject only on a2a-prompts/*)."""
    client = boto3.client("s3", region_name=AWS_REGION)
    try:
        response = client.get_object(Bucket=S3_BUCKET, Key=S3_PROBE_KEY)
        body = response["Body"].read(64)
        _ok(f"S3 GetObject -> s3://{S3_BUCKET}/{S3_PROBE_KEY} ({len(body)} bytes read)")
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in ("NoSuchKey", "404"):
            _fail(
                f"S3 object missing: s3://{S3_BUCKET}/{S3_PROBE_KEY}. "
                f"Upload a probe file before running this check."
            )
        elif code in ("AccessDenied", "403"):
            _fail(f"S3 GetObject denied ({code}). Check bucket policy and IAM role.")
        else:
            _fail(f"S3 GetObject ({code}): {exc}")
        return False
    except BotoCoreError as exc:
        _fail(f"S3 client error: {exc}")
        return False


def verify_fyers_ping() -> bool:
    """
    Placeholder Fyers connectivity check.

    Uses dummy credentials until FYERS_APP_ID / FYERS_ACCESS_TOKEN are set.
    Expects HTTP response (even 401) to confirm network reachability.
    """
    if FYERS_APP_ID.startswith("DUMMY") or FYERS_ACCESS_TOKEN.startswith("DUMMY"):
        print(
            "[SKIP] Fyers: dummy credentials detected "
            "(set FYERS_APP_ID and FYERS_ACCESS_TOKEN for a real auth test)."
        )
        return True

    url = f"{FYERS_BASE_URL.rstrip('/')}/profile"
    headers = {
        "Authorization": f"{FYERS_APP_ID}:{FYERS_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
            if 200 <= status < 300:
                _ok(f"Fyers API reachable ({status}) -> {url}")
                return True
            _fail(f"Fyers API unexpected status {status} -> {url}")
            return False
    except urllib.error.HTTPError as exc:
        # 401/403 still proves TLS + routing to Fyers (credentials may be wrong).
        if exc.code in (401, 403):
            _ok(
                f"Fyers API reachable (HTTP {exc.code} — auth not verified) -> {url}"
            )
            return True
        _fail(f"Fyers API HTTP {exc.code}: {exc.reason}")
        return False
    except urllib.error.URLError as exc:
        _fail(f"Fyers API unreachable: {exc.reason}")
        return False


def main() -> int:
    print("A2A Trading Engine — Epic 1.1 Infrastructure Smoke Test")
    print(f"  Region:          {AWS_REGION}")
    print(f"  S3 bucket:       {S3_BUCKET}")
    print(f"  DynamoDB table:  {DYNAMODB_TABLE}")
    print(f"  S3 probe key:    {S3_PROBE_KEY}")
    print()

    results: list[bool] = []

    if SKIP_AWS:
        print("[SKIP] AWS checks disabled (A2A_SKIP_AWS=1)")
    else:
        results.append(verify_dynamodb_putitem())
        results.append(verify_s3_getobject())

    if SKIP_FYERS:
        print("[SKIP] Fyers check disabled (A2A_SKIP_FYERS=1)")
    else:
        results.append(verify_fyers_ping())

    print()
    if not results:
        print("No checks executed.")
        return 1

    if all(results):
        print("All executed checks passed.")
        return 0

    print("One or more checks failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
