"""Parse structured local API key files without logging secret values."""

from __future__ import annotations

import re
from pathlib import Path

REQUIRED_KEYS = ("OPENAI_API_KEY", "GROK_API_KEY", "DEEPSEEK_API_KEY")

LEGACY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OPENAI_API_KEY", re.compile(r"^sk-proj-", re.IGNORECASE)),
    ("GROK_API_KEY", re.compile(r"^xai-", re.IGNORECASE)),
    ("DEEPSEEK_API_KEY", re.compile(r"^sk-[0-9a-f]", re.IGNORECASE)),
]


def parse_key_file(path: Path) -> dict[str, str]:
    """
    Parse KEY=VALUE lines or legacy one-key-per-line format.
    Ignores blank lines, comments, and GEMINI_API_KEY if present.
    """
    if not path.exists():
        raise FileNotFoundError(f"Key file not found: {path}")

    discovered: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if "=" in line and not line.startswith("="):
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key == "GEMINI_API_KEY":
                continue
            if key in REQUIRED_KEYS and value:
                discovered[key] = value
            continue

        for env_name, pattern in LEGACY_PATTERNS:
            if pattern.match(line) and env_name not in discovered:
                discovered[env_name] = line
                break

    missing = [key for key in REQUIRED_KEYS if key not in discovered]
    if missing:
        raise ValueError(f"Missing keys in source file: {', '.join(missing)}")

    return {key: discovered[key] for key in REQUIRED_KEYS}


def format_key_file(values: dict[str, str]) -> str:
    lines = [
        "# A2A LLM API Keys",
        "# Local file only. Never commit to git.",
        "# Used by setup_local_env.py and sync_secrets_manager.py",
        "",
    ]
    for key in REQUIRED_KEYS:
        lines.append(f"{key}={values[key]}")
    lines.append("")
    return "\n".join(lines)
