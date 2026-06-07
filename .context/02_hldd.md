High-Level Design Document (HLDD): A2A Trading Engine v4.1 (Deterministic Core)

1. The 5 Cross-Cutting Infrastructure Systems

1.1 The Agent Context Contract

A single Python dataclass passed chronologically through the entire execution chain. Agents (both Python state-machines and peripheral LLMs) read from it and append to it; they do not fetch independent data.

from dataclasses import dataclass
from typing import Dict, Optional

@dataclass
class AgentContext:
    session_id: str
    
    # Static per session
    overnight_context: Dict        # Populated by Agent 0 (Scout - LLM Lambda)
    opening_regime: Dict           # Populated by Feature Engine at 8:45 AM
    
    # Dynamic, accumulated per event (Pure Python Intraday)
    regime_decision: str           # Populated by Agent 1 (Math Thresholds)
    strategy_decision: Dict        # Populated by Agent 2 (Lookup Matrix)
    critic_decision: Dict          # Populated by Agent 3 (Greeks/Stale Quote Check)
    
    # Risk & Session state
    open_position: Optional[Dict]  # Verified via Fyers GET /positions on boot
    feature_snapshot_price: float  # Used for Stale Quote aborts
    daily_pnl: float
    circuit_status: bool
    dte: int


1.2 Observability Layer (Telemetry)

A Python decorator @trace_agent wraps execution blocks. It forces structured logging to AWS DynamoDB to enable Agent 6 (Analyzer) and Agent 7 (Parameter Tuner).

DynamoDB Schema:

Partition Key: session_id (String)

Sort Key: timestamp (Number)

Attributes: agent_name, input_hash, output_json, validation_passed, downstream_action, latency_ms. (Token counts only logged for Agent 0/6/7 Lambda invocations).

1.3 Prompt Registry (Periphery Only)

Prompts are completely decoupled from application code and reside in Amazon S3 (s3://a2a-prompts/). No LLM prompts are used for intraday trading. Prompts are strictly reserved for:

agent_0_macro/v1.md: For the morning sentiment scout.

agent_7_reflection/v1.md: For the nightly post-mortem and parameter tuning.

1.4 Eval Suite (Offline Harness)

An offline testing environment running locally.

Deterministic Eval: Runs 50+ historical JSON snapshots of market extremes through Agents 1, 2, and 3 to verify the Python math routes to the correct strategy and triggers staleness/Greeks aborts correctly.

LLM Eval: Verifies Agent 0 and Agent 7 return exact Pydantic formats.

1.5 Security & Synchronization

OS-Level Process Lock: The SessionPipeline uses an OS-level file lock (fcntl) or DynamoDB conditional write. If a 5-minute loop exceeds 5 minutes, the subsequent cron trigger is blocked, preventing concurrent ticks and double-ordering.

Data Sanitizer: Prevents data anomalies (e.g., TrueData sending a VIX of -100) from breaching the deterministic engine.

2. Deterministic Muscle & Risk Engines (Layer 1 - EC2)

2.1 The Feature Engine

Execution: Async Python script processing broker REST API / websockets.

Outputs: NIFTY_500_AD_Ratio, VIX_ATR_Divergence, Expiry_Weighted_PCR_Momentum.

Data sources — Greeks: Greeks are computed locally via Black-Scholes in
src/features/greeks_engine.py. Inputs: Fyers index LTP (get_index_ltp), option LTP
+ bid/ask + OI from optionchain, DTE from chain expiry timestamp, risk-free rate
and dividend yield from RiskConfig. IV solved by Newton-Raphson with bisection
fallback. Confidence derived from spread %, OI (>=100), and IV convergence speed.

2.2 The Risk Gatekeeper (Circuit Breakers)

The absolute final authority before Fyers API execution. Implemented with pydantic extra="forbid" to ensure strict immutability.

Stale Quote Abort: IF current_underlying - feature_snapshot_price > 10 THEN abort().

Daily Circuit Breaker: IF daily_realized_loss <= -8000 THEN halt_all_new_entries().

Dynamic Lot Scaling: allowed_lots = 1 + floor(max(0, current_capital - 600000) / 400000).

Undefined Risk Block: Hard-rejects any naked short options.

2.3 The Exit Engine & Execution

State Recovery: On boot, the engine queries Fyers GET /positions. DynamoDB/S3 is never trusted for open position state.

Hard Stop (Broker Level): A strict 2.5x ATR Cover/Bracket order is placed instantly at execution to protect against EC2 failure.

Soft Stop (Memory Level): Enforces a dynamic trailing stop. Triggers market exit on Regime Flip (e.g., A/D ratio drops below 1.0).

3. Autonomous Parameter Tuning (Agent 7 - Lambda)

Agent 7 (The Tuner) runs via Lambda on weekends. It is strictly forbidden from altering Python execution logic. It may only propose changes to risk_config.json in S3 (e.g., atr_stop_multiplier: 2.0 -> 2.2).

Immutable Guardrails (Python-Enforced):

Agent 7 cannot exceed hardcoded limits in absolute_limits.json (e.g., MAX_STOP_ATR = 3.0).

HITL Requirement: All risk_config.json mutations require a human Telegram "Approve" click before 9:00 AM Monday. If unapproved or if Lambda fails, the system defaults to the previous configuration.