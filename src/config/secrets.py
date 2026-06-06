"""
Secure environment loading for local development.

Loads variables from project-root .env (gitignored). Never logs secret values.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"


def load_project_env() -> Path | None:
    """Load .env into process env if present. Returns path when loaded."""
    if not ENV_PATH.exists():
        return None

    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=ENV_PATH, override=False)
        return ENV_PATH
    except ImportError:
        # Minimal fallback parser if python-dotenv is unavailable.
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


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return "<missing>"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"
