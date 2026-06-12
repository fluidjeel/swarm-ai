#!/usr/bin/env python3
"""
Post-soak diagnostic utility for A2A Trading Engine v4.1.

Pulls execution traces from DynamoDB (A2A_Traces) or a local JSON/JSONL dump,
then prints an executive summary covering timeline continuity, error/rejection
matrices, paper PnL, and zombie positions.

Standalone — does not import from src/.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import boto3
    from boto3.dynamodb.conditions import Key
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover - optional for --file-only runs
    boto3 = None  # type: ignore[assignment]
    BotoCoreError = ClientError = Exception  # type: ignore[misc, assignment]

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "logs"
FRICTION_RUPEES = 40.0
DEFAULT_TICK_INTERVAL_SEC = 300
DEFAULT_TABLE = "A2A_Traces"
DEFAULT_REGION = "ap-south-1"

TICK_EVENTS = frozenset({"paper_tick", "tick_trace"})
TRADE_ENTRY_EVENTS = frozenset({"PAPER_APPROVE"})
TRADE_EXIT_EVENTS = frozenset({"PAPER_EXIT"})


@dataclass(frozen=True, slots=True)
class TraceEvent:
    session_id: str
    timestamp: datetime
    timestamp_ms: int
    event: str
    payload: dict[str, Any]


@dataclass
class TradeLifecycle:
    entry: TraceEvent
    exit: TraceEvent | None
    strategy: str
    gross_pnl: float
    net_pnl: float
    friction: float
    is_win: bool
    executed: bool


@dataclass
class SoakAnalysis:
    session_id: str
    events: list[TraceEvent]
    wall_clock_start: datetime | None = None
    wall_clock_end: datetime | None = None
    tick_events: list[TraceEvent] = field(default_factory=list)
    time_gaps: list[tuple[TraceEvent, TraceEvent, float]] = field(default_factory=list)
    exception_counts: Counter[str] = field(default_factory=Counter)
    gatekeeper_rejects: Counter[str] = field(default_factory=Counter)
    critic_rejects: Counter[str] = field(default_factory=Counter)
    state_mismatches: list[str] = field(default_factory=list)
    trades: list[TradeLifecycle] = field(default_factory=list)
    zombie_positions: list[str] = field(default_factory=list)
    expected_ticks: int = 0
    actual_ticks: int = 0


def _parse_timestamp(value: Any) -> tuple[datetime, int]:
    if value is None:
        now = datetime.now(timezone.utc)
        return now, int(now.timestamp() * 1000)

    if isinstance(value, (int, float)):
        ms = int(value)
        if ms < 10_000_000_000:
            ms *= 1000
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc), ms

    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc), int(dt.timestamp() * 1000)
    except ValueError:
        pass

    now = datetime.now(timezone.utc)
    return now, int(now.timestamp() * 1000)


def _unwrap_dynamodb_attr(value: Any) -> Any:
    if not isinstance(value, dict) or len(value) != 1:
        return value
    key = next(iter(value))
    if key in {"S", "N", "BOOL", "NULL"}:
        raw = value[key]
        if key == "N":
            return float(raw) if "." in str(raw) else int(raw)
        if key == "BOOL":
            return bool(raw)
        if key == "NULL":
            return None
        return raw
    if key == "M":
        return {k: _unwrap_dynamodb_attr(v) for k, v in value[key].items()}
    if key == "L":
        return [_unwrap_dynamodb_attr(v) for v in value[key]]
    return value


def _flatten_record(raw: dict[str, Any]) -> dict[str, Any]:
    record = {k: _unwrap_dynamodb_attr(v) for k, v in raw.items()}
    if "payload_json" in record and isinstance(record["payload_json"], str):
        try:
            nested = json.loads(record["payload_json"])
            if isinstance(nested, dict):
                merged = dict(record)
                merged.update(nested)
                return merged
        except json.JSONDecodeError:
            pass
    if "output_json" in record and isinstance(record["output_json"], str):
        try:
            nested = json.loads(record["output_json"])
            if isinstance(nested, dict) and "event" not in record:
                merged = dict(record)
                merged.setdefault("_agent_output", nested)
                return merged
        except json.JSONDecodeError:
            pass
    return record


def _event_name(record: dict[str, Any]) -> str:
    if record.get("event"):
        return str(record["event"])
    if record.get("agent_name"):
        return "agent_trace"
    return "unknown"


def _to_trace_event(record: dict[str, Any]) -> TraceEvent | None:
    flat = _flatten_record(record)
    session_id = flat.get("session_id")
    if not session_id:
        return None
    ts, ts_ms = _parse_timestamp(flat.get("timestamp"))
    return TraceEvent(
        session_id=str(session_id),
        timestamp=ts,
        timestamp_ms=ts_ms,
        event=_event_name(flat),
        payload=flat,
    )


def load_traces_from_file(path: Path) -> list[TraceEvent]:
    text = path.read_text(encoding="utf-8-sig")
    records: list[dict[str, Any]] = []

    if path.suffix.lower() == ".jsonl":
        for line in text.splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    else:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict):
            if "Items" in parsed:
                records = parsed["Items"]
            elif "events" in parsed:
                records = parsed["events"]
            else:
                records = [parsed]
        else:
            raise ValueError(f"Unsupported JSON structure in {path}")

    events = [_to_trace_event(row) for row in records]
    return sorted(
        [event for event in events if event is not None],
        key=lambda item: item.timestamp_ms,
    )


def load_traces_from_dynamodb(
    *,
    table_name: str,
    session_id: str,
    region: str,
) -> list[TraceEvent]:
    if boto3 is None:
        raise RuntimeError("boto3 is required for DynamoDB access. pip install boto3")

    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    events: list[TraceEvent] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("session_id").eq(session_id),
    }

    while True:
        response = table.query(**kwargs)
        for item in response.get("Items", []):
            event = _to_trace_event(item)
            if event is not None:
                events.append(event)
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    return sorted(events, key=lambda item: item.timestamp_ms)


def _classify_exception(message: str) -> str:
    upper = message.upper()
    if "429" in message or "REQUEST LIMIT" in upper:
        return "HTTP 429"
    if "MARKETDATAERROR" in upper.replace("_", "") or "MARKET DATA" in upper:
        return "MarketDataError"
    if "EXECUTIONFAILEDERROR" in upper.replace("_", ""):
        return "ExecutionFailedError"
    if "TIMEOUT" in upper:
        return "TimeoutError"
    if "TICK_LOCK" in upper:
        return "TickLockError"
    if "MEMORY_GUARD" in upper:
        return "MemoryGuardError"
    if "EXPIRYSELECTIONERROR" in upper.replace("_", ""):
        return "ExpirySelectionError"
    if "STRIKESELECTIONERROR" in upper.replace("_", ""):
        return "StrikeSelectionError"
    return message.split(":", 1)[0].strip()[:80] or "UnknownError"


def _collect_exceptions(events: Iterable[TraceEvent]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for event in events:
        payload = event.payload
        if event.event == "paper_tick_error":
            detail = str(payload.get("detail", ""))
            code = str(payload.get("code", ""))
            label = _classify_exception(f"{code}: {detail}")
            counts[label] += 1
            continue

        if event.event == "paper_shared_features_error":
            counts[_classify_exception(str(payload.get("detail", "")))] += 1
            continue

        if payload.get("downstream_action") == "ERROR":
            counts["AgentTraceError"] += 1

        if payload.get("validation_passed") is False:
            counts["ValidationFailed"] += 1

        detail = payload.get("detail")
        if isinstance(detail, str) and detail:
            counts[_classify_exception(detail)] += 1

        agent_output = payload.get("_agent_output")
        if isinstance(agent_output, dict) and agent_output.get("error"):
            counts[_classify_exception(str(agent_output["error"]))] += 1
    return counts


def _collect_rejections(events: Iterable[TraceEvent]) -> tuple[Counter[str], Counter[str]]:
    gatekeeper: Counter[str] = Counter()
    critic: Counter[str] = Counter()

    for event in events:
        payload = event.payload
        gate = payload.get("gatekeeper_decision")
        if isinstance(gate, dict) and gate.get("verdict") == "REJECT":
            rule = gate.get("rule_id") or gate.get("reason") or "unknown"
            gatekeeper[str(rule)] += 1

        critic_dec = payload.get("critic_decision")
        if isinstance(critic_dec, dict) and critic_dec.get("status") == "REJECT":
            reason = critic_dec.get("reason") or "unknown"
            critic[str(reason)] += 1

        if event.event == "agent_trace" and payload.get("validation_passed") is False:
            gatekeeper["agent_validation_failed"] += 1

    return gatekeeper, critic


def _detect_state_mismatches(events: list[TraceEvent]) -> list[str]:
    mismatches: list[str] = []
    tick_by_number: dict[int, TraceEvent] = {}

    for event in events:
        if event.event not in TICK_EVENTS:
            continue
        tick_no = event.payload.get("tick_number")
        if isinstance(tick_no, int):
            tick_by_number[tick_no] = event

        gate = event.payload.get("gatekeeper_decision")
        final_outcome = event.payload.get("final_outcome")
        open_pos = event.payload.get("open_position")

        if (
            isinstance(gate, dict)
            and gate.get("verdict") == "APPROVE"
            and final_outcome in {"WOULD_TRADE", "EXIT_MARKET"}
            and open_pos is None
            and event.payload.get("exit_action") != "EXIT_MARKET"
        ):
            ts = event.timestamp.isoformat()
            mismatches.append(
                f"{ts} tick#{event.payload.get('tick_number', '?')}: "
                "gatekeeper APPROVE but open_position missing after entry path"
            )

    approve_times: list[datetime] = []
    order_ack_times: list[datetime] = []
    for event in events:
        if event.event == "PAPER_APPROVE":
            approve_times.append(event.timestamp)
        if event.event == "PAPER_ORDER_ACK":
            order_ack_times.append(event.timestamp)

    for approve_ts in approve_times:
        if not any(abs((ack_ts - approve_ts).total_seconds()) < 120 for ack_ts in order_ack_times):
            mismatches.append(
                f"{approve_ts.isoformat()}: PAPER_APPROVE without PAPER_ORDER_ACK within 120s "
                "(may be expected for dry-run aborts or cash_no_trade intents)"
            )

    return mismatches


def _is_emergency_flatten(exit_payload: dict[str, Any]) -> bool:
    reason = str(exit_payload.get("exit_reason") or exit_payload.get("rule_id") or "").lower()
    return "broker_error_emergency_flatten" in reason


def _has_execution_attempt(events: list[TraceEvent], approve_ts: datetime) -> bool:
    for event in events:
        if event.event != "PAPER_ORDER_ACK":
            continue
        if abs((event.timestamp - approve_ts).total_seconds()) <= 120:
            return True
    return False


def _is_aborted_same_tick_setup(entry: TraceEvent, exit_event: TraceEvent | None) -> bool:
    if exit_event is None:
        return False
    if not _is_emergency_flatten(exit_event.payload):
        return False
    return abs((exit_event.timestamp - entry.timestamp).total_seconds()) <= 5.0


def _is_executable_trade(
    entry: TraceEvent,
    exit_event: TraceEvent | None,
    events: list[TraceEvent],
) -> bool:
    strategy = _strategy_from_event(entry)
    if strategy == "cash_no_trade":
        return False
    if _is_aborted_same_tick_setup(entry, exit_event):
        return False
    return _has_execution_attempt(events, entry.timestamp)


def _estimate_gross_pnl(exit_payload: dict[str, Any]) -> float:
    if _is_emergency_flatten(exit_payload):
        return 0.0
    reason = str(exit_payload.get("exit_reason") or exit_payload.get("rule_id") or "").lower()
    if any(token in reason for token in ("take_profit", "target", "profit")):
        return 150.0
    if any(token in reason for token in ("stop", "loss", "trail")):
        return -120.0
    return 0.0


def _strategy_from_event(event: TraceEvent) -> str:
    payload = event.payload
    strategy = payload.get("strategy_decision")
    if isinstance(strategy, dict):
        return str(strategy.get("strategy", "unknown"))
    if strategy:
        return str(strategy)
    pos = payload.get("open_position")
    if isinstance(pos, dict) and pos.get("strategy"):
        return str(pos["strategy"])
    return "unknown"


def _link_trades(events: list[TraceEvent]) -> list[TradeLifecycle]:
    entries = [event for event in events if event.event in TRADE_ENTRY_EVENTS]
    exits = [event for event in events if event.event in TRADE_EXIT_EVENTS]
    trades: list[TradeLifecycle] = []
    exit_index = 0

    for entry in entries:
        strategy = _strategy_from_event(entry)
        while exit_index < len(exits) and exits[exit_index].timestamp < entry.timestamp:
            exit_index += 1
        exit_event = exits[exit_index] if exit_index < len(exits) else None
        if exit_event is not None:
            exit_index += 1

        executed = _is_executable_trade(entry, exit_event, events)
        if not executed:
            continue

        gross = _estimate_gross_pnl(exit_event.payload) if exit_event else 0.0
        friction = FRICTION_RUPEES
        net = gross - friction
        trades.append(
            TradeLifecycle(
                entry=entry,
                exit=exit_event,
                strategy=strategy,
                gross_pnl=gross,
                net_pnl=net,
                friction=friction,
                is_win=gross > 0,
                executed=True,
            )
        )

    return trades


def _find_zombie_positions(events: list[TraceEvent]) -> list[str]:
    zombies: list[str] = []
    last_open: dict[str, Any] | None = None
    last_tick_ts: datetime | None = None

    for event in events:
        if event.event not in TICK_EVENTS:
            continue
        last_tick_ts = event.timestamp
        open_pos = event.payload.get("open_position")
        if open_pos:
            last_open = open_pos
        elif last_open is not None and event.payload.get("exit_action") is None:
            last_open = None

    if last_open is not None:
        symbol = last_open.get("symbol", "unknown")
        strategy = last_open.get("strategy", "unknown")
        ts = last_tick_ts.isoformat() if last_tick_ts else "unknown"
        zombies.append(
            f"{ts}: open_position still set ({strategy}/{symbol}) — no clean exit before session end"
        )

    open_after_approve = False
    approve_strategy = "unknown"
    approve_ts = ""
    for event in events:
        if event.event == "PAPER_APPROVE":
            open_after_approve = True
            approve_strategy = _strategy_from_event(event)
            approve_ts = event.timestamp.isoformat()
        if event.event == "PAPER_EXIT" and open_after_approve:
            open_after_approve = False
        if event.event in TICK_EVENTS and open_after_approve:
            if event.payload.get("open_position") is None and event.payload.get("final_outcome") == "WOULD_TRADE":
                zombies.append(
                    f"{event.timestamp.isoformat()}: approved {approve_strategy} at {approve_ts} "
                    "but position never bound in subsequent tick"
                )
                open_after_approve = False

    return zombies


def analyze_soak(
    events: list[TraceEvent],
    *,
    session_id: str,
    tick_interval_sec: int,
    expected_hours: float | None,
    gap_multiplier: float,
) -> SoakAnalysis:
    if not events:
        return SoakAnalysis(session_id=session_id, events=[])

    tick_events = [event for event in events if event.event in TICK_EVENTS]
    start = events[0].timestamp
    end = events[-1].timestamp
    duration_sec = max((end - start).total_seconds(), 0.0)

    if expected_hours is not None:
        expected_ticks = max(int((expected_hours * 3600) // tick_interval_sec), 0)
    else:
        expected_ticks = max(int(duration_sec // tick_interval_sec) + 1, 0)

    time_gaps: list[tuple[TraceEvent, TraceEvent, float]] = []
    gap_threshold = tick_interval_sec * gap_multiplier
    for prev, curr in zip(tick_events, tick_events[1:]):
        delta = (curr.timestamp - prev.timestamp).total_seconds()
        if delta > gap_threshold:
            time_gaps.append((prev, curr, delta))

    gatekeeper, critic = _collect_rejections(events)
    trades = _link_trades(events)
    zombies = _find_zombie_positions(events)

    return SoakAnalysis(
        session_id=session_id,
        events=events,
        wall_clock_start=start,
        wall_clock_end=end,
        tick_events=tick_events,
        time_gaps=time_gaps,
        exception_counts=_collect_exceptions(events),
        gatekeeper_rejects=gatekeeper,
        critic_rejects=critic,
        state_mismatches=_detect_state_mismatches(events),
        trades=trades,
        zombie_positions=zombies,
        expected_ticks=expected_ticks,
        actual_ticks=len(tick_events),
    )


def _format_duration(seconds: float) -> str:
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}h {minutes}m {secs}s"


def _performance_metrics(trades: list[TradeLifecycle]) -> dict[str, Any]:
    total = len(trades)
    wins = [trade for trade in trades if trade.is_win]
    losses = [trade for trade in trades if trade.gross_pnl < 0]
    gross_wins = sum(trade.gross_pnl for trade in wins)
    gross_losses = abs(sum(trade.gross_pnl for trade in losses))
    net_total = sum(trade.net_pnl for trade in trades)

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        cumulative += trade.net_pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float("inf")

    return {
        "total_trades": total,
        "win_rate_pct": (100.0 * len(wins) / total) if total else 0.0,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "net_paper_pnl": net_total,
        "friction_per_trade": FRICTION_RUPEES,
    }


def render_report(analysis: SoakAnalysis, *, tick_interval_sec: int) -> str:
    lines: list[str] = []
    duration_sec = 0.0
    if analysis.wall_clock_start and analysis.wall_clock_end:
        duration_sec = (analysis.wall_clock_end - analysis.wall_clock_start).total_seconds()

    lines.append("# A2A Soak Trace Analysis")
    lines.append("")
    lines.append(f"**Session:** `{analysis.session_id}`")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    lines.append("=" * 72)
    lines.append("1. SESSION TIMELINE & CONTINUITY")
    lines.append("=" * 72)
    if analysis.wall_clock_start and analysis.wall_clock_end:
        lines.append(f"Wall-clock start : {analysis.wall_clock_start.isoformat()}")
        lines.append(f"Wall-clock end   : {analysis.wall_clock_end.isoformat()}")
        lines.append(f"Total duration   : {_format_duration(duration_sec)}")
    else:
        lines.append("No trace events found.")
    lines.append(f"Tick interval    : {tick_interval_sec}s ({tick_interval_sec // 60} min)")
    lines.append(f"Ticks executed   : {analysis.actual_ticks}")
    lines.append(f"Ticks expected   : {analysis.expected_ticks}")
    if analysis.expected_ticks:
        delta = analysis.actual_ticks - analysis.expected_ticks
        lines.append(f"Tick delta       : {delta:+d}")
    lines.append("")

    lines.append("--- Time Gaps (> interval threshold) ---")
    if not analysis.time_gaps:
        lines.append("None detected.")
    else:
        for prev, curr, delta in analysis.time_gaps:
            lines.append(
                f"- {prev.timestamp.isoformat()} tick#{prev.payload.get('tick_number', '?')} "
                f"-> {curr.timestamp.isoformat()} tick#{curr.payload.get('tick_number', '?')} "
                f"= {delta:.0f}s gap ({delta / tick_interval_sec:.1f}x interval)"
            )
    lines.append("")

    lines.append("=" * 72)
    lines.append('2. CRACK DETECTOR - ERRORS & REJECTIONS')
    lines.append("=" * 72)
    lines.append("--- Exceptions by type ---")
    if analysis.exception_counts:
        for label, count in analysis.exception_counts.most_common():
            lines.append(f"- {label}: {count}")
    else:
        lines.append("None recorded.")
    lines.append("")

    lines.append("--- Gatekeeper rejections (rule_id) ---")
    if analysis.gatekeeper_rejects:
        for label, count in analysis.gatekeeper_rejects.most_common():
            lines.append(f"- {label}: {count}")
    else:
        lines.append("None recorded.")
    lines.append("")

    lines.append("--- Critic rejections (reason) ---")
    if analysis.critic_rejects:
        for label, count in analysis.critic_rejects.most_common():
            lines.append(f"- {label}: {count}")
    else:
        lines.append("None recorded.")
    lines.append("")

    lines.append("--- State mismatches ---")
    if analysis.state_mismatches:
        for item in analysis.state_mismatches:
            lines.append(f"- {item}")
    else:
        lines.append("None detected.")
    lines.append("")

    perf = _performance_metrics(analysis.trades)
    lines.append("=" * 72)
    lines.append("3. PAPER PERFORMANCE TRACKING")
    lines.append("=" * 72)
    lines.append(f"Total trades     : {perf['total_trades']}")
    lines.append(f"Win rate         : {perf['win_rate_pct']:.1f}%")
    pf = perf["profit_factor"]
    lines.append(f"Profit factor    : {'inf' if pf == float('inf') else f'{pf:.2f}'}")
    lines.append(f"Max drawdown     : INR {perf['max_drawdown']:.2f}")
    lines.append(
        f"Net paper PnL    : INR {perf['net_paper_pnl']:.2f}  "
        f"(includes INR {FRICTION_RUPEES:.0f} friction/trade)"
    )
    lines.append("")

    lines.append("--- Entry -> exit lifecycle (executed only) ---")
    if not analysis.trades:
        lines.append("No executed trades (confirmed PAPER_ORDER_ACK, non-aborted setup).")
    else:
        for index, trade in enumerate(analysis.trades, start=1):
            exit_ts = trade.exit.timestamp.isoformat() if trade.exit else "NO_EXIT"
            exit_reason = ""
            if trade.exit:
                exit_reason = str(
                    trade.exit.payload.get("exit_reason")
                    or trade.exit.payload.get("rule_id")
                    or ""
                )
            lines.append(
                f"{index}. {trade.entry.timestamp.isoformat()} -> {exit_ts} | "
                f"{trade.strategy} | gross INR {trade.gross_pnl:.2f} | "
                f"friction INR {trade.friction:.2f} | net INR {trade.net_pnl:.2f}"
                + (f" | exit={exit_reason}" if exit_reason else "")
            )
    lines.append("")

    lines.append("--- Zombie positions ---")
    if analysis.zombie_positions:
        for item in analysis.zombie_positions:
            lines.append(f"- {item}")
    else:
        lines.append("None detected.")
    lines.append("")

    return "\n".join(lines)


def _resolve_session_id(events: list[TraceEvent], explicit: str | None) -> str:
    if explicit:
        return explicit
    if events:
        return events[0].session_id
    return "unknown"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze A2A soak traces from DynamoDB or a local JSON/JSONL dump.",
    )
    parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help=f"DynamoDB table name (default: {DEFAULT_TABLE}). Ignored when --file is set.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Local JSON array or JSONL trace dump for offline analysis.",
    )
    parser.add_argument(
        "--session-id",
        help="Session partition key (required with --table).",
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region for DynamoDB (default: {DEFAULT_REGION}).",
    )
    parser.add_argument(
        "--tick-interval",
        type=int,
        default=DEFAULT_TICK_INTERVAL_SEC,
        help="Expected tick cadence in seconds (default: 300).",
    )
    parser.add_argument(
        "--expected-hours",
        type=float,
        default=None,
        help="Optional planned soak duration in hours for expected tick count.",
    )
    parser.add_argument(
        "--gap-multiplier",
        type=float,
        default=1.15,
        help="Flag gaps when inter-tick delay exceeds interval × multiplier (default: 1.15).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Markdown report path (default: logs/soak_report_YYYYMMDD_HHMM.md).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.file:
        if not args.file.exists():
            print(f"ERROR: file not found: {args.file}", file=sys.stderr)
            return 2
        events = load_traces_from_file(args.file)
    else:
        if not args.session_id:
            print("ERROR: --session-id is required when querying DynamoDB.", file=sys.stderr)
            return 2
        try:
            events = load_traces_from_dynamodb(
                table_name=args.table,
                session_id=args.session_id,
                region=args.region,
            )
        except (ClientError, BotoCoreError, RuntimeError) as exc:
            print(f"ERROR: DynamoDB query failed: {exc}", file=sys.stderr)
            return 1

    session_id = _resolve_session_id(events, args.session_id)
    analysis = analyze_soak(
        events,
        session_id=session_id,
        tick_interval_sec=args.tick_interval,
        expected_hours=args.expected_hours,
        gap_multiplier=args.gap_multiplier,
    )
    report = render_report(analysis, tick_interval_sec=args.tick_interval)

    print(report)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    output_path = args.output or (REPORT_DIR / f"soak_report_{stamp}.md")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print("")
    print(f"--- Report saved -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
