# Pending Fixes Checklist

**Last updated:** 2026-06-07 (v4.1 ship readiness)

Tracked from MiniMax review, Safety Audit, and Phase 0-4 close-out.

**Recently closed (June 2026):** tick lock, vector memory purge, Fyers state recovery with multi-leg aggregation, Agent 1/2/3 pure Python, defensive StrategyName enum, paper-mode harness, NSE holiday integration, .cursorrules Prime Directive rewrite, prompt archive, Local Black-Scholes Greeks producer (P0 — gates paper soak strike selection): Fyers greeks field no longer required; delta/gamma computed deterministically from chain quotes. Commit: pending.

---

## 🔥 Architecture / Safety Audit Fixes

| Status | Item | Notes |
|--------|------|-------|
| ✅ | OS-Level Loop Lock | FileTickLock (fcntl/msvcrt) wired into SessionPipeline.run_tick() |
| ✅ | Purge Vector Memory from AgentContext | Removed similar_regimes / SimilarRegimeSnapshot |
| ✅ | Fyers State Recovery | GET /positions on boot; multi-leg aggregation with summary legs[] |
| ✅ | Stale Quote Abort | Agent 3 enforces `|live - snapshot| > 10` NIFTY points |
| ✅ | Defensive StrategyName StrEnum | Pydantic-rejected; naked/futures blocked at validation |
| ✅ | Tick-lock in production | Cross-process fcntl + DynamoDB fallback path |
| ✅ | Defensive delete of dead strategy code | short_strangle/short_straddle/nifty_futures removed from registry, gatekeeper, exit_engine |
| ✅ | .cursorrules Prime Directive | LLM-in-hot-path explicitly forbidden |
| ⬜ | Hard Stop Enforcement (broker-side) | 2.5x ATR stop as native Fyers Bracket/Cover order (Phase 4.2) |
| ⬜ | Idempotent Retries | Fyers 504 → query orderbook before retry (Phase 4.2) |
| ⬜ | Daily Fyers token rotation Lambda | Token expires 03:30 IST; needs Lambda cron (deferred per PM) |

---

## Gatekeeper (src/risk/gatekeeper.py)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | Dynamic lot scaling (HLDD §2.2) | `compute_allowed_lots()`, reject if `requested_lots > allowed` |
| ✅ | Slippage surface (₹150 futures / ₹40 options) | `expected_round_trip_cost` on every GatekeeperDecision |
| ✅ | Boundary tests (VIX, DTE, PnL, lots, slippage) | `tests/test_gatekeeper.py` |
| ✅ | GatekeeperRule StrEnum for rule_id | Typos become import-time errors |
| ✅ | `evaluate_from_context()` wired | CASH_NO_TRADE, CRITIC_BLOCK, STALE_QUOTE_BLOCK, UNDEFINED_RISK_BLOCK (now redundant), MAX_LOSS_DAY_BLOCK, MAX_LOTS_BLOCK |
| ✅ | `absolute_limits.py` with upper AND lower bounds | Every key has `tuple[lower, upper, reason]` |
| ✅ | Naked-strategy code removed | `NAKED_SHORT_STRATEGIES` frozenset deleted; comment "blocked at validation" |
| ⬜ | Case-normalize payload keys at boundary | `_read_float` only tries known aliases (deferred; not a current bug) |

---

## Feature Engine (src/features/)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | PCR momentum: None = no history, 0.0 = flat | `compute_expiry_weighted_pcr_momentum` returns `float \| None` |
| ✅ | FeatureEngineError.code enum (TIMEOUT, MARKET_DATA, ...) | Standardize caller handling |
| ✅ | NSE holiday integration | `_weekly_expiry_timestamps` skips weekends + holidays |
| ⬜ | **PCR momentum threshold widening** | Current `±0.02` is statistical noise; widen to `±0.10-0.15` based on paper soak data |
| ⬜ | **VIX/ATR divergence threshold calibration** | Default 0.10; tune from paper soak |
| ⬜ | Increase default timeout (30s → 60s) or document | Opening-hour Fyers latency |
| ⬜ | Assert `save_pcr_snapshot` called in feature engine test | Implicit today |

---

## Exit Engine (src/risk/exit_engine.py)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | Core rules (ATR stop, regime flip, theta, VIX spike) | 10 tests passing |
| ✅ | **Multi-leg aggregation** | Per-leg evaluation, ANY-EXIT aggregation, fail-closed broker errors |
| ✅ | `LegActionIntent` for Phase-4 executor | Symbol + action + leg_id |
| ✅ | `build_emergency_flatten_decision` | `broker_error_emergency_flatten` reason |
| ⬜ | ExitRule StrEnum for rule_id | Same pattern as GatekeeperRule (low priority) |
| ⬜ | Wire into live position loop | Phase 4.1 |

---

## Strike & Expiry Selection (src/agents/symbol_resolver.py)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | 30-delta targeting for short strikes | iron_condor / strangle / straddle |
| ✅ | 50/20-delta for vertical spreads | bull_call_spread / bear_put_spread |
| ✅ | 200-pt wings for iron condor | Config-driven via `wing_width_points` |
| ✅ | DTE band 1-7 | `min_dte_for_entry`, `max_dte_for_entry` |
| ✅ | NSE holiday-aware expiry | Skips Thursday holidays |
| ✅ | Strike errors fail-closed | `StrikeSelectionError` → critic REJECT |
| ⬜ | Wider delta tolerance for low-VIX days | Current `±0.10`; consider `±0.15` when VIX < 12 |
| ⬜ | Strike selection for cash-settled BANKNIFTY | Currently NIFTY-only |

---

## Observability (planned for v4.2)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | `BootLogger` for bootstrap events | `a2a_bootstrap` DDB table |
| ✅ | `PaperLogger` for dry-run rows | JSONL files |
| ⬜ | `TraceLogger` for per-tick rows | `a2a_traces` DDB; 5-year retention for SEBI |
| ⬜ | S3 Object Lock for audit immutability | DynamoDB is mutable; SEBI may require immutable audit |
| ⬜ | CloudWatch alarm on heartbeat absence | Detect dead EC2 within 6 min |
| ⬜ | Telegram alerter for halt events | No operator push today |

---

## Ops / Infra

| Status | Item | Notes |
|--------|------|-------|
| ⬜ | **4h paper soak against live Fyers** | In progress; required before live |
| ⬜ | **Capital deployment plan** | ₹2.5L Liquid BeES + ₹3.5L trading |
| ⬜ | EC2 memory watchdog (psutil > 85% halt) | Defer until paper soak validates thresholds |
| ⬜ | EC2 IAM role with SSM:GetParameter | Step 4 of the SSM migration |
| ⬜ | Purge `~/.claude` from git history | Requires `git filter-repo` + force push to main |
| ✅ | Rotate leaked Anthropic/opencode API key | User rotated 2026-06-06 |
| ✅ | Untrack `~/.claude`, extend `.gitignore` | Commit d32e7f5 |

---

## Three-Track Roadmap (new section)

### Track 1 — v4.1 Ship (in flight)

**Goal:** Lock deterministic core, paper-soak validation, deploy live with 1-lot constraint.

| Status | Item |
|--------|------|
| ✅ | Deterministic core (Agents 1-3, Gatekeeper, ExitEngine, Recovery) |
| ✅ | Paper-mode harness with 5-min cadence |
| ⏳ | 4h paper soak against live Fyers |
| ⬜ | Capital deployment ₹6L |
| ⬜ | Live with 1-lot constraint |
| ⬜ | 20+ hours of paper data over multiple trading days |

**Exit criteria:** `PAPER_APPROVE` count > 0, broker error rate < 1%, no engine crashes.

### Track 2 — v4.2 Positional Weekly Spreads (gated on Track 1)

**Goal:** Sibling pipeline for daily/4-hour positional signals, defined-risk spreads.

| Status | Item |
|--------|------|
| ⬜ | `StrategyClass.POSITIONAL` enum on AgentContext |
| ⬜ | `positional_run_chain` (mirror of `_run_entry_chain` with 4-hour cadence) |
| ⬜ | Weekly circuit-breaker cap (-₹15,000) |
| ⬜ | New `StrategyName` entries for weekly verticals |
| ⬜ | Separate paper-soak harness for positional cadence |

**Gating:** Track 1 must demonstrate positive expectancy in paper soak first.

### Track 3 — v4.3+ Scaled (gated on Track 2)

**Goal:** Scale to ₹10L+ with controlled naked-strategy exposure.

| Status | Item |
|--------|------|
| ⬜ | 2× credit stop-loss rules for naked strategies |
| ⬜ | Sub-portfolio caps (separate ₹25k for naked) |
| ⬜ | `short_strangle` re-enables (with stop) |
| ⬜ | NIFTY futures positional (1 lot, 2% stop, weekly cap) |
| ⬜ | Daily Fyers token rotation Lambda |
| ⬜ | TraceLogger to DynamoDB for SEBI audit |

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
