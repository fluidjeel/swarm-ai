"""
Pydantic output schemas for LLM agent responses.

Reference: .context/02_hldd.md §1.4 (Schema Eval)

DEPRECATED v4.1: RegimeClassifierOutput and StrategySelectorOutput are LLM JSON
output schemas. They are no longer used by the intraday path. Kept for backward
compat with src/evals/parser.py which validates old eval fixtures. New code MUST
NOT import these classes for the hot path.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.core.context import RegimeLabel


class AgentOutputModel(BaseModel):
    # LLM JSON arrives as strings; allow typed coercion while forbidding extra keys.
    model_config = ConfigDict(extra="forbid")


class RegimeClassifierOutput(AgentOutputModel):
    regime_decision: RegimeLabel
    rationale: str = Field(..., min_length=8, max_length=512)


class StrategySelectorOutput(AgentOutputModel):
    strategy: str = Field(..., min_length=1, max_length=64)
    supporting_signals: list[str] = Field(..., min_length=2, max_length=6)
    rationale: str = Field(..., min_length=8, max_length=512)
