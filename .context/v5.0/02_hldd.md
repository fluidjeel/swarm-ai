# HLDD: A2A Trading Engine v5.0

> **Target design.** Running code = `../v4.1/02_hldd.md` (REST) until Epic 3 ships.

## 1. Cross-Cutting Infrastructure

### 1.1 Agent Context Contract

Single Pydantic model passed chronologically through the chain. Agents read snapshots and return modified copies.

```python
@dataclass
class AgentContext:
    session_id: str
    overnight_context: Dict
    opening_regime: Dict
    live_option_chain: Dict        # In-memory async order book (v5)
    regime_decision: str
    strategy_decision: Dict
    critic_decision: Dict
    open_position: Optional[Dict]  # Broker GET /positions on boot
    calculated_lots: int           # Fractional sizing output
    expected_friction_cost: float  # e.g. 4 legs × ₹40 = ₹160
    feature_snapshot_price: float
    daily_pnl: float
    circuit_status: bool
    dte: int
```

### 1.2 Observability

`@trace_agent` → DynamoDB `A2A_Traces`. v5 adds: `entry_premium`, `exit_cost`, `net_pnl_after_friction`, `friction_inr`.

### 1.3 Eval Suite & Synthetic Backtesting

Offline WebSocket replay harness for premium-based stops and 0-DTE theta capture before live deployment.

### 1.4 Security & Synchronization

- Mutex on every evaluation tick (fcntl / DynamoDB lock).
- Fail-closed when `data_degraded=True`.
- WebSocket reconnect with exponential backoff (Epic 3).

## 2. Deterministic Engines (EC2 Daemon)

### 2.1 Feature Engine (WebSocket)

- Continuous asyncio loop on Fyers v3 WebSocket.
- Streams: NIFTY 500 A/D, VIX–ATR divergence, expiry-weighted PCR momentum.
- Greeks: local Black-Scholes (`src/features/greeks_engine.py`) on streaming quotes.

### 2.2 Risk Gatekeeper (Governor)

| Rule | Behavior |
|------|----------|
| Fractional sizing | `lots = floor((capital × risk_pct) / max_loss_per_lot)`; cap at freeze qty |
| IV percentile gate | Block premium selling if IV &lt; 30th percentile |
| Friction EV block | Reject if `max_profit < 2 × expected_friction_cost` |
| Stale quote | Abort if underlying moved &gt; 10 NIFTY points |
| Session breaker | Halt new entries at −₹8,000 daily PnL |
| Undefined risk | Naked shorts blocked unless Epic 5 guardrails + enum extension |

**Friction (shipped):** ₹40 per leg round-trip via `src/risk/friction.py`.

### 2.3 Exit Engine (Premium-Based)

| Strategy type | Exit logic |
|---------------|------------|
| Debit spreads | TP at 75% max; stop when spread value −50% from entry |
| Credit spreads / condors | Stop when cost-to-close = 1.5–2.0× entry credit |
| 0-DTE harvester | Per-leg premium trails (e.g. cut CE at +30%, let PE decay) |

Broker bracket orders remain disaster-recovery layer (Phase 4.2+).

## 3. Agent 7 — Expectancy Controller

Post-market Lambda:

- 14-day rolling Sharpe and max drawdown.
- If DD &gt; 8%: write throttled `risk_config.json` (1% risk, disable 0-DTE matrix).
- Telegram HITL required to restore 3% leverage.
- Never deploys Python — config only, clamped by `absolute_limits.json`.

## 4. Prime Directives (non-negotiable)

1. Broker GET /positions is execution source of truth.
2. No LLM in intraday hot path.
3. AgentContext is the only state bus.
4. STALE_QUOTE_POINTS = 10 NIFTY points.
5. Agent 7 config-only + HITL.

See also: `PIVOT_DECISION.md` for v4.1 fence collisions.
