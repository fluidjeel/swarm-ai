"""
Observability layer: @trace_agent decorator for DynamoDB telemetry.

Every LLM agent invocation must be wrapped so Agent 6/7 can analyze traces.

Reference: .context/02_hldd.md §1.2
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel

from src.core.context import AgentContext

F = TypeVar("F", bound=Callable[..., Any])


class TraceWriter(Protocol):
    def write(self, record: TraceRecord) -> None: ...


@dataclass(frozen=True)
class TraceRecord:
    session_id: str
    timestamp: int
    agent_name: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    input_hash: str
    output_json: str
    validation_passed: bool
    downstream_action: str


class AgentTraceResult(BaseModel):
    """Optional wrapper for agent returns that include token usage metadata."""

    output: Any
    input_tokens: int = 0
    output_tokens: int = 0
    downstream_action: str = "NONE"
    validation_passed: bool = True


class DynamoDBTraceWriter:
    """Persists trace records to A2A_Traces (EC2 role: PutItem only)."""

    def __init__(
        self,
        table_name: str | None = None,
        region_name: str | None = None,
    ) -> None:
        self.table_name = table_name or os.getenv("A2A_DYNAMODB_TABLE", "A2A_Traces")
        self.region_name = region_name or os.getenv("AWS_REGION", "ap-south-1")
        self._table = None

    @property
    def table(self):
        if self._table is None:
            import boto3

            resource = boto3.resource("dynamodb", region_name=self.region_name)
            self._table = resource.Table(self.table_name)
        return self._table

    def write(self, record: TraceRecord) -> None:
        self.table.put_item(
            Item={
                "session_id": record.session_id,
                "timestamp": record.timestamp,
                "agent_name": record.agent_name,
                "prompt_version": record.prompt_version,
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "latency_ms": record.latency_ms,
                "input_hash": record.input_hash,
                "output_json": record.output_json,
                "validation_passed": record.validation_passed,
                "downstream_action": record.downstream_action,
            }
        )


def _default_writer() -> TraceWriter:
    return DynamoDBTraceWriter()


def _hash_input(ctx: AgentContext, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    payload = {
        "context": ctx.model_dump(mode="json"),
        "args": args,
        "kwargs": kwargs,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _serialize_output(value: Any) -> str:
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    if isinstance(value, AgentTraceResult):
        return json.dumps(value.output, default=str)
    return json.dumps(value, default=str)


def _extract_trace_metadata(
    value: Any,
    default_downstream_action: str,
) -> tuple[Any, int, int, str, bool]:
    if isinstance(value, AgentTraceResult):
        return (
            value.output,
            value.input_tokens,
            value.output_tokens,
            value.downstream_action,
            value.validation_passed,
        )
    return value, 0, 0, default_downstream_action, True


def _build_record(
    *,
    ctx: AgentContext,
    agent_name: str,
    prompt_version: str,
    default_downstream_action: str,
    started_at: float,
    input_hash: str,
    result: Any,
    validation_passed: bool,
    input_tokens: int = 0,
    output_tokens: int = 0,
    downstream_action: str | None = None,
) -> TraceRecord:
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    return TraceRecord(
        session_id=ctx.session_id,
        timestamp=int(time.time() * 1000),
        agent_name=agent_name,
        prompt_version=prompt_version,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        input_hash=input_hash,
        output_json=_serialize_output(result),
        validation_passed=validation_passed,
        downstream_action=downstream_action or default_downstream_action,
    )


def _write_trace(writer: TraceWriter, record: TraceRecord) -> None:
    writer.write(record)


def trace_agent(
    agent_name: str,
    prompt_version: str,
    downstream_action: str = "NONE",
    writer: TraceWriter | None = None,
) -> Callable[[F], F]:
    """
    Decorator that wraps an agent function and logs telemetry to DynamoDB.

    The wrapped callable must accept AgentContext as its first argument.
    """

    def decorator(func: F) -> F:
        resolved_writer = writer

        @functools.wraps(func)
        def sync_wrapper(ctx: AgentContext, *args: Any, **kwargs: Any) -> Any:
            nonlocal resolved_writer
            if not isinstance(ctx, AgentContext):
                raise TypeError(
                    f"{func.__name__} must accept AgentContext as the first argument"
                )

            active_writer = resolved_writer or _default_writer()
            input_hash = _hash_input(ctx, args, kwargs)
            started_at = time.perf_counter()

            try:
                raw_result = func(ctx, *args, **kwargs)
                result, input_tokens, output_tokens, action, validation_passed = (
                    _extract_trace_metadata(raw_result, downstream_action)
                )
                record = _build_record(
                    ctx=ctx,
                    agent_name=agent_name,
                    prompt_version=prompt_version,
                    default_downstream_action=downstream_action,
                    started_at=started_at,
                    input_hash=input_hash,
                    result=result,
                    validation_passed=validation_passed,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    downstream_action=action,
                )
                _write_trace(active_writer, record)
                return raw_result
            except Exception:
                record = _build_record(
                    ctx=ctx,
                    agent_name=agent_name,
                    prompt_version=prompt_version,
                    default_downstream_action=downstream_action,
                    started_at=started_at,
                    input_hash=input_hash,
                    result={"error": "agent_invocation_failed"},
                    validation_passed=False,
                    downstream_action="ERROR",
                )
                _write_trace(active_writer, record)
                raise

        @functools.wraps(func)
        async def async_wrapper(ctx: AgentContext, *args: Any, **kwargs: Any) -> Any:
            nonlocal resolved_writer
            if not isinstance(ctx, AgentContext):
                raise TypeError(
                    f"{func.__name__} must accept AgentContext as the first argument"
                )

            active_writer = resolved_writer or _default_writer()
            input_hash = _hash_input(ctx, args, kwargs)
            started_at = time.perf_counter()

            try:
                raw_result = await func(ctx, *args, **kwargs)
                result, input_tokens, output_tokens, action, validation_passed = (
                    _extract_trace_metadata(raw_result, downstream_action)
                )
                record = _build_record(
                    ctx=ctx,
                    agent_name=agent_name,
                    prompt_version=prompt_version,
                    default_downstream_action=downstream_action,
                    started_at=started_at,
                    input_hash=input_hash,
                    result=result,
                    validation_passed=validation_passed,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    downstream_action=action,
                )
                await asyncio.to_thread(_write_trace, active_writer, record)
                return raw_result
            except Exception:
                record = _build_record(
                    ctx=ctx,
                    agent_name=agent_name,
                    prompt_version=prompt_version,
                    default_downstream_action=downstream_action,
                    started_at=started_at,
                    input_hash=input_hash,
                    result={"error": "agent_invocation_failed"},
                    validation_passed=False,
                    downstream_action="ERROR",
                )
                await asyncio.to_thread(_write_trace, active_writer, record)
                raise

        if inspect.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    return decorator
