# A2A Context Documentation

**Last updated:** 2026-06-13

## Two systems — two folders (prevents agent hallucination)

| Folder | Status | When to read |
|--------|--------|--------------|
| **[v4.1/](./v4.1/)** | **Current — matches `src/` code** | Bug fixes, paper soak, live ops, "how does it work today?" |
| **[v5.0/](./v5.0/)** | **Target — roadmap only** | Sprint planning, new features, "what are we building?" |

**Rule:** If the task changes runtime behavior, start with **v4.1**. Cross-check **v5.0** only when implementing a listed epic.

## Shared

| File | Purpose |
|------|---------|
| [v5.0/PIVOT_DECISION.md](./v5.0/PIVOT_DECISION.md) | v4.1 → v5 migration, gates, guardrail collisions |

## Code vs docs (honest status)

| Capability | Doc version | Code status |
|------------|-------------|-------------|
| 5-min REST `SessionPipeline` | v4.1 | ✅ Running |
| 1-lot + step scaling | v4.1 | ✅ Running |
| Per-leg friction + EV gate | v5 prep | ✅ Shipped on v4.1 runtime |
| True MTM paper PnL | v5 | ⏳ In progress |
| WebSocket daemon | v5 | ⬜ Not started |
| Fractional 3% sizing | v5 | ⬜ Gated |
| 0-DTE straddle matrix | v5 | ⬜ Blocked (enum fence) |

## Ops runbooks (v4.1 — still valid)

- `docs/SOAK_TEST_RECIPE.md`
- `docs/PAPER_MODE_RUNBOOK.md`
- `docs/CAPITAL_DEPLOYMENT_CHECKLIST.md`

## Deprecated

`.context/archive/` — superseded by `v4.1/`; safe to delete after review.
