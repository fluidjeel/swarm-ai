"""JSON extraction and Pydantic schema parsing for eval outputs."""

from __future__ import annotations

import json
import re
from typing import Any, Type

from pydantic import BaseModel, ValidationError

from src.agents.schemas import RegimeClassifierOutput, StrategySelectorOutput

AGENT_OUTPUT_MODELS: dict[str, Type[BaseModel]] = {
    "regime_classifier": RegimeClassifierOutput,
    "strategy_selector": StrategySelectorOutput,
}


def extract_json_object(raw: str) -> dict[str, Any]:
    """Extract first JSON object from raw LLM text (handles fenced code blocks)."""
    text = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in model response")
        text = text[start : end + 1]
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Model response JSON must be an object")
    return payload


def parse_agent_output(agent: str, raw: str) -> BaseModel:
    model_cls = AGENT_OUTPUT_MODELS.get(agent)
    if model_cls is None:
        raise ValueError(f"Unsupported agent for schema eval: {agent}")
    payload = extract_json_object(raw)
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"Schema validation failed: {exc}") from exc
