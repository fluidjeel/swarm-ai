# Execution Plan & Roadmap: A2A Trading Engine v4.1

Phases are chronological; do not advance until the current phase DoD is satisfied.

---

## Phase 1 ΓÇö Observability, Security, & Environment Core Γ£à

| Epic | Status |
|------|--------|
| 1.1 AWS & local standup | Γ£à EC2, S3, DynamoDB scaffolded |
| 1.2 Cross-cutting infra | Γ£à AgentContext, `@trace_agent`, FileTickLock |
| 1.3 Eval suite & configs | Γ£à `evals/`, `risk_config.json`, `absolute_limits.json` |

---

## Phase 2 ΓÇö Deterministic State Engine Γ£à

| Epic | Status |
|------|--------|
| 2.1 Feature Engine | Γ£à Fyers A/D, PCR momentum, VIX divergence |
| 2.2 Gatekeeper & Exit | Γ£à Circuit breakers, recovery, ATR stops |

**Local Greeks (Pass 0.5):** `src/features/greeks_engine.py` ΓÇö Black-Scholes from Fyers quotes.

---

## Phase 3 ΓÇö Intraday Python Engine Γ£à

| Epic | Status |
|------|--------|
| 3.2 Agents 1ΓÇô3 | Γ£à Pure Python regime ΓåÆ strategy ΓåÆ critic |
| 3.1 Agent 0 Scout | ≡ƒƒí Stub (`src/periphery/agent0_scout.py`); Lambda deferred |
| 3.3 Telegram HITL | ≡ƒƒí Stub (`src/periphery/agent5_hitl.py`); Lambda deferred |

---

## Phase 4 ΓÇö Execution & Integration ≡ƒƒí (code complete; ops gate open)

| Item | Status |
|------|--------|
| ExecutionPort + Mock + NoOp | Γ£à |
| FyersExecutionPort | Γ£à `src/execution/fyers_port.py` |
| Idempotent submit (orderTag + orderbook) | Γ£à |
| 504/502 retry + orderbook recovery | Γ£à |
| Per-tick broker position sync | Γ£à `sync_position_from_broker()` |
| Post-submit fill reconcile | Γ£à `verify_entry_fills()` |
| Paper soak harness | Γ£à `paper_mode.py` |
| `--broker` broker-exercising soak | Γ£à |
| `--mock` offline soak (CI) | Γ£à |
| RAM watchdog (psutil) | Γ£à `runtime_guards.py` |
| Heartbeat + tick trace JSONL | Γ£à |
| Soak log validator | Γ£à `scripts/validate_soak_log.py` |
| Hard broker bracket stops | Γ¼£ Phase 4.2+ |
| **4h live Fyers soak (ops)** | ΓÅ│ **You** ΓÇö Monday runbook in `docs/SOAK_TEST_RECIPE.md` |
| Capital deployment Γé╣6L | ΓÅ│ **You** ΓÇö see `docs/CAPITAL_DEPLOYMENT_CHECKLIST.md` |
| Live 1-lot | ΓÅ│ Gated on soak pass + sign-off |

**DoD:** 4h paper soak &lt; 1% broker errors, sane approve rate, then live with 1-lot + Γé╣8k daily cap.

---

## Phase 5 ΓÇö Periphery & Autonomous Tuning ≡ƒƒí (stubs shipped)

| Item | Status |
|------|--------|
| Agent 0 overnight context | ≡ƒƒí Stub writes `data/overnight_context.json` |
| Agent 5 Telegram HITL | ≡ƒƒí Stub callback processor |
| Agent 6 nightly analyzer | ≡ƒƒí Stub clusters JSONL traces |
| Agent 7 parameter tuner | ≡ƒƒí Stub proposes clamped `risk_config` patch |
| EOD archiver | ≡ƒƒí Stub copies logs to `~/soak-archive` |
| TraceLogger ΓåÆ DynamoDB | ≡ƒƒí `DynamoDBTickTraceWriter` optional; JSONL default |
| Lambda deploy + EventBridge | Γ¼£ Infra ΓÇö mock until AWS wiring |

---

## Track 1 ΓÇö v4.1 Ship (in flight)

| Item | Owner |
|------|-------|
| Deterministic core | Γ£à Done |
| Paper harness + validation scripts | Γ£à Done |
| 4h soak ├ù multi-day (20h+) | ΓÅ│ **You** (market hours) |
| Broker-exercising `--broker` soak | ΓÅ│ **You** (after NoOp pass) |
| Live 1-lot | ΓÅ│ **You** (post sign-off) |

---

## Track 2 / 3 ΓÇö Gated

Positional weekly spreads and scaled naked exposure remain **gated** on Track 1 paper-soak expectancy. No code started (by design).

---

## Quick validation (no market required)

```bash
python -m pytest tests/ -q
python -m src.orchestration.paper_mode --mock
python scripts/validate_soak_log.py logs/paper_soak/<session>.jsonl --smoke
```
