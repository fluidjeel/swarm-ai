# Pivot Decision: v4.1 Shipped Baseline → v5.0 Unboxed Edge (Gated Track)

**Date:** 2026-06-13  
**Status:** Approved for documentation restructure; development gated by epics below.

---

## Executive summary

v4.1 and v5.0 are **not** a routine version bump. v4.1 is a **defensive, auditable, 1-lot REST engine** that is largely built and paper-soaking. v5.0 is an **aggressive compounding track** (WebSocket, fractional sizing, 0-DTE harvesting) that **reuses the v4.1 deterministic core** but changes risk posture materially.

**Decision:** v4.1 docs live in `.context/v4.1/` (matches code). v5.0 docs live in `.context/v5.0/` (north-star roadmap). Do **not** deploy v5 Epics 4–6 against the full ₹6L pool until Gate 1 passes on paper.

---

## What stays true (both versions)

1. **Deterministic intraday core** — No LLM in the hot path.
2. **AgentContext** is the only state bus; Pydantic schema-first.
3. **Broker GET /positions** is execution source of truth.
4. **Fail-closed** on missing or degraded data.
5. **Observable always** — DynamoDB / JSONL traces.
6. **Agent 7 never deploys code** — config-only mutations, clamped by `absolute_limits.json`, Telegram HITL.

---

## v4.1 → v5.0: material changes

| Dimension | v4.1 (shipped / running) | v5.0 (target) |
|-----------|--------------------------|---------------|
| Return target | 35–45% CAGR, &lt;15% DD | 100% CAGR (stretch); 15% survival floor via Expectancy Controller |
| Data path | 5-min REST poll | Fyers v3 WebSocket continuous daemon |
| Sizing | 1 lot + capital step function | 2.5–3.0% fractional risk per trade |
| PCR thresholds | ±0.02 (noise-sensitive) | ±0.12 for breakouts |
| Strategies | Iron condor, vertical spreads only | Adds 0-DTE short straddle matrix (post-13:00 IST) |
| Entry gates | Stale quote, VIX, DTE, critic | + IV percentile gate, friction EV block |
| Exits | ATR / regime flip / theta | Premium-based stops (credit/debit denominated) |
| Agent 7 | Parameter tuner | Expectancy Controller (auto-throttle on 8% rolling DD) |
| Paper PnL | Heuristic gross − friction | True MTM from entry/exit premiums |

---

## Guardrail collisions (must resolve before Epic 5)

v4.1 **deliberately removed** naked / short-vol strategies (Step 4.4, 2026-06-07):

- `short_straddle` / `short_strangle` deleted from `StrategyName`, gatekeeper, exit_engine.
- Changelog: *"engine is now structurally incapable of selecting naked or futures strategies."*

v5 Epic 5 (0-DTE Short Straddle matrix) **requires re-enabling** with **replacement guardrails**:

| Removed v4.1 fence | v5 replacement (required before code) |
|--------------------|----------------------------------------|
| Naked strategy block | New `StrategyName` + sub-capital bucket (see below) |
| 0-DTE iron condor block (DTE ≤ 1) | Explicit expiry-day matrix with time gate (≥ 13:00 IST only) |
| 1-lot fixed sizing | Fractional sizing capped by freeze qty + Expectancy Controller |
| Flat ₹40 friction | Per-leg ₹40 (✅ shipped: `src/risk/friction.py`) |
| Heuristic paper PnL | True MTM (Epic 1 — in progress) |

**No Epic 5 code until:** PIVOT sign-off + per-leg premium stops + ring-fenced capital documented in `04_finance_guru.md`.

---

## Epic sequencing (risk order, not convenience)

### Tier A — Improves v4.1 and v5 (do first)

| Epic | Items | Notes |
|------|-------|-------|
| **Epic 1: Truth Engine** | True MTM paper PnL, per-leg friction ✅, IV percentile gate | Makes existing paper soak trustworthy |
| **Epic 2: Directional uncaging** | PCR ±0.12, premium-based stops | Testable on REST pipeline before WebSocket |

### Tier B — Infrastructure (decouple from aggression)

| Epic | Items | Notes |
|------|-------|-------|
| **Epic 3: Infra** | WebSocket, asyncio daemon, reconnect | Can run 1-lot defensive logic on WebSocket first |

### Tier C — High risk (gated sub-account only)

| Epic | Items | Gate |
|------|-------|------|
| **Epic 4: Dynamic sizing** | 2.5–3% fractional Kelly | Gate 1 paper edge + Gate 3 live slippage test |
| **Epic 5: 0-DTE matrix** | Post-13:00 straddle | Guardrail collision resolved + ₹1L ring-fence |
| **Epic 6: Expectancy Controller** | 14d DD throttle | Safety net for Tier C — not a substitute for gates |

---

## Capital allocation decision

| Pool | Amount | Use |
|------|--------|-----|
| **Core book (v4.1 Track 1)** | ₹5.0L trading + ₹2.5L Liquid BeES reserve | Live 1-lot defined-risk until v4.1 soak sign-off |
| **Edge lab (v5 Tier C)** | ₹1.0L ring-fenced | 0-DTE / fractional sizing experiments only after Gate 1 |
| **Daily breaker (both)** | −₹8,000 session halt | Non-negotiable |

100% CAGR is a **stretch hypothesis**, not a deployment mandate. Gate 1 must prove edge before Tier C touches main capital.

---

## Live deployment gates (v5)

### Gate 1 — Statistical proof (paper)

- 14 consecutive trading days with True MTM + per-leg friction.
- Targets: Win rate &gt; 60%, profit factor &gt; 1.4, expectancy &gt; +₹1,000/trade.

### Gate 2 — WebSocket stability

- 3 full sessions (09:15–15:30 IST), zero dropped connections, 100% data continuity.

### Gate 3 — Live slippage calibration

- 5 business days live at **forced 1-lot** clamp.
- Compare broker fills vs paper MTM; friction model tuned if systematic bias &gt; 10%.

---

## v4.1 Track 1 (parallel, not cancelled)

v4.1 paper soak and optional live 1-lot **continue** as baseline proof:

- Runbook: `docs/SOAK_TEST_RECIPE.md`
- Capital checklist: `docs/CAPITAL_DEPLOYMENT_CHECKLIST.md`

v5 Tier A (Truth Engine) **improves** v4.1 soak quality — not a blocker to finishing Track 1.

---

## Document map after restructure

- **v4.1 (current):** `.context/v4.1/` = matches `src/` code today
- **v5.0 (target):** `.context/v5.0/` = Phase 5.0 sprint specs
- **Ops runbooks:** `docs/SOAK_TEST_RECIPE.md` remains v4.1 until WebSocket soak recipe exists
