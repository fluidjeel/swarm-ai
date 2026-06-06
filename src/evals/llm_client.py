"""Thin provider clients for offline eval runs."""

from __future__ import annotations

import json
import os
from typing import Any

SUPPORTED_PROVIDERS = ("openai", "gemini", "grok", "deepseek")


class LLMClientError(RuntimeError):
    pass


def supported_providers() -> list[str]:
    return list(SUPPORTED_PROVIDERS)


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
    if provider == "gemini":
        return _call_gemini(model=model, system_prompt=system_prompt, user_message=user_message)
    if provider == "grok":
        return _call_openai_compatible(
            api_key_env="GROK_API_KEY",
            base_url="https://api.x.ai/v1",
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            provider_label="Grok",
        )
    if provider == "deepseek":
        return _call_openai_compatible(
            api_key_env="DEEPSEEK_API_KEY",
            base_url="https://api.deepseek.com",
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            provider_label="DeepSeek",
        )
    raise LLMClientError(f"Unsupported provider: {provider}")


def _call_openai(*, model: str, system_prompt: str, user_message: str) -> str:
    return _call_openai_compatible(
        api_key_env="OPENAI_API_KEY",
        base_url=None,
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        provider_label="OpenAI",
        json_mode=True,
    )


def _call_openai_compatible(
    *,
    api_key_env: str,
    base_url: str | None,
    model: str,
    system_prompt: str,
    user_message: str,
    provider_label: str,
    json_mode: bool = True,
) -> str:
    api_key = os.getenv(api_key_env, "")
    if not api_key:
        raise LLMClientError(f"{api_key_env} is not set")

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
    except Exception as exc:
        raise LLMClientError(f"{provider_label} request failed: {exc}") from exc

    if not content.strip():
        raise LLMClientError(f"{provider_label} returned empty content")
    return content


def _call_gemini(*, model: str, system_prompt: str, user_message: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise LLMClientError("GEMINI_API_KEY is not set")

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0,
                response_mime_type="application/json",
            ),
        )
        content = (response.text or "").strip()
    except Exception as exc:
        raise LLMClientError(f"Gemini request failed: {exc}") from exc

    if not content:
        raise LLMClientError("Gemini returned empty content")
    return content
