"""Thin provider clients for offline eval runs."""

from __future__ import annotations

import json
import os
from typing import Any


class LLMClientError(RuntimeError):
    pass


def _build_user_message(fixture: dict[str, Any]) -> str:
    payload = {
        "feature_payload": fixture.get("feature_payload", {}),
        "context": fixture.get("context", {}),
        "instruction": "Return only valid JSON for your agent schema.",
    }
    return json.dumps(payload, indent=2)


def call_llm(
    *,
    provider: str,
    model: str,
    system_prompt: str,
    fixture: dict[str, Any],
) -> str:
    user_message = _build_user_message(fixture)
    provider = provider.lower()

    if provider == "openai":
        return _call_openai(model=model, system_prompt=system_prompt, user_message=user_message)
    if provider == "anthropic":
        return _call_anthropic(model=model, system_prompt=system_prompt, user_message=user_message)
    raise LLMClientError(f"Unsupported provider: {provider}")


def _call_openai(*, model: str, system_prompt: str, user_message: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise LLMClientError("OPENAI_API_KEY is not set")

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    content = response.choices[0].message.content or ""
    if not content.strip():
        raise LLMClientError("OpenAI returned empty content")
    return content


def _call_anthropic(*, model: str, system_prompt: str, user_message: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise LLMClientError("ANTHROPIC_API_KEY is not set")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=512,
        temperature=0,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    chunks = [block.text for block in response.content if block.type == "text"]
    content = "".join(chunks).strip()
    if not content:
        raise LLMClientError("Anthropic returned empty content")
    return content
