"""Load versioned prompts from S3 or local prompt registry."""

from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCAL_PROMPTS_DIR = ROOT / "prompts"

# v4.1 archived intraday prompts (historical eval fixtures only).
ARCHIVED_LOCAL_PROMPTS: dict[tuple[str, str], str] = {
    ("regime_classifier", "v1"): "archive/regime_classifier_v4.0_deprecated.md",
    ("strategy_selector", "v1"): "archive/strategy_selector_v4.0_deprecated.md",
}


def _resolve_local_prompt_path(
    local_dir: Path,
    agent_name: str,
    version: str,
) -> Path:
    local_path = local_dir / agent_name / f"{version}.md"
    if local_path.exists():
        return local_path
    archived_rel = ARCHIVED_LOCAL_PROMPTS.get((agent_name, version))
    if archived_rel is not None:
        archive_path = local_dir / archived_rel
        if archive_path.exists():
            return archive_path
    return local_path


def load_prompt(
    agent_name: str,
    version: str = "v1",
    *,
    bucket: str | None = None,
    region: str | None = None,
    local_prompts_dir: Path | None = None,
    prefer_local: bool = False,
) -> tuple[str, str]:
    """
    Load prompt markdown for an agent.

    Returns (prompt_text, source) where source is s3://... or local://...
    """
    s3_key = f"{agent_name}/{version}.md"
    local_dir = local_prompts_dir or DEFAULT_LOCAL_PROMPTS_DIR
    local_path = _resolve_local_prompt_path(local_dir, agent_name, version)

    if prefer_local:
        if not local_path.exists():
            raise FileNotFoundError(f"Local prompt not found: {local_path}")
        return local_path.read_text(encoding="utf-8"), f"local://{local_path.as_posix()}"

    bucket_name = bucket or os.getenv("A2A_S3_BUCKET", "a2a-prompts")
    region_name = region or os.getenv("AWS_REGION", "ap-south-1")

    try:
        client = boto3.client("s3", region_name=region_name)
        response = client.get_object(Bucket=bucket_name, Key=s3_key)
        body = response["Body"].read().decode("utf-8")
        return body, f"s3://{bucket_name}/{s3_key}"
    except (ClientError, BotoCoreError):
        if local_path.exists():
            return local_path.read_text(encoding="utf-8"), f"local://{local_path.as_posix()}"
        raise
