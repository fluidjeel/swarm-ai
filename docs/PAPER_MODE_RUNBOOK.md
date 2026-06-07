# Paper Mode Runbook (Step 4.6)

Paper mode runs the v4.1 deterministic pipeline against **live Fyers market data** with **no order placement**. Use it for a mandatory 4-hour soak before Phase 4 live trading.

## Pre-flight checklist

1. **Fyers credentials** — `FYERS_APP_ID` and `FYERS_ACCESS_TOKEN` in `.env` or SSM (`/a2a/llm/FYERS_*` or `A2A_FYERS_SSM_PREFIX`). Generate on laptop: `python scripts/fyers_authenticate.py` (auto-syncs local `.env` + SSM; add `--sync-ec2-ssh` for EC2 `.env`).
2. **Token freshness** — Fyers access tokens expire daily. Re-auth before the soak if the token is from a prior session.
3. **Trading day** — Run on an NSE weekday that is not a holiday (`session_clock.is_trading_day`).
4. **Session phase** — Bootstrap runs during INTRADAY or SQUARE_OFF (09:30–15:20 IST).
5. **No parallel runs** — Do not run paper mode and production on the same broker account simultaneously.
6. **Tick lock** — Paper mode uses `/var/lock/a2a-paper-tick.lock` (or `%TEMP%\a2a-paper-tick.lock` on Windows). Ensure no stale lock from a crashed run.

## Launch

```bash
# From repo root with venv active
python -m src.orchestration.paper_mode

# Optional overrides
PAPER_TICK_SECONDS=300 PAPER_SOAK_HOURS=4 python -m src.orchestration.paper_mode
python -m src.orchestration.paper_mode --hours 8 --tick-seconds 300
```

Environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `PAPER_TICK_SECONDS` | `300` | Seconds between ticks |
| `PAPER_SOAK_HOURS` | `4` | Minimum soak duration |
| `PAPER_SESSION_ID` | auto UUID | Log file name |
| `PAPER_LOG_DIR` | `logs/paper_soak` | JSONL output directory |
| `PAPER_TICK_LOCK_PATH` | `/var/lock/a2a-paper-tick.lock` | Paper tick lock path |

## Log file

Each session writes `logs/paper_soak/<session_id>.jsonl`.

### Row types

| `event` | Meaning |
|---------|---------|
| `paper_tick` | One 5-minute pipeline tick (regime, strategy, critic, gatekeeper, stale distance) |
| `PAPER_APPROVE` | Gatekeeper approved — **would-have-traded** (no order placed) |
| `PAPER_EXIT` | Exit engine fired EXIT_MARKET — **would-have-flattened** (no order placed) |
| `paper_tick_error` | Pipeline/broker error on a tick |
| `paper_soak_complete` | Final summary row |

There is **no** `ORDER_` prefix in paper mode. `PAPER_APPROVE` is the only approve signal.

### Example `paper_tick` row

```json
{
  "event": "paper_tick",
  "session_id": "paper-abc123",
  "timestamp": "2026-06-07T09:30:00+05:30",
  "tick_number": 1,
  "phase": "intraday",
  "regime_decision": "RANGE",
  "strategy_decision": "iron_condor",
  "critic_decision": {"status": "APPROVE", "reason": "math_checks_passed"},
  "gatekeeper_decision": {"verdict": "APPROVE", "rule_id": null, "expected_round_trip_cost": 40.0},
  "open_position": null,
  "baseline_initialized": true,
  "feature_snapshot_price": 24850.5,
  "stale_quote_distance": 1.2,
  "elapsed_ms": 142.3
}
```

## What to look for

- **High critic reject rate** — threshold or strike-selection issues; document in `.context/06_pending_fixes.md`, do not hot-tune in paper mode.
- **High gatekeeper reject rate** — risk limits firing as designed.
- **`stale_quote_distance` > 10** — should correlate with `stale_quote_abort` critic rejects.
- **`paper_tick_error` / broker errors** — auth expiry, rate limits, 5xx. Target: **< 1% of ticks**.
- **`PAPER_APPROVE` count** — how many entries the bot would have taken.
- **Tick `elapsed_ms`** — multi-leg iron condor ≈ 6 broker calls per tick; watch for timeouts.

## Halt and resume

- **Ctrl-C** — stops the soak cleanly, releases the paper tick lock, writes `paper_soak_complete`.
- **Resume** — start a new session with a fresh `PAPER_SESSION_ID`; prior JSONL is append-only per session file.
- **Stale lock** — delete the lock file only if no paper-mode process is running.

## Rollback

There is no automated rollback. If paper mode reveals a critical issue:

1. Stop paper mode (Ctrl-C).
2. `git revert` or checkout the last known-good commit.
3. Redeploy to EC2 manually.
4. Document the finding in `.context/06_pending_fixes.md`.

## Success criteria (4h soak)

- No crashes for the full duration.
- Broker error rate < 1% of ticks.
- Summary row printed and logged with approve/reject breakdown.
- Operator can review JSONL and explain what the bot would have done each tick.
