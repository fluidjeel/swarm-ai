# Active Sprint Checklist — v5.0 Transition

**Last updated:** 2026-06-13  
**v4.1 carry-over checklist:** `../v4.1/06_pending_fixes.md`

---

## Tier 0 — Resilience Hardening (P0 — BEFORE any live capital)

From institutional architecture review (2026-06-13). These close real capital-risk
holes and are sequenced by dependency. See "Resilience review notes" at the bottom.

| Status | Item | Notes |
|--------|------|-------|
| ⬜ | **Stop-ownership reality check** | Test if Fyers accepts BO/CO on NIFTY options for this account. If rejected → stops are permanently synthetic (EC2-only). 1hr test. |
| ⬜ | **TraceID threading** | `uuid4` on `AgentContext` stamped through feature → critic → gatekeeper → order → fill → exit. Cheap; unblocks all debugging. Do first. |
| ⬜ | **Dead-man's switch** | Heartbeat → CloudWatch alarm → Telegram. **P0 if stops are synthetic** (EC2 death = unprotected position). |
| ⬜ | **4-way state reconciliation** | `broker_recovery.py` must reconcile positions + orders + trades + funds. On mismatch → `RECONCILIATION_HALT`. Needs new provider methods `get_orders`/`get_trades`/`get_funds`. |
| ⬜ | **Call timeouts everywhere** | Every Fyers call needs a hard timeout so a hung-but-alive process cannot orphan the fcntl lock. (Note: OS auto-releases flock on process *death*; risk is hung-alive only.) |
| ⬜ | **Lock TTL + watchdog** | Lock file writes creation ts; orchestrator kills PID + clears lock if > 6 min stale. Backstop to timeouts. |
| ⬜ | **Adaptive stale-quote threshold** | Replace fixed 10-pt with `> 0.5 × 5m_ATR`. Static threshold paralyzes in high VIX, under-protects in low VIX. |
| ⬜ | **Chaos Monkey test suite** | pytest fault injection on `FyersExecutionPort`: 504s, partial fills (2/4 legs), duplicate orderTags. Validates idempotency + reconciliation. Build AFTER reconciliation exists. |

---

## Tier A — Truth Engine & Direction (current sprint)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | Per-leg friction (₹40/leg) | `src/risk/friction.py`, gatekeeper EV block, noop_port, analyzer |
| ⏳ | True MTM paper PnL | Replace heuristic gross in `noop_port.py` + analyzer |
| ⏳ | IV percentile gate | Block premium selling below 30th pct IV |
| ⬜ | PCR threshold widen (±0.12) | `risk_config.json` + regime classifier |
| ⬜ | Premium-based exit stops | Exit engine credit/debit denominated |

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
