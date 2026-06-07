# Execution Plan & Roadmap: A2A Trading Engine v4.1

Phases are chronological; do not advance until the current phase DoD is satisfied.

---

## Phase 1 — Observability, Security, & Environment Core ✅

| Epic | Status |
|------|--------|
| 1.1 AWS & local standup | ✅ EC2, S3, DynamoDB scaffolded |
| 1.2 Cross-cutting infra | ✅ AgentContext, `@trace_agent`, FileTickLock |
| 1.3 Eval suite & configs | ✅ `evals/`, `risk_config.json`, `absolute_limits.json` |

---

## Phase 2 — Deterministic State Engine ✅

| Epic | Status |
|------|--------|
| 2.1 Feature Engine | ✅ Fyers A/D, PCR momentum, VIX divergence |
| 2.2 Gatekeeper & Exit | ✅ Circuit breakers, recovery, ATR stops |

**Local Greeks (Pass 0.5):** `src/features/greeks_engine.py` — Black-Scholes from Fyers quotes.

---

## Phase 3 — Intraday Python Engine ✅

| Epic | Status |
|------|--------|
| 3.2 Agents 1–3 | ✅ Pure Python regime → strategy → critic |
| 3.1 Agent 0 Scout | 🟡 Stub (`src/periphery/agent0_scout.py`); Lambda deferred |
| 3.3 Telegram HITL | 🟡 Stub (`src/periphery/agent5_hitl.py`); Lambda deferred |

---

## Phase 4 — Execution & Integration 🟡 (code complete; ops gate open)

| Item | Status |
|------|--------|
| ExecutionPort + Mock + NoOp | ✅ |
| FyersExecutionPort | ✅ `src/execution/fyers_port.py` |
| Idempotent submit (orderTag + orderbook) | ✅ |
| 504/502 retry + orderbook recovery | ✅ |
| Per-tick broker position sync | ✅ `sync_position_from_broker()` |
| Post-submit fill reconcile | ✅ `verify_entry_fills()` |
| Paper soak harness | ✅ `paper_mode.py` |
| `--broker` broker-exercising soak | ✅ |
| `--mock` offline soak (CI) | ✅ |
| RAM watchdog (psutil) | ✅ `runtime_guards.py` |
| Heartbeat + tick trace JSONL | ✅ |
| Soak log validator | ✅ `scripts/validate_soak_log.py` |
| Hard broker bracket stops | ⬜ Phase 4.2+ |
| **4h live Fyers soak (ops)** | ⏳ **You** — Monday runbook in `docs/SOAK_TEST_RECIPE.md` |
| Capital deployment ₹6L | ⏳ **You** — see `docs/CAPITAL_DEPLOYMENT_CHECKLIST.md` |
| Live 1-lot | ⏳ Gated on soak pass + sign-off |

**DoD:** 4h paper soak &lt; 1% broker errors, sane approve rate, then live with 1-lot + ₹8k daily cap.

---

## Phase 5 — Periphery & Autonomous Tuning 🟡 (stubs shipped)

| Item | Status |
|------|--------|
| Agent 0 overnight context | 🟡 Stub writes `data/overnight_context.json` |
| Agent 5 Telegram HITL | 🟡 Stub callback processor |
| Agent 6 nightly analyzer | 🟡 Stub clusters JSONL traces |
| Agent 7 parameter tuner | 🟡 Stub proposes clamped `risk_config` patch |
| EOD archiver | 🟡 Stub copies logs to `~/soak-archive` |
| TraceLogger → DynamoDB | 🟡 `DynamoDBTickTraceWriter` optional; JSONL default |
| Lambda deploy + EventBridge | ⬜ Infra — mock until AWS wiring |

---

## Track 1 — v4.1 Ship (in flight)

| Item | Owner |
|------|-------|
| Deterministic core | ✅ Done |
| Paper harness + validation scripts | ✅ Done |
| 4h soak × multi-day (20h+) | ⏳ **You** (market hours) |
| Broker-exercising `--broker` soak | ⏳ **You** (after NoOp pass) |
| Live 1-lot | ⏳ **You** (post sign-off) |

---

## Track 2 / 3 — Gated

Positional weekly spreads and scaled naked exposure remain **gated** on Track 1 paper-soak expectancy. No code started (by design).

---

## Quick validation (no market required)

```bash
python -m pytest tests/ -q
python -m src.orchestration.paper_mode --mock
python scripts/validate_soak_log.py logs/paper_soak/<session>.jsonl --smoke
```
