# Pending Fixes Checklist

**Last updated:** 2026-06-07 (v4.1 ship readiness)

Tracked from MiniMax review, Safety Audit, and Phase 0-4 close-out.

**Recently closed (June 2026):** tick lock, vector memory purge, Fyers state recovery with multi-leg aggregation, Agent 1/2/3 pure Python, defensive StrategyName enum, paper-mode harness, NSE holiday integration, .cursorrules Prime Directive rewrite, prompt archive, Local Black-Scholes Greeks producer (P0 ΓÇö gates paper soak strike selection), Strike 1 (execution fail-closed + open_position + flatten gating), Strike 2 (gatekeeper MISSING_DATA + per-leg critic delta + frozen AgentContext), Phase 4.2 scaffold (`FyersExecutionPort`, orderbook idempotency, per-tick `sync_position_from_broker`, `--broker` paper flag). Commit: pending.

---

## ≡ƒöÑ Architecture / Safety Audit Fixes

| Status | Item | Notes |
|--------|------|-------|
| Γ£à | OS-Level Loop Lock | FileTickLock (fcntl/msvcrt) wired into SessionPipeline.run_tick() |
| Γ£à | Purge Vector Memory from AgentContext | Removed similar_regimes / SimilarRegimeSnapshot |
| Γ£à | Fyers State Recovery | GET /positions on boot; multi-leg aggregation with summary legs[] |
| Γ£à | Stale Quote Abort | Agent 3 enforces `|live - snapshot| > 10` NIFTY points |
| Γ£à | Defensive StrategyName StrEnum | Pydantic-rejected; naked/futures blocked at validation |
| Γ£à | Tick-lock in production | Cross-process fcntl + DynamoDB fallback path |
| Γ£à | Defensive delete of dead strategy code | short_strangle/short_straddle/nifty_futures removed from registry, gatekeeper, exit_engine |
| Γ£à | .cursorrules Prime Directive | LLM-in-hot-path explicitly forbidden |
| Γ¼£ | Hard Stop Enforcement (broker-side) | 2.5x ATR stop as native Fyers Bracket/Cover order (Phase 4.2+) |
| Γ£à | Idempotent order submit | `FyersExecutionPort`: orderbook pre-check by `orderTag`; 504 ΓåÆ re-query before single retry |
| Γ£à | `FyersExecutionPort` | `src/execution/fyers_port.py`; wired via `paper_mode --broker` |
| Γ£à | Per-tick broker position sync | `sync_position_from_broker()` in `broker_recovery.py`; `broker_sync=True` on pipeline |
| Γ¼£ | Daily Fyers token rotation Lambda | Token expires 03:30 IST; needs Lambda cron (deferred per PM) |

---

## Gatekeeper (src/risk/gatekeeper.py)

| Status | Item | Notes |
|--------|------|-------|
| Γ£à | Dynamic lot scaling (HLDD ┬º2.2) | `compute_allowed_lots()`, reject if `requested_lots > allowed` |
| Γ£à | Slippage surface (Γé╣150 futures / Γé╣40 options) | `expected_round_trip_cost` on every GatekeeperDecision |
| Γ£à | Boundary tests (VIX, DTE, PnL, lots, slippage) | `tests/test_gatekeeper.py` |
| Γ£à | GatekeeperRule StrEnum for rule_id | Typos become import-time errors |
| Γ£à | `evaluate_from_context()` wired | CASH_NO_TRADE, CRITIC_BLOCK, STALE_QUOTE_BLOCK, UNDEFINED_RISK_BLOCK (now redundant), MAX_LOSS_DAY_BLOCK, MAX_LOTS_BLOCK |
| Γ£à | `absolute_limits.py` with upper AND lower bounds | Every key has `tuple[lower, upper, reason]` |
| Γ£à | Naked-strategy code removed | `NAKED_SHORT_STRATEGIES` frozenset deleted; comment "blocked at validation" |
| Γ¼£ | Case-normalize payload keys at boundary | `_read_float` only tries known aliases (deferred; not a current bug) |

---

## Feature Engine (src/features/)

| Status | Item | Notes |
|--------|------|-------|
| Γ£à | PCR momentum: None = no history, 0.0 = flat | `compute_expiry_weighted_pcr_momentum` returns `float \| None` |
| Γ£à | FeatureEngineError.code enum (TIMEOUT, MARKET_DATA, ...) | Standardize caller handling |
| Γ£à | NSE holiday integration | `_weekly_expiry_timestamps` skips weekends + holidays |
| Γ¼£ | **PCR momentum threshold widening** | Current `┬▒0.02` is statistical noise; widen to `┬▒0.10-0.15` based on paper soak data |
| Γ¼£ | **VIX/ATR divergence threshold calibration** | Default 0.10; tune from paper soak |
| Γ£à | Fyers request timeout 60s in paper_mode | `FyersMarketDataProvider(request_timeout_sec=60)` |
| Γ¼£ | Assert `save_pcr_snapshot` called in feature engine test | Implicit today |

---

## Exit Engine (src/risk/exit_engine.py)

| Status | Item | Notes |
|--------|------|-------|
| Γ£à | Core rules (ATR stop, regime flip, theta, VIX spike) | 10 tests passing |
| Γ£à | **Multi-leg aggregation** | Per-leg evaluation, ANY-EXIT aggregation, fail-closed broker errors |
| Γ£à | `LegActionIntent` for Phase-4 executor | Symbol + action + leg_id |
| Γ£à | `build_emergency_flatten_decision` | `broker_error_emergency_flatten` reason |
| Γ¼£ | ExitRule StrEnum for rule_id | Same pattern as GatekeeperRule (low priority) |
| Γ£à | Wire into live position loop | Flatten via `execution_port`; fill reconcile on broker_sync |

---

## Strike & Expiry Selection (src/agents/symbol_resolver.py)

| Status | Item | Notes |
|--------|------|-------|
| Γ£à | 30-delta targeting for short strikes | iron_condor / strangle / straddle |
| Γ£à | 50/20-delta for vertical spreads | bull_call_spread / bear_put_spread |
| Γ£à | 200-pt wings for iron condor | Config-driven via `wing_width_points` |
| Γ£à | DTE band 1-7 | `min_dte_for_entry`, `max_dte_for_entry` |
| Γ£à | NSE holiday-aware expiry | Skips Thursday holidays |
| Γ£à | Strike errors fail-closed | `StrikeSelectionError` ΓåÆ critic REJECT |
| Γ¼£ | Wider delta tolerance for low-VIX days | Current `┬▒0.10`; consider `┬▒0.15` when VIX < 12 |
| Γ¼£ | Strike selection for cash-settled BANKNIFTY | Currently NIFTY-only |

---

## Observability (planned for v4.2)

| Status | Item | Notes |
|--------|------|-------|
| Γ£à | `BootLogger` for bootstrap events | `a2a_bootstrap` DDB table |
| Γ£à | `PaperLogger` for dry-run rows | JSONL files |
| ≡ƒƒí | `TraceLogger` for per-tick rows | JSONL default (`tick_trace.py`); DDB writer optional |
| Γ¼£ | S3 Object Lock for audit immutability | DynamoDB is mutable; SEBI may require immutable audit |
| Γ¼£ | CloudWatch alarm on heartbeat absence | Detect dead EC2 within 6 min |
| Γ¼£ | Telegram alerter for halt events | No operator push today |

---

## Ops / Infra

| Status | Item | Notes |
|--------|------|-------|
| Γ¼£ | **4h paper soak against live Fyers** | In progress; required before live |
| Γ¼£ | **Capital deployment plan** | Γé╣2.5L Liquid BeES + Γé╣3.5L trading |
| Γ£à | EC2 memory watchdog (psutil > 85% halt) | `runtime_guards.check_memory_usage` in pipeline + paper_mode |
| Γ¼£ | EC2 IAM role with SSM:GetParameter | Step 4 of the SSM migration |
| Γ¼£ | Purge `~/.claude` from git history | Requires `git filter-repo` + force push to main |
| Γ£à | Rotate leaked Anthropic/opencode API key | User rotated 2026-06-06 |
| Γ£à | Untrack `~/.claude`, extend `.gitignore` | Commit d32e7f5 |

---

## Three-Track Roadmap (new section)

### Track 1 ΓÇö v4.1 Ship (in flight)

**Goal:** Lock deterministic core, paper-soak validation, deploy live with 1-lot constraint.

| Status | Item |
|--------|------|
| Γ£à | Deterministic core (Agents 1-3, Gatekeeper, ExitEngine, Recovery) |
| Γ£à | Paper-mode harness with 5-min cadence |
| ΓÅ│ | 4h paper soak against live Fyers |
| Γ¼£ | Capital deployment Γé╣6L |
| Γ¼£ | Live with 1-lot constraint |
| Γ¼£ | 20+ hours of paper data over multiple trading days |

**Exit criteria:** `PAPER_APPROVE` count > 0, broker error rate < 1%, no engine crashes.

### Track 2 ΓÇö v4.2 Positional Weekly Spreads (gated on Track 1)

**Goal:** Sibling pipeline for daily/4-hour positional signals, defined-risk spreads.

| Status | Item |
|--------|------|
| Γ¼£ | `StrategyClass.POSITIONAL` enum on AgentContext |
| Γ¼£ | `positional_run_chain` (mirror of `_run_entry_chain` with 4-hour cadence) |
| Γ¼£ | Weekly circuit-breaker cap (-Γé╣15,000) |
| Γ¼£ | New `StrategyName` entries for weekly verticals |
| Γ¼£ | Separate paper-soak harness for positional cadence |

**Gating:** Track 1 must demonstrate positive expectancy in paper soak first.

### Track 3 ΓÇö v4.3+ Scaled (gated on Track 2)

**Goal:** Scale to Γé╣10L+ with controlled naked-strategy exposure.

| Status | Item |
|--------|------|
| Γ¼£ | 2├ù credit stop-loss rules for naked strategies |
| Γ¼£ | Sub-portfolio caps (separate Γé╣25k for naked) |
| Γ¼£ | `short_strangle` re-enables (with stop) |
| Γ¼£ | NIFTY futures positional (1 lot, 2% stop, weekly cap) |
| Γ¼£ | Daily Fyers token rotation Lambda |
| Γ¼£ | TraceLogger to DynamoDB for SEBI audit |

**Gating:** Track 2 must demonstrate positive expectancy over 50+ paper trades.

---

## Recently Deferrals (Architectural, Not Bugs)

| Item | Why deferred |
|------|--------------|
| Daily Fyers token rotation | Manual reauth for v4.1; Lambda is a v4.1.1 add-on |
| `case-normalize payload keys` | No current bug; alias list works |
| `ExitRule` StrEnum | Cosmetic; rule_id as string is fine |
| `assert save_pcr_snapshot` test | Implicit today; not a current bug |

---

## Risks Blocked by Track 1 Completion

1. Live order submission without paper validation (capital risk: P0)
2. Capital deployment without proven edge (capital risk: P0)
3. Multi-leg execution without idempotency (double-fill risk: P0)
4. Naked-strategy re-enablement without stop-loss rule (capital risk: P0)

The above are the four risks that paper soak must close before any of the tracks can advance.
