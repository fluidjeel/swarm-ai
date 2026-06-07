"""
Secure environment loading for local development and EC2 runtime.

Load order:
  1) Existing process env vars (never overwritten)
  2) Project .env (gitignored, local dev)
  3) AWS SSM Parameter Store SecureString (EC2/production)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from src.config.key_file import REQUIRED_KEYS

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
LLM_KEYS = REQUIRED_KEYS
DEFAULT_SSM_PREFIX = "/a2a/llm"
FYERS_ENV_KEYS = ("FYERS_APP_ID", "FYERS_ACCESS_TOKEN")


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return "<missing>"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"


def ssm_parameter_name(env_key: str, prefix: str | None = None) -> str:
    base = (prefix or os.getenv("A2A_SSM_PARAM_PREFIX", DEFAULT_SSM_PREFIX)).rstrip("/")
    return f"{base}/{env_key}"


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


def _should_use_ssm() -> bool:
    source = os.getenv("A2A_SECRETS_SOURCE", "auto").lower()
    if source == "local":
        return False
    if source in ("ssm", "aws"):
        return True
    if os.getenv("A2A_USE_AWS_SECRETS", "").lower() in ("1", "true", "yes"):
        return True
    if os.getenv("A2A_USE_SSM_PARAMETERS", "").lower() in ("1", "true", "yes"):
        return True
    return bool(_missing_llm_keys())


def _load_ssm_parameters() -> str | None:
    prefix = os.getenv("A2A_SSM_PARAM_PREFIX", DEFAULT_SSM_PREFIX)
    region = os.getenv("AWS_REGION", "ap-south-1")

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        client = boto3.client("ssm", region_name=region)
        loaded = 0
        for key in LLM_KEYS:
            if os.getenv(key):
                continue
            name = ssm_parameter_name(key, prefix)
            response = client.get_parameter(Name=name, WithDecryption=True)
            value = response.get("Parameter", {}).get("Value", "")
            if value:
                os.environ[key] = value
                loaded += 1

        if loaded == 0 and _missing_llm_keys():
            raise ValueError("No LLM keys loaded from SSM")
        return f"aws-ssm://{prefix}"
    except (ClientError, BotoCoreError, ValueError):
        return None


def _load_fyers_from_ssm() -> bool:
    prefix = os.getenv("A2A_FYERS_SSM_PREFIX", os.getenv("A2A_SSM_PARAM_PREFIX", DEFAULT_SSM_PREFIX))
    region = os.getenv("AWS_REGION", "ap-south-1")
    missing = [key for key in FYERS_ENV_KEYS if not os.getenv(key)]
    if not missing:
        return False

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        client = boto3.client("ssm", region_name=region)
        loaded = 0
        for key in missing:
            name = ssm_parameter_name(key, prefix)
            response = client.get_parameter(Name=name, WithDecryption=True)
            value = response.get("Parameter", {}).get("Value", "")
            if value:
                os.environ[key] = value
                loaded += 1
        return loaded > 0
    except (ClientError, BotoCoreError, ValueError):
        return False


def get_fyers_credentials() -> tuple[str, str]:
    """Load FYERS_APP_ID and FYERS_ACCESS_TOKEN from .env or SSM."""
    load_project_env()
    if any(not os.getenv(key) for key in FYERS_ENV_KEYS):
        _load_fyers_from_ssm()

    app_id = os.getenv("FYERS_APP_ID", "").strip()
    access_token = os.getenv("FYERS_ACCESS_TOKEN", "").strip()
    if not app_id or not access_token:
        raise ValueError(
            "FYERS_APP_ID and FYERS_ACCESS_TOKEN must be set in .env or SSM."
        )
    return app_id, access_token


def load_project_env() -> Literal["local", "ssm", "mixed", "none"]:
    """
    Load secrets into process env.
    Returns source marker for logging/diagnostics.
    """
    dotenv_loaded = _load_dotenv() is not None
    had_missing = bool(_missing_llm_keys())

    ssm_loaded = False
    if _should_use_ssm() and _missing_llm_keys():
        ssm_loaded = _load_ssm_parameters() is not None

    if dotenv_loaded and ssm_loaded:
        return "mixed"
    if ssm_loaded:
        return "ssm"
    if dotenv_loaded:
        return "local"
    if not had_missing:
        return "local"
    return "none"
