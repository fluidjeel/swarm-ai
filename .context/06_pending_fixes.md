# Pending Fixes Checklist

Tracked from MiniMax review (Gatekeeper + Feature Engine) and Phase 2/3 close-out.  
**Done in this pass:** lot scaling, slippage surface, gatekeeper boundary tests, PCR momentum `None` vs `0.0`.

---

## Gatekeeper (`src/risk/gatekeeper.py`)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | Dynamic lot scaling (HLDD §2.2) | `compute_allowed_lots()`, reject if `requested_lots > allowed` |
| ✅ | Slippage surface (₹150 futures / ₹40 options) | `expected_round_trip_cost` on every `GatekeeperDecision` |
| ✅ | Boundary tests (VIX, DTE, PnL, lots, slippage) | `tests/test_gatekeeper.py` |
| ⬜ | `GatekeeperRule` StrEnum for `rule_id` | Typos become import-time errors |
| ⬜ | Document `dte <= 1` semantics (weekly vs monthly) | Configurable via `expiry_dte_block` already |
| ⬜ | Case-normalize payload keys at boundary | `_read_float` only tries known aliases |
| ⬜ | Ordered rule list (explicit priority) | Low priority until 5+ rules |
| ⬜ | Wire gatekeeper into orchestrator / `AgentContext` | Phase 3 integration |

---

## Feature Engine (`src/features/`)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | PCR momentum: `None` = no history, `0.0` = flat | `compute_expiry_weighted_pcr_momentum` returns `float \| None` |
| ⬜ | `FeatureEngineError.code` enum (`TIMEOUT`, `MARKET_DATA`, …) | Standardize caller handling |
| ⬜ | Rename Nifty 50 proxy field / method | `NIFTY_500_AD_Ratio` → document or `NIFTY_BREADTH_PROXY` |
| ⬜ | Trading-day DTE (NSE holiday calendar) | Calendar DTE overstates near holidays |
| ⬜ | VIX/ATR divergence threshold bands | Formula exists; calibration for Agent 1 TBD |
| ⬜ | Log warnings on degenerate inputs (`atr=0`, `previous_vix=0`) | Silent `0.0` today |
| ⬜ | Increase default timeout (30s → 60s) or document | Opening-hour Fyers latency |
| ⬜ | Timeout / sanitizer / `MarketDataError` tests | Happy-path only today |
| ⬜ | VIX trend boundary test (`delta == 0.15`) | `regime_metrics.py` |
| ⬜ | Assert `save_pcr_snapshot` called in feature engine test | Implicit today |

---

## Exit Engine (`src/risk/exit_engine.py`)

| Status | Item | Notes |
|--------|------|-------|
| ✅ | Core rules (ATR stop, regime flip, theta, VIX spike) | 10 tests passing |
| ⬜ | `ExitRule` StrEnum for `rule_id` | Same pattern as gatekeeper |
| ⬜ | Wire into live position loop | Phase 3/4 |
| ⬜ | Commit + push if still local-only | Verify `git status` |

---

## Cross-cutting

| Status | Item | Notes |
|--------|------|-------|
| ⬜ | DynamoDB logging for gatekeeper / exit decisions | Agent 6 clustering |
| ⬜ | Latency telemetry (`time.monotonic()` wrapper) | Matters at 1s re-eval cadence |
| ⬜ | `MarketProfile` config (NIFTY / BANKNIFTY / FINNIFTY) | Hardcoded symbols today |
| ⬜ | Review `trace_agent.py` + sanitizer depth | Next review target per MiniMax |
| ⬜ | Review data layer (`fyers_provider.py`) | Fyers-specific edge cases |
| ⬜ | Property-based tests (ATR, divergence) | Optional hardening |

---

## Ops / Infra

| Status | Item | Notes |
|--------|------|-------|
| ⬜ | EC2 soak: confirm `poll_complete` in `feature_engine.log` | Weekend stability run |
| ⬜ | Monday market hours: live `run_regime_metrics.py` smoke | Validate non-zero breadth |
| ⬜ | `git pull` on EC2 after push | Deploy latest |
| ⬜ | `iam_lambda_policy.json` account ID placeholder | `YOUR_AWS_ACCOUNT_ID` |
| ⬜ | Rotate exposed AWS access key | From prior `aws configure` session |
| ⬜ | EC2 cron/systemd for market-hours polling only | Phase 2 close-out |

---

## Phase 3+ (not blocking current foundation)

| Status | Item |
|--------|------|
| ⬜ | Agent 0 pre-market scout |
| ⬜ | Agent 1 production path (features → S3 prompt → LLM → `@trace_agent`) |
| ⬜ | Agents 2–3, vector store, critic veto |
| ⬜ | Telegram HITL (Agent 5) |
| ⬜ | Invocation Router + Fyers execution (Phase 4) |
| ⬜ | Lambda analyzer/compiler (Phase 5) |
| ⬜ | Prompt v2 tuning (live eval 7/22 behavioral) |
| ⬜ | Upgrade Nifty 50 → Nifty 500 breadth |

---

*Last updated: 2026-06-06*
