# Project Brief: A2A Trading Engine (v5.0 — Unboxed Edge)

> **Target architecture — not fully deployed.** Running code: `../v4.1/`. Migration: `PIVOT_DECISION.md`.

## 1. Executive Summary

The Agent-to-Algorithm (A2A) Trading Engine v5.0 is a semi-autonomous quantitative middleware for Indian index derivatives (NIFTY / BANKNIFTY / SENSEX). It extends the v4.1 deterministic core with **dynamic fractional sizing**, **continuous WebSocket execution**, and **premium-denominated risk controls**.

Philosophy: **"Unboxed upside, hard-floored downside."** Scale into mathematically verified edges; the Expectancy Controller enforces a 15% absolute survival floor if conditions decay.

Core mantra (unchanged): **Deterministic by default, Agentic by exception, Observable always.**

Capital pool: ₹6,00,000–₹7,00,000 (see `04_finance_guru.md` for core vs edge-lab split).

## 2. Problems Solved

| Problem | v5.0 approach |
|---------|----------------|
| Compounding drag (fixed lots) | Fractional sizing: 2.5–3.0% risk capital per valid setup |
| REST latency (5-min poll) | Fyers v3 WebSocket + continuous asyncio daemon |
| Hallucination / slippage | Pure Python intraday; LLMs only in Lambda periphery |
| Regulatory audit | White-box decision trees → DynamoDB traces |
| Underestimated friction | Per-leg round-trip cost (₹40/leg) + friction EV gate |

## 3. Three-Tier Workflow

### Tier 0 — Pre-Market (Lambda + LLM)

**Agent 0 (Scout):** GIFT Nifty, FII/DII, overnight macro → `overnight_context.json`.

### Tier 1 — Intraday (EC2 — Pure Python, WebSocket)

| Agent | Role |
|-------|------|
| **Agent 1** | Regime classifier on streaming features; PCR ±0.12 breakouts; VIX compression for range |
| **Agent 2** | Strategy selector + 0-DTE harvester; debit/credit spreads + condors; post-13:00 expiry matrix (gated) |
| **Agent 3** | Pre-trade critic: Greeks, spreads, stale quote, **IV percentile gate** |

### Tier 2 — Post-Market (Lambda)

| Agent | Role |
|-------|------|
| **Agent 6** | Nightly analyzer: slippage, decay, misclassification |
| **Agent 7** | **Expectancy Controller:** 14-day rolling DD; auto-throttle to 1% risk if DD > 8%; Telegram HITL to restore |

## 4. Goals

**Stretch:** Verified compounding equity curve targeting 100% CAGR on edge-lab capital after Gate 1.

**Commercialization:** Package orchestration (Router, Gatekeeper, Expectancy pipeline) as IaaS for HNIs / prop desks after multi-regime live proof.

## 5. Relationship to v4.1

v4.1 Phases 1–4 are **complete in code**. v5 Phase 5.0 is a **gated evolution**, not a replacement until WebSocket + gates pass. See `03_execution_plan.md`.
