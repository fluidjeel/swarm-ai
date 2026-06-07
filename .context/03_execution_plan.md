# Execution Plan & Roadmap: A2A Trading Engine v4.1

**Status:** v4.1 Deterministic Core complete (218 tests, 60 subtests). v4.0 LLM-in-the-hot-path architecture deprecated.

This document tracks which phases are **done**, **in flight**, **gated**, and **future** for the v4.1 pivot and beyond. The v4.0 phases (1-5) are mapped to v4.1 status below.

---

## Phase 0 — Safety Pivot (June 2026)

**Objective:** Remove the dangerous v4.0 hot-path LLM/Vector DB architecture.

| Status | Item | Notes |
|--------|------|-------|
| ✅ | Purge `similar_regimes` from `AgentContext` | Field + validator + exports removed |
| ✅ | `STALE_QUOTE_POINTS = 10.0` constant | NIFTY points (not option premium) |
| ✅ | Add `feature_snapshot_price`, `data_degraded`, `baseline_initialized` | `AgentContext` extension |
| ✅ | Tick lock (`FileTickLock` with `fcntl` / `msvcrt`) | Cross-process; blocks overlapping 5-min loops |
| ✅ | `bootstrap_session()` once per day | Refuses outside INTRADAY/SQUARE_OFF |
| ✅ | `.cursorrules` Prime Directive rewrite | v4.1 wording; LLM-in-hot-path explicitly forbidden |
| ✅ | Archive deprecated LLM prompts | `prompts/archive/` with deprecation README |
| ✅ | Defensive StrategyName StrEnum | 4 values; Pydantic rejects unknown strings |

**DoD:** ✅ All 9 items shipped. The hot path has zero LLM imports (verified by `grep`).

---

## Phase 1 — Infrastructure & Recovery (✅ Done)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | Fyers `get_positions()` provider method | Fail-closed on 5xx, auth, untagged |
| ✅ | `broker_recovery.rebuild_from_fyers()` | Aggregates multi-leg into summary position |
| ✅ | Multi-leg inference for untagged legs | `_infer_strategy_from_legs()` (iron_condor, strangle, straddle) |
| ✅ | `OrphanLegError`, `PartialFillError`, `UntaggedPositionError` | Distinct error classes for distinct failure modes |
| ✅ | `session_clock.py` with NSE holidays | Hardcoded 2026 calendar |
| ✅ | `baseline_initialized` on first LTP | From broker LTP, not next bar close |

**DoD:** ✅ 100+ tests pass. Broker is source of truth on boot. A 4-leg iron condor reconstructs as 1 summary with 4 legs.

---

## Phase 2 — Feature & Risk Engines (✅ Done)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | Feature Engine with PCR momentum, AD ratio, VIX/ATR divergence | Async Python; FeatureEngineError enum |
| ✅ | Risk Gatekeeper with 8 rules | Pure Python, no I/O |
| ✅ | Exit Engine with multi-leg aggregation | Per-leg evaluation, ANY-EXIT aggregation |
| ✅ | Absolute limits + risk config | Two-tier config, clamping on load |
| ✅ | Stale-quote abort | `|live_ltp - snapshot| > 10` NIFTY points |
| ✅ | Position sizing | `compute_allowed_lots()` based on capital |

**DoD:** ✅ Gatekeeper rejects engineered bad inputs 100% of the time. Exit engine handles 4-leg iron condor flatten correctly.

---

## Phase 3 — Intraday Deterministic Core (✅ Done)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | Agent 1 (Regime) pure Python thresholds | `src/agents/regime_classifier.py` |
| ✅ | Agent 2 (Strategy) lookup matrix | `src/agents/strategy_selector.py` |
| ✅ | Agent 3 (Critic) live Greeks + spread + stale-quote | `src/agents/pre_trade_critic.py` |
| ✅ | Strike + expiry selection | `src/agents/symbol_resolver.py` with 30-delta targeting |
| ✅ | SessionPipeline wiring | `regime → strategy → strike → critic → gatekeeper` |
| ✅ | Perf budget | 1→2 chain <50ms; strike selection <5ms |

**DoD:** ✅ 218 tests pass. End-to-end tick: bootstrap → regime → strategy → strike → critic → gatekeeper. Net delta/gamma across legs drives Agent 3 bounds.

---

## Phase 4 — Execution (In Flight)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | Paper-mode soak harness | `src/orchestration/paper_mode.py` with 5-min cadence |
| ✅ | `dry_run` flag in SessionPipeline | Logs `PAPER_APPROVE` / `PAPER_EXIT`, no execution |
| ✅ | Paper runbook | `docs/PAPER_MODE_RUNBOOK.md` |
| ⏳ | 4h paper soak against live Fyers | In progress; required before live orders |
| ⬜ | `ExecutionPort` interface (Phase 4.1) | Subclassed by `FyersExecutionPort` (Phase 4.2) |
| ⬜ | Idempotent order submission | Tick-derived order keys; broker orderbook query before retry |
| ⬜ | Fyers 502/504 retry with backoff | Exponential, max 3 attempts |
| ⬜ | Fill reconciliation | `LegActionIntent` → fill verification via `GET /orderbook` |
| ⬜ | Capital deployment | ₹2.5L in Liquid BeES + ₹3.5L trading capital |

**DoD (Phase 4 complete):** 4h paper soak shows < 1% broker errors and reasonable approve rate. Then live with 1-lot constraint and ₹8,000 daily cap.

---

## Phase 5 — Periphery & Observability (Gated on Phase 4)

| Status | Item | Notes |
|--------|------|-------|
| ⬜ | Agent 0 (Pre-Market Scout) on Lambda | 08:00 IST cron; writes `overnight_context.json` to S3 |
| ⬜ | Telegram HITL (Agent 5) on Lambda | Function URL; processes Approve/Veto callbacks |
| ⬜ | End-of-day archiver on Lambda | DDB traces → Parquet in S3 Data Lake |
| ⬜ | Agent 6 (Analyzer) trace clustering | Embedding-based; runs nightly |
| ⬜ | Agent 7 (Parameter Tuner) on Lambda | Proposes `risk_config.json` diffs; clamped by `absolute_limits`; HITL-gated |
| ⬜ | `TraceLogger` to DynamoDB | Per-tick rows; 5-year retention for SEBI |

**DoD (Phase 5 complete):** Agent 0 produces a real `overnight_context.json` daily. Agent 7 produces a parameter proposal that passes human review.

---

## Track 1 — v4.1 Ship (In Flight)

**Goal:** Lock the deterministic core, validate via paper soak, deploy live with 1-lot constraint.

| Milestone | Status |
|-----------|--------|
| 0+1+2+3+4 deterministic core | ✅ |
| Paper-mode harness | ✅ |
| 4h paper soak | ⏳ |
| Capital deployment ₹6L | ⬜ |
| Live with 1 lot constraint | ⬜ |

**Exit criteria:** 20+ hours of paper-soak data over multiple trading days, with `PAPER_APPROVE` count > 0 and broker error rate < 1%.

---

## Track 2 — v4.2 Positional Weekly Spreads (Gated on Track 1)

**Goal:** Add a sibling pipeline for positional weekly spreads on daily/4-hour signals. Same risk profile as Track 1 (defined-risk) but with better signal-to-noise.

| Item | Notes |
|------|-------|
| `StrategyClass.POSITIONAL` enum | On `AgentContext`; defaults to `INTRADAY` |
| `positional_run_chain` | Mirror of `_run_entry_chain` with 4-hour cadence |
| `ExpirySelectionError` extended | Positional 1-4 week DTE band |
| Weekly circuit-breaker cap | -₹15,000 weekly, separate from daily cap |
| New `StrategyName` entries | `BULL_CALL_SPREAD_WEEKLY`, `BEAR_PUT_SPREAD_WEEKLY` (vertical, 50/20 delta) |

**Gating:** Track 1 must demonstrate positive expectancy in paper soak first.

---

## Track 3 — v4.3+ Scaled (Gated on Track 2)

**Goal:** Scale to ₹10L+ capital with controlled naked-strategy exposure.

| Item | Notes |
|------|-------|
| 2× credit stop-loss rules | New gatekeeper rule for naked strategies |
| Sub-portfolio caps | Separate ₹25k cap for naked-strategy sub-portfolio |
| `short_strangle` with stop | Returns to enum with stop-loss guarantee |
| NIFTY futures positional | 1 lot, 2% mechanical stop, weekly cap |
| Daily token rotation Lambda | Fyers access token expires 03:30 IST |

**Gating:** Track 2 must demonstrate positive expectancy over 50+ paper trades.

---

## Track Independence

The three tracks are **independent code paths** but share the `AgentContext` and `RiskConfig`. Track 2 adds a new entry chain (`positional_run_chain`); Track 3 extends `StrategyName` and adds new gatekeeper rules. Tracks can be developed in parallel once Track 1 is shipped.

---

Last updated: 2026-06-07 (v4.1 ship readiness review)
