High-Level Design Document (HLDD): A2A Trading Engine v4.0

1. The 5 Cross-Cutting Infrastructure Systems

1.1 The Agent Context Contract

A single Python dataclass passed chronologically through the entire execution chain. Agents read from it and append to it; they do not fetch independent data.

from dataclasses import dataclass
from typing import List, Dict, Optional

@dataclass
class AgentContext:
    session_id: str
    
    # Static per session
    overnight_context: Dict        # Populated by Agent 0 (Scout)
    opening_regime: Dict           # Populated by Feature Engine at 8:45 AM
    
    # Dynamic, accumulated per event
    regime_decision: str           # Populated by Agent 1
    similar_regimes: List[Dict]    # Populated by Vector Store query
    strategy_decision: Dict        # Populated by Agent 2 (Strategy, Signals)
    critic_decision: Dict          # Populated by Agent 3 (Status, Reason)
    
    # Session state
    open_position: Optional[Dict]
    daily_pnl: float
    circuit_status: bool
    dte: int


1.2 Observability Layer (Telemetry)

A Python decorator @trace_agent wraps every LLM invocation. It forces structured logging to AWS DynamoDB to enable Agent 6 (Analyzer) and Agent 7 (Compiler).

DynamoDB Schema:

Partition Key: session_id (String)

Sort Key: timestamp (Number)

Attributes: agent_name, prompt_version, input_tokens, output_tokens, latency_ms, input_hash, output_json, validation_passed, downstream_action.

1.3 Prompt Registry

Prompts are completely decoupled from application code. They reside in Amazon S3 (s3://a2a-prompts/). Every prompt is version-controlled (e.g., regime_classifier/v1.md, regime_classifier/v2.md). Modifying agent behavior requires deploying a new version, ensuring historical trade logs perfectly map to the prompt that generated them.

1.4 Eval Suite (Offline Harness)

An offline testing environment running locally. Before deploying v2 of any prompt to S3, it must pass against a fixture library (20+ historical JSON snapshots of market extremes).

Schema Eval: Does the LLM return the exact Pydantic format?

Behavioral Eval: Does the Critic correctly veto an engineered bad trade?

HITL Eval: Does the system trigger Telegram on an engineered UNCERTAIN payload?

1.5 Security Sanitizer

Middleware sitting between the TrueData websocket/Feature Engine and the LLM.

Prevents data anomalies (e.g., TrueData sending a VIX of -100 or 9999) from reaching the LLM and causing hallucination.

Truncates any text fields to prevent prompt-injection style disruption.

2. Deterministic Muscle & Risk Engines (Layer 1)

2.1 The Feature Engine

Execution: Async Python script processing TrueData websockets.

Outputs:

NIFTY_500_AD_Ratio (Advancing / Declining).

VIX_ATR_Divergence.

Expiry_Weighted_PCR_Momentum = ((Current PCR - PCR 2h ago) / PCR 2h ago) * min(DTE / 10, 1.0).

2.2 The Risk Gatekeeper (Circuit Breakers)

The absolute final authority before Fyers API execution.

Daily Circuit Breaker: IF daily_realized_loss <= -8000 THEN halt_all_new_entries().

Dynamic Lot Scaling: allowed_lots = 1 + floor(max(0, current_capital - 600000) / 400000).

VIX Ceiling: IF strategy == RANGE AND VIX > 18 THEN reject().

Gamma/DTE Filter: IF strategy == RANGE AND DTE <= 1 THEN reject().

Slippage Enforcement: Hardcodes ₹150 round-trip deduction for Futures and ₹40 round-trip for Options in all internal expectancy math.

2.3 The Exit Engine

Directional (Futures): Recalculates and enforces a 2x 14-period ATR trailing stop every 5 minutes. Triggers market exit on Regime Flip (e.g., A/D ratio drops below 1.0).

Range (Credit Spreads): Executes hard take-profit at 60% theta capture relative to max premium. Flat exits if VIX spikes >10% intraday.

3. The Compilation Threshold (Agent 7 Rules)

Agent 7 (The Compiler) may only propose promoting a Layer 2 Agentic pattern to Layer 1 Deterministic code if the DynamoDB traces show:

Frequency: ≥ 10 occurrences of the exact clustered context.

Win Rate: > 65% profitability.

Expectancy: Average Win > 1.5x Average Loss (post-slippage).

Shadow Mode: All compiled rules MUST run in Shadow Mode (logging only, no execution) alongside the active LLM for 14 calendar days. Promotion occurs only if Shadow PnL is within ±15% of Agent PnL.