# High-Level Design Document (HLDD): A2A Trading Engine v4.1

**Status:** v4.1 Deterministic Core + Agentic Periphery. Supersedes v4.0 (LLM-in-the-hot-path), which was deprecated in the June 2026 safety audit.

**Architectural shift:** The intraday 5-minute loop is **pure Python** with no LLM API calls and no Vector DB. LLMs live in AWS Lambda periphery (Agent 0 pre-market scout, Agent 7 parameter tuner) and only write to S3 / DynamoDB — never to the hot path.

---

## 1. The 5 Cross-Cutting Infrastructure Systems

### 1.1 The Agent Context Contract (v4.1)

A single Pydantic `StrictModel` passed chronologically through the entire execution chain. Frozen on `extra="forbid"`. Agents receive a snapshot and return a modified copy via `ctx.update(**fields)`.

```python
class AgentContext(StrictModel):
    session_id: str

    # Static per session
    overnight_context: OvernightContext        # Populated by Agent 0 (Lambda, 08:00 IST)
    opening_regime: OpeningRegime              # Populated by Feature Engine at 09:00 IST

    # Dynamic, accumulated per routing event (pure Python intraday)
    regime_decision: RegimeLabel | None        # Populated by Agent 1
    strategy_decision: StrategyDecision | None # Populated by Agent 2
    critic_decision: CriticDecision | None     # Populated by Agent 3

    # Risk & session state
    open_position: OpenPosition | None         # Populated by broker_recovery at boot
    exit_leg_intents: list | None              # Populated by ExitEngine for Phase-4 executor
    feature_snapshot_price: float | None      # NIFTY LTP at feature capture; Agent 3 stale-quote check
    baseline_initialized: bool = False         # Set True after first successful LTP fetch
    data_degraded: bool = False                # Set True if feature fetch fails
    daily_pnl: float = 0.0
    circuit_status: bool = False
    dte: int                                   # Days to expiry, 0-45
```

**Invariants:**
- `circuit_status=True ⟺ daily_pnl <= -8000` (enforced by `model_validator`).
- `baseline_initialized` must be `True` before Agent 3 will approve any trade.
- `strategy` fields are `StrategyName` StrEnum (4 values) — see §2.4.

### 1.2 Observability Layer

Two distinct writers, scoped by data lifetime:

| Writer | Scope | Backing store | Retention |
|--------|-------|---------------|-----------|
| `BootLogger` | Boot rows (`broker_recovery`, `session_bootstrap`) | DynamoDB `a2a_bootstrap` | 90 days |
| `TraceLogger` (planned v4.2) | Per-tick rows (regime → strategy → critic → gatekeeper → exit) | DynamoDB `a2a_traces` | 5 years (SEBI) |
| `PaperLogger` | `paper_mode` dry-run rows | JSONL files in `logs/paper_soak/` | Append-only per session |

The v4.0 `@trace_agent` decorator (LLM-call-only) is **deprecated**. Future observability uses `@trace_step` for deterministic blocks (token fields optional).

### 1.3 Configuration & Limits

Configuration is two-tiered:

| Tier | File | Mutability |
|------|------|------------|
| **Tunable** | `config/risk_config.json` (loaded via `RiskConfig`) | Agent 7 can propose diffs, clamped by absolute limits |
| **Immutable** | `src/config/absolute_limits.py` (Python module, not JSON) | Hard ceiling; no runtime mutation |

The two-tier design is the safety floor: even a malicious Agent 7 prompt cannot push a parameter past `clamp_to_absolute()`. Every key has **both upper AND lower bound with reason string**.

**Credential storage:**
- Local dev: `.env` file (FYERS_APP_ID, FYERS_ACCESS_TOKEN)
- EC2: AWS SSM Parameter Store (SecureString, KMS-encrypted)
- Loader: `src/config/secrets.py` with `get_fyers_credentials()` — SSM-first with `.env` fallback

### 1.4 Eval Suite

Two eval paths:

| Path | Scope | Trigger |
|------|-------|---------|
| **Deterministic** | Agents 1-3, Gatekeeper, ExitEngine | `pytest tests/` — runs in CI on every commit |
| **Lambda eval** | Agent 0, Agent 7 (LLM schemas only) | Lambda CI pipeline; never on EC2 |

The v4.0 hot-path LLM eval (`src/evals/llm_client.py` invoked from EC2) is **deprecated**. The eval client only runs in Lambda for Agent 0/7 schema validation.

### 1.5 Security Sanitizer

Sits at the Feature Engine → AgentContext boundary. Blocks:
- VIX < 0 or > 100
- AD ratio negative or > 10
- Strike price outside ±20% of underlying LTP
- PCR momentum > 1.0 or < -1.0

`data_degraded=True` if any of these trip — Agent 3 rejects any entry while degraded.

---

## 2. Deterministic Muscle & Risk Engines (Layer 1)

### 2.1 The Feature Engine

Async Python; reads Fyers v3 quotes + option chain; computes:

- `NIFTY_500_AD_Ratio` (advancers / decliners)
- `vix` (India VIX LTP)
- `vix_atr_divergence` (rolling 14-period)
- `expiry_weighted_pcr_momentum = ((current_pcr - pcr_2h_ago) / pcr_2h_ago) * min(dte/10, 1.0)`

**Momentum is the signal, not level.** A PCR of 1.4 with falling momentum is bearish (institutions aggressively writing calls); a PCR of 0.8 with rising momentum is bullish. Thresholds (±0.02 default) are **subject to widening based on paper-soak empirical data** — currently a pending-fixes item.

### 2.2 The Risk Gatekeeper (v4.1)

The absolute final authority before Fyers API execution. Pure Python; no I/O.

**Rules** (in evaluation order):
1. `CASH_NO_TRADE` — strategy_decision is null or `cash_no_trade` → REJECT.
2. `CRITIC_BLOCK` — critic_decision is null or not APPROVE → REJECT.
3. `MAX_LOSS_DAY_BLOCK` — `daily_pnl <= -max_loss_per_day_inr` → REJECT.
4. `MAX_LOTS_BLOCK` — `requested_lots > max_lots_per_trade` → REJECT.
5. `STALE_QUOTE_BLOCK` — critic reason == "stale_quote_abort" → REJECT.
6. Legacy `RiskGatekeeper.evaluate()`: VIX ceiling (18) for RANGE; DTE filter (≤1) for RANGE.
7. Daily circuit breaker (`SESSION_CIRCUIT_BREAKER_PNL = -8000`).
8. Lot scaling: `allowed_lots = 1 + floor(max(0, capital - 600000) / 400000)`.

**Capital fence:** `BASE_CAPITAL_INR = 600_000.0` is a hard constant. Naked options / intraday futures are blocked **at StrategyDecision Pydantic validation** (see §2.4) before they reach the gatekeeper.

### 2.3 The Exit Engine (v4.1 multi-leg)

Per-leg evaluation when `open_position.legs` has 2+ entries:
- Each leg evaluated independently using `per_leg_quotes[leg.symbol]`
- **ANY leg EXIT_MARKET → overall EXIT_MARKET with all legs flattened** (most conservative)
- **ALL legs HOLD → per-leg HOLD intents**
- Blended `theta_capture_pct = max(leg decay)` (worst leg drives exit)
- **Fail-closed**: if `get_bid_ask` raises on any leg, all legs → `EXIT_MARKET` with reason `broker_error_emergency_flatten`

Single-leg path delegates to existing `evaluate()` for backward compat.

### 2.4 StrategyName Enum (Defensive Fence)

```python
class StrategyName(StrEnum):
    IRON_CONDOR = "iron_condor"
    BULL_CALL_SPREAD = "bull_call_spread"
    BEAR_PUT_SPREAD = "bear_put_spread"
    CASH_NO_TRADE = "cash_no_trade"
```

**Excluded (intentionally not in enum, with policy comment in `strategy_selector.py`):**
- `short_strangle`, `short_straddle` — undefined risk, margin > ₹6L per leg
- `nifty_futures_long`, `nifty_futures_short` — catastrophic loss on t3.small account size

A future Cursor session attempting to add any of these **must extend the enum** (compile-time change) **and** update the policy comment. The Pydantic `_coerce_strategy` validator raises `TypeError` for unknown strings, so the engine **cannot** select a non-enum strategy even if the matrix is corrupted.

---

## 3. The Three-Track Roadmap (post v4.1)

See `docs/FUTURE_ENHANCEMENTS.md` for full detail. Summary:

| Track | Goal | Strategy class | Capital | Time horizon |
|-------|------|----------------|---------|--------------|
| **Track 1 (v4.1 ship)** | Lock the deterministic core, paper-soak validation | Defined-risk intraday options spreads | ₹6L | 1-7 DTE |
| **Track 2 (v4.2)** | Add positional weekly spreads on daily/4-hour signals | Defined-risk weekly options spreads | ₹6L | 1-4 weeks |
| **Track 3 (v4.3+)** | Scale to ₹10L+ capital with naked strategies under controlled caps | Naked options with 2× credit stop | ₹10L+ | 1-4 weeks |

Track 1 is in flight (paper soak 4.6 in progress). Tracks 2 and 3 are gated on Track 1 paper validation.

---

## 4. The Compiled-Deterministic Threshold (formerly Agent 7's "Compilation Threshold")

The v4.0 spec described a "compilation" process where an LLM (Agent 7) would generate Python code from observed patterns. **This is removed in v4.1.** LLMs cannot deploy execution code.

What replaces it: Agent 7 (Lambda) proposes **parameter diffs** to `risk_config.json`, clamped by `absolute_limits.py`, gated by Telegram HITL. The compiled-deterministic threshold is now: "can this parameter be safely widened/narrowed within its absolute bounds given observed paper-soak data?" — answered by human review, not LLM code generation.
