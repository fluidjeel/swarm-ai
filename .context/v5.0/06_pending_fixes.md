# Active Sprint Checklist — v5.0 Transition

**Last updated:** 2026-06-13  
**v4.1 carry-over checklist:** `../v4.1/06_pending_fixes.md`

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
