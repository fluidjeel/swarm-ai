Pending Fixes Checklist

Tracked from MiniMax review, Safety Audit, and Phase 2/3 close-out.

Done: lot scaling, slippage, boundary tests, PCR momentum None vs 0.0, FeatureEngineError.code, GatekeeperRule StrEnum, Nifty 50 proxy docs.

🔥 Architecture / Safety Audit Fixes (CRITICAL)

Status

Item

Notes

✅

OS-Level Loop Lock

FileTickLock (fcntl/msvcrt) wired into SessionPipeline.run_tick().

✅

Purge Vector Memory from AgentContext

Removed similar_regimes / SimilarRegimeSnapshot; added feature_snapshot_price + data_degraded.

⬜

Fyers State Recovery

Force GET /positions on EC2 boot/loop init. Never trust local memory/DynamoDB for has_open_position status.

⬜

Stale Quote Abort

Agent 3 / Gatekeeper must verify abs(current_price - feature_snapshot_price) < 10. Abort if market moved during calc.

⬜

Hard Stop Enforcement

2.5x ATR stop must be placed as a native Broker Bracket/Cover order at exact time of entry.

⬜

Idempotent Retries

Handle Fyers 504 Timeouts by explicitly querying orderbook before retrying an order placement.

Gatekeeper (src/risk/gatekeeper.py)

Status

Item

Notes

✅

Dynamic lot scaling (HLDD §2.2)

compute_allowed_lots(), reject if requested_lots > allowed

✅

Slippage surface (₹150 futures / ₹40 options)

expected_round_trip_cost on every GatekeeperDecision

✅

Boundary tests (VIX, DTE, PnL, lots, slippage)

tests/test_gatekeeper.py

✅

GatekeeperRule StrEnum for rule_id

Typos become import-time errors

⬜

Implement absolute_limits.json check

Hard-ceiling caps for LLM parameter tuning

⬜

Case-normalize payload keys at boundary

_read_float only tries known aliases

🔄

Wire gatekeeper into orchestrator / AgentContext

SessionPipeline Steps 1–2 done; gatekeeper in Step 4

Feature Engine (src/features/)

Status

Item

Notes

✅

PCR momentum: None = no history, 0.0 = flat

compute_expiry_weighted_pcr_momentum returns float | None

✅

FeatureEngineError.code enum (TIMEOUT, MARKET_DATA, …)

Standardize caller handling

⬜

Trading-day DTE (NSE holiday calendar)

Calendar DTE overstates near holidays

⬜

VIX/ATR divergence threshold bands

Formula exists; calibration for Agent 1 TBD

⬜

Increase default timeout (30s → 60s) or document

Opening-hour Fyers latency

⬜

Assert save_pcr_snapshot called in feature engine test

Implicit today

Exit Engine (src/risk/exit_engine.py)

Status

Item

Notes

✅

Core rules (ATR stop, regime flip, theta, VIX spike)

10 tests passing

⬜

ExitRule StrEnum for rule_id

Same pattern as gatekeeper

⬜

Wire into live position loop

Phase 3/4

Ops / Infra

Status

Item

Notes

⬜

EC2 soak: confirm poll_complete in feature_engine.log

Weekend stability run

⬜

Monday market hours: live run_regime_metrics.py smoke

Validate non-zero breadth

✅

Rotate leaked Anthropic/opencode API key

User rotated key (2026-06-06)

✅

Untrack ~/.claude, extend .gitignore

Commit d32e7f5

⬜

Purge ~/.claude from git history

Requires git filter-repo + force push to main

⬜

EC2 memory watchdog

Add psutil RAM check at start of tick. Halt if >85%.

Phase 3+ Roadmap

Status

Item

⬜

Agent 0 pre-market scout (Lambda)

⬜

Agent 1 (Regime) Pure Python Thresholds

⬜

Agent 2 (Strategy) Python Lookup Matrix

⬜

Agent 3 (Critic) Live Greeks & Spread Validation

⬜

Telegram HITL Webhook (Lambda)

⬜

Invocation Router + Fyers execution (Phase 4)

⬜

Lambda parameter tuner (Agent 7 - Phase 5)

Last updated: June 2026 (Deterministic Pivot)