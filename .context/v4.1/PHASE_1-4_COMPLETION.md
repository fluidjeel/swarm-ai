# Phases 1–4 Completion Record (v4.1 Shipped Baseline)

**Archived:** 2026-06-13

This document records what **shipped in code** under v4.1. v5.0 builds on this foundation; it does not replace it until Epics 1–3 land and deployment gates pass (see `PIVOT_DECISION.md`).

## Phase 1 — Observability, Security, & Environment Core ✅

| Epic | Status |
|------|--------|
| 1.1 AWS & local standup | ✅ EC2, S3, DynamoDB scaffolded |
| 1.2 Cross-cutting infra | ✅ AgentContext, `@trace_agent`, FileTickLock |
| 1.3 Eval suite & configs | ✅ `evals/`, `risk_config.json`, `absolute_limits.json` |

## Phase 2 — Deterministic State Engine ✅

| Epic | Status |
|------|--------|
| 2.1 Feature Engine | ✅ Fyers A/D, PCR momentum, VIX divergence |
| 2.2 Gatekeeper & Exit | ✅ Circuit breakers, recovery, ATR stops |

Local Greeks: `src/features/greeks_engine.py` — Black-Scholes from Fyers quotes.

## Phase 3 — Intraday Python Engine ✅

| Epic | Status |
|------|--------|
| 3.2 Agents 1–3 | ✅ Pure Python regime → strategy → critic |
| 3.1 Agent 0 Scout | 🟡 Stub; Lambda deferred |
| 3.3 Telegram HITL | 🟡 Stub; Lambda deferred |

## Phase 4 — Execution & Integration 🟡 (code complete; ops gate open)

| Item | Status |
|------|--------|
| ExecutionPort + Mock + NoOp | ✅ |
| FyersExecutionPort | ✅ |
| Idempotent submit + 504 recovery | ✅ |
| Per-tick broker position sync | ✅ |
| Paper soak harness | ✅ `paper_mode.py` |
| RAM watchdog, heartbeat, JSONL traces | ✅ |
| Hard broker bracket stops | ⬜ Phase 4.2+ |
| 4h live Fyers soak (ops) | ⏳ Human gate |
| Live 1-lot | ⏳ Gated on soak |

## Track 1 exit criteria (still valid for core book)

- `PAPER_APPROVE` count > 0
- Broker error rate < 1%
- No engine crashes over 20h+ paper data

Runbooks: `docs/SOAK_TEST_RECIPE.md`, `docs/PAPER_MODE_RUNBOOK.md`, `docs/CAPITAL_DEPLOYMENT_CHECKLIST.md`.

## Structural safety fences (do not bypass casually)

- `StrategyName` enum: iron_condor, bull_call_spread, bear_put_spread, cash_no_trade only
- Naked / short straddle / strangle **removed** (Step 4.4)
- Per-leg friction model shipped post-archive (benefits v4.1 paper soak too)
