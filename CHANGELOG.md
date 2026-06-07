# Changelog

## 2026-06-07 — Phase 4.1 ExecutionPort + Fyers auth sync

- Add `src/execution/` — `ExecutionPort`, `MockExecutionPort`, `NoOpExecutionPort`, `idem_key`
- Inject `execution_port` into `SessionPipeline`; log `PAPER_ORDER_ACK` on dry-run approve
- Add `tests/test_execution_port.py` (idempotency, fail-closed, health latency)
- Add `docs/SOAK_TEST_RECIPE.md`
- Enhance `scripts/fyers_authenticate.py` — sync local `.env`, AWS SSM, optional EC2 SSH
- 223 tests pass

## 2026-06-07 — Step 4.4 defensive fence

- Add StrategyName StrEnum to src/core/context.py (4 values)
- Tighten StrategyDecision.strategy and OpenPosition.strategy to enum
- Delete short_strangle/short_straddle from strategy_registry.py
- Delete nifty_futures_* from strategy_registry.py
- Delete FUTURES_STRATEGIES / RANGE_SHORT_VOL_STRATEGIES /
  NAKED_SHORT_STRATEGIES frozensets from gatekeeper.py
- Delete FUTURES_STRATEGIES / CREDIT_SPREAD_STRATEGIES frozensets
  from exit_engine.py
- Centralize policy comment in strategy_selector.py
- 215+ tests pass; the engine is now structurally incapable of
  selecting naked or futures strategies

## 2026-06-07 — Step 4.6 paper-mode soak

- Add `src/orchestration/paper_mode.py` — 4h live-data dry-run loop with JSONL logging
- Add `SessionPipeline.dry_run` — `PAPER_APPROVE` / `PAPER_EXIT` observability, no position mutation
- Add `get_fyers_credentials()` in `src/config/secrets.py`
- Add `docs/PAPER_MODE_RUNBOOK.md`

## 2026-06-07 — Step 4.3 housekeeping

- Move `IRON_CONDOR_WING_WIDTH` from module constant to `RiskConfig.wing_width_points`
- Integrate NSE holiday list into `_weekly_expiry_timestamps`
- Update `.cursorrules` to reflect v4.1 Prime Directive
- Archive deprecated LLM prompts to `prompts/archive/`
