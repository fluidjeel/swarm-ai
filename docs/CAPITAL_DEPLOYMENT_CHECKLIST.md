# Capital Deployment Checklist (v4.1 — mock / human steps)

**Gate:** Complete only after NoOp + `--broker` paper soaks pass (`scripts/validate_soak_log.py`).

## Allocation (₹6L total)

| Bucket | Amount | Instrument | Notes |
|--------|--------|------------|-------|
| Reserve | ₹2.5L | Liquid BeES | Margin buffer; not traded by engine |
| Trading | ₹3.5L | NIFTY options margin | 1-lot iron condor / verticals only |

## Pre-live (human)

1. Fyers account funded; MIS/MARGIN product permissions verified.
2. `python scripts/fyers_authenticate.py` — token fresh before 09:30 IST.
3. EC2 `git pull`; `pytest tests/ -q` green.
4. Soak logs archived; `PAPER_APPROVE` rate reviewed.
5. Sign-off row in ops notes (date, session_id, approve count, error rate).

## Go-live (human — not automated in v4.1)

1. Remove `--mock`; do **not** use `dry_run=False` until explicit Phase 4.3 PR.
2. Start with **1 lot**; daily circuit `-₹8,000` enforced in gatekeeper.
3. Monitor `logs/heartbeat.jsonl` every 30 min during first session.
4. Abort if `execution_halted` or `MEMORY_GUARD` events appear.

## Rollback

1. `Ctrl-c` paper/live process; clear `/var/lock/a2a-paper-tick.lock`.
2. Flatten open positions manually in Fyers if `execution_halted` during exit.
3. Capture JSONL + `logs/traces/` for post-mortem.
