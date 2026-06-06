"""
Secure environment loading for local development and EC2 runtime.

Load order:
  1) Existing process env vars (never overwritten)
  2) Project .env (gitignored, local dev)
  3) AWS Secrets Manager (EC2/production) when enabled or keys still missing
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
LLM_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY", "DEEPSEEK_API_KEY")
DEFAULT_SECRET_NAME = "a2a/llm-api-keys"


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return "<missing>"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"


def _load_dotenv() -> Path | None:
    if not ENV_PATH.exists():
        return None

    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=ENV_PATH, override=False)
    except ImportError:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    return ENV_PATH


def _missing_llm_keys() -> list[str]:
    return [key for key in LLM_KEYS if not os.getenv(key)]


def _should_use_aws_secrets() -> bool:
    source = os.getenv("A2A_SECRETS_SOURCE", "auto").lower()
    if source == "local":
        return False
    if source == "aws":
        return True
    if os.getenv("A2A_USE_AWS_SECRETS", "").lower() in ("1", "true", "yes"):
        return True
    return bool(_missing_llm_keys())


def _load_aws_secrets() -> str | None:
    secret_name = os.getenv("A2A_LLM_SECRET_NAME", DEFAULT_SECRET_NAME)
    region = os.getenv("AWS_REGION", "ap-south-1")

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=secret_name)
        payload = json.loads(response["SecretString"])
        if not isinstance(payload, dict):
            raise ValueError("SecretString must be a JSON object")

        loaded = 0
        for key in LLM_KEYS:
            value = payload.get(key, "")
            if value and key not in os.environ:
                os.environ[key] = str(value)
                loaded += 1

        if loaded == 0 and _missing_llm_keys():
            raise ValueError("No LLM keys found in secret payload")
        return f"aws-secretsmanager://{secret_name}"
    except (ClientError, BotoCoreError, ValueError, json.JSONDecodeError):
        return None


def load_project_env() -> Literal["local", "aws", "mixed", "none"]:
    """
    Load secrets into process env.
    Returns source marker for logging/diagnostics.
    """
    dotenv_loaded = _load_dotenv() is not None
    had_missing = bool(_missing_llm_keys())

    aws_loaded = False
    if _should_use_aws_secrets() and _missing_llm_keys():
        aws_loaded = _load_aws_secrets() is not None

    if dotenv_loaded and aws_loaded:
        return "mixed"
    if aws_loaded:
        return "aws"
    if dotenv_loaded:
        return "local"
    if not had_missing:
        return "local"
    return "none"
