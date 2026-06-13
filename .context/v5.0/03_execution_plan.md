# Execution Plan: A2A v5.0 (Unboxed Edge)

> **Active roadmap.** v4.1 Phases 1–4 complete — see `../v4.1/PHASE_1-4_COMPLETION.md`.

Phases 1–4 (v4.1 baseline) are **shipped**. Development focus is **Phase 5.0** below.

---

## Phase 5.0 — Unboxed Edge Sprint

### Epic 1: Truth Engine (Tier A — do first)

| Item | Status | Notes |
|------|--------|-------|
| Per-leg friction (₹40/leg) + EV gate | ✅ Done | `src/risk/friction.py`, gatekeeper, noop_port, analyzer |
| True MTM paper PnL | ⏳ Active | Capture entry/exit premiums in `noop_port.py` |
| IV percentile gate | ⏳ Next | Block cheap-vol premium selling |

### Epic 2: Directional Uncaging (Tier A)

| Item | Status | Notes |
|------|--------|-------|
| Widen PCR thresholds (±0.02 → ±0.12) | ⬜ Pending | `risk_config.json` |
| Premium-based stop-loss in Exit Engine | ⬜ Pending | Credit/debit denominated, not index ATR |

### Epic 3: Infra Overhaul (Tier B)

| Item | Status | Notes |
|------|--------|-------|
| Fyers v3 WebSocket migration | ⬜ Pending | Replace REST hot path |
| Asyncio continuous daemon | ⬜ Pending | systemd/pm2 managed |
| WebSocket reconnect + backoff | ⬜ Pending | Fail-closed on prolonged disconnect |

### Epic 4: Dynamic Sizing (Tier C — gated)

| Item | Status | Notes |
|------|--------|-------|
| Fractional sizing (2.5–3.0% risk) | ⬜ Pending | After Gate 1 |
| Exchange freeze quantity limiter | ⬜ Pending | |

### Epic 5: 0-DTE Matrix (Tier C — gated)

| Item | Status | Notes |
|------|--------|-------|
| Expiry-day matrix (post-13:00 IST) | ⬜ Pending | Requires StrategyName + guardrail work |
| Per-leg premium trailing stops | ⬜ Pending | |

### Epic 6: Expectancy Controller (Tier C)

| Item | Status | Notes |
|------|--------|-------|
| 14-day rolling drawdown analyzer | ⬜ Pending | Agent 7 evolution |
| Auto-throttle (DD > 8% → 1% risk) | ⬜ Pending | Telegram HITL to restore |

---

## v4.1 Track 1 (parallel — core book)

| Item | Status |
|------|--------|
| Deterministic core | ✅ |
| Paper harness + validation | ✅ |
| Multi-day paper soak (20h+) | ⏳ Ops |
| Live 1-lot on core ₹5L | ⏳ Post soak sign-off |

Runbooks: `docs/SOAK_TEST_RECIPE.md`, `docs/CAPITAL_DEPLOYMENT_CHECKLIST.md`.

---

## Live Deployment Gates (v5)

### Gate 1 — Statistical proof (paper)

- 14 consecutive days, True MTM + per-leg friction.
- Win rate &gt; 60%, profit factor &gt; 1.4, expectancy &gt; +₹1,000/trade.

### Gate 2 — WebSocket stability

- 3 full sessions (09:15–15:30 IST), zero drops, 100% continuity.

### Gate 3 — Live slippage calibration

- 5 days live at forced 1-lot; reconcile broker fills vs paper MTM.

---

## Quick validation (no market)

```bash
python -m pytest tests/ -q
python -m src.orchestration.paper_mode --mock
python scripts/validate_soak_log.py logs/paper_soak/<session>.jsonl --smoke
```
