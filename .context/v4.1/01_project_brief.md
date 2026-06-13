# Project Brief: A2A Trading Engine (v4.1 — Shipped Baseline)

> **Matches running code today.** Target roadmap: `../v5.0/01_project_brief.md`

## 1. Executive Summary

The Agent-to-Algorithm (A2A) Trading Engine is hybrid quantitative middleware for Indian derivatives (NIFTY/BANKNIFTY). Philosophy: **Deterministic by default, Agentic by exception, Observable always.**

Capital: ₹6,00,000–₹7,00,000 with ₹2,50,000 Liquid BeES reserve pledged for margin. Deterministic Python core; LLMs only as async pre-market scouts and post-market parameter tuners. Intraday execution is 100% pure Python math.

## 2. Problems Solved

- **Latency trap:** LLMs off hot path; Python state machines execute live.
- **Hallucination:** Strategy selection via lookup matrices + Greek validation.
- **Silent failure:** Tick locks, broker recovery, trace logging, Pydantic contracts.
- **Regulatory audit:** White-box Python decision trees.

## 3. Three-Tier Workflow

| Tier | Agents | Role |
|------|--------|------|
| 0 | Agent 0 | Pre-market scout (Lambda) → `overnight_context.json` |
| 1 | Agents 1–3 | 5-min REST: regime → strategy → critic |
| 2 | Agents 6–7 | Post-market analyzer + parameter tuner (Lambda) |

## 4. Commercialization

Goal 1: Proprietary equity curve on ₹6L. Goal 2: Package orchestration as IaaS for HNIs.
