# Active Sprint Checklist — v5.0 Transition

**Last updated:** 2026-06-13  
**v4.1 carry-over checklist:** `../v4.1/06_pending_fixes.md`

---

## Tier 0 — Resilience Hardening (P0 — BEFORE any live capital)

From institutional architecture review (2026-06-13). These close real capital-risk
holes and are sequenced by dependency. See "Resilience review notes" at the bottom.

| Status | Item | Notes |
|--------|------|-------|
| ⬜ | **Stop-ownership reality check** | Test if Fyers accepts BO/CO on NIFTY options for this account. If rejected → stops are permanently synthetic (EC2-only). 1hr test. **DEFERRED — assuming synthetic; dead-man's switch built regardless.** |
| ✅ | **TraceID threading** | `uuid4` on `AgentContext` stamped through tick trace + all paper rows (approve/order-ack/exit/sync). `src/core/context.py`, `session_pipeline.py`, `tick_trace.py`. |
| ✅ | **Dead-man's switch** | `src/orchestration/deadman.py` (heartbeat-absence detector, out-of-process CLI) + `src/observability/alerting.py` (AlertSink/Telegram/logging). CloudWatch wiring still ops. |
| ✅ | **4-way state reconciliation** | `reconcile_broker_state` in `broker_recovery.py`: positions+orders+trades+funds → `reconciliation_halt`. New optional provider methods `get_orders`/`get_trades`/`get_funds`. Hard-stops the tick. |
| ✅ | **Call timeouts (tick deadline)** | `run_tick` wraps body in `asyncio.wait_for(tick_timeout_sec=120)`; cancels hung body and releases lock in `finally`. |
| ✅ | **Lock TTL + watchdog** | `FileTickLock` writes pid/host/heartbeat sidecar; `_try_break_stale_lock` kills hung same-host holder past TTL (default 360s). |
| ✅ | **Adaptive stale-quote threshold** | `effective_stale_quote_threshold = min(10pt ceiling, 0.5 × 5m_ATR)`. ATR can only *tighten* (Prime Directive #5 keeps 10pt as a hard ceiling). |
| ✅ | **Chaos Monkey test suite** | `tests/test_chaos_execution.py`: 504-but-placed, retry-success, persistent-504, duplicate-tag, hard-reject, partial-fill flatten. |

**Tier 0 status: code-complete on `pivot/v5.0-dev`. Remaining = ops wiring
(CloudWatch alarm → dead-man CLI, Telegram creds) + the deferred BO/CO probe.**

---

## Tier A — Truth Engine & Direction (current sprint)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | Per-leg friction (₹40/leg) | `src/risk/friction.py`, gatekeeper EV block, noop_port, analyzer |
| ✅ | True MTM paper PnL | `compute_paper_mtm` (real entry/exit premiums) → PAPER_EXIT rows; analyzer prefers logged `gross/net/friction`, heuristic only as legacy fallback |
| ✅ | IV percentile gate | Gatekeeper `LOW_VOLATILITY_ENVIRONMENT` rule, scoped to premium selling (iron condor). **Runs on VIX proxy today** (`vix_low_vol_floor`); true IVP path is wired but dormant — see follow-up below |
| ✅ | PCR threshold widen (±0.12) | `risk_config.json` + `risk_config.py` defaults + regime classifier; tested |
| ✅ | Premium-based exit stops | `ExitEngine` credit-denominated: `credit_stop_loss` (1.5× entry credit), `theta_capture`, `vix_intraday_spike`; multi-leg uses net credit vs net close cost; tested single + multi-leg |

**Tier A status: code-complete on `pivot/v5.0-dev`.**

### Tier A follow-up (new)

| Status | Item | Notes |
|--------|------|-------|
| ⬜ | **True IV-percentile feed** | The IV gate falls back to the VIX proxy because the Feature Engine does not yet compute an IV-percentile rank. Build an IV-history store (mirror `pcr_history`), surface `iv_percentile` on `OpeningRegime`, and thread it through `_feature_payload_from_ctx`. Then the gate uses real IVP (`iv_percentile_min=30`) instead of `vix_low_vol_floor`. |

---

## Tier B — Infrastructure (after Tier A baseline)

| Status | Item | Notes |
|--------|------|-------|
| ⬜ | Fyers v3 WebSocket | Replace REST hot path |
| ⬜ | Asyncio continuous daemon | systemd/pm2 |
| ⬜ | WebSocket reconnect + backoff | Fail-closed on prolonged gap |
| ⬜ | WebSocket soak runbook | New doc when Epic 3 lands |

---

## Tier C — Gated (Edge lab ₹1L only, post Gate 1)

| Status | Item | Notes |
|--------|------|-------|
| ⬜ | Fractional sizing (2.5–3%) | Requires Gate 1 + 3 |
| ⬜ | 0-DTE expiry matrix (post-13:00) | Requires PIVOT guardrail sign-off + enum |
| ⬜ | Expectancy Controller (Agent 7) | 14d DD throttle + Telegram HITL |
| ⬜ | Freeze quantity limiter | Exchange cap enforcement |

---

## v4.1 carry-over (still open — core book)

| Status | Item | Notes |
|--------|------|-------|
| ⬜ | 4h+ multi-day paper soak | `docs/SOAK_TEST_RECIPE.md` |
| ⬜ | Capital deployment ₹6L | `docs/CAPITAL_DEPLOYMENT_CHECKLIST.md` |
| ⬜ | Live 1-lot core book | Post soak sign-off |
| ⬜ | Hard broker bracket stops | Phase 4.2+ |
| ⬜ | Daily Fyers token rotation Lambda | Manual reauth for now |
| ⬜ | CloudWatch heartbeat alarm | Ops |
| ⬜ | Telegram halt alerter | Ops |

---

## Recently closed (v4.1 foundation)

Tick lock, broker recovery + multi-leg aggregation, Agents 1–3 pure Python, StrategyName enum fence, execution port + idempotency, per-tick broker sync, RAM watchdog, paper soak harness, shutdown flatten, NIFTY chain trim, DDB tick traces.

Full v4.1 audit table: `../v4.1/06_pending_fixes.md`.

---

## Gatekeeper note

Slippage surface is now **per-leg**: iron condor ₹160, 2-leg spread ₹80, single leg ₹40. Update any stale "flat ₹40" references when touching docs/tests.

---

## Risks before Tier C

1. Live fractional sizing without Gate 1 paper proof (P0)
2. 0-DTE straddle without enum + per-leg stops + ring-fence (P0)
3. WebSocket deploy without reconnect fail-closed (P1)
4. True MTM not shipped but Gate 1 measured against heuristics (P1)

See `PIVOT_DECISION.md`.

---

## Resilience review notes (2026-06-13 architecture review)

Code-verified status of the "six catastrophic risks":

| Risk | Shipped today | Gap |
|------|---------------|-----|
| State recovery | `get_positions()` only + per-tick `sync_position_from_broker` (fail-closed on orphan/partial) | No orders/trades/funds reconciliation |
| Tick sync | `FileTickLock` (fcntl/msvcrt) | No TTL/watchdog; relies on OS releasing flock on death |
| Stale quote | Fixed `stale_quote_points = 10` | Not volatility-adaptive |
| Idempotency | ✅ orderTag + orderbook re-query + single retry + `PartialFillError` | Strongest part; needs chaos tests |
| Catastrophic loss | Synthetic stop in `ExitEngine` only; `productType=MARGIN` | **No bracket/cover order exists** — exchange-side protection is NOT implemented |
| Observability | `@trace_agent` + JSONL/DDB traces | No `trace_id` correlation; Telegram HITL is a stub; no CloudWatch alarm |

**Correction to common assumption:** "Exchange holds the stop even if EC2 dies" is **false today** — stops are 100% EC2-synthetic. This makes the dead-man's switch a P0, not an enhancement.
