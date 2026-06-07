# Paper Soak Test Recipe (v4.1)

Run on **NSE weekdays only** during live market hours. All times below are **IST (Asia/Kolkata)**.

Session phases (from `session_clock.py`):

| Phase | IST window | Paper soak |
|-------|------------|------------|
| PRE_OPEN | before 09:00 | Do not start — bootstrap will refuse |
| OPENING | 09:00–09:30 | Do not start — bootstrap will refuse |
| INTRADAY | 09:30–14:30 | **Primary window** — entries allowed |
| NO_NEW_ENTRY | 14:30–15:10 | Ticks run; new entries unlikely by design |
| SQUARE_OFF | 15:10–15:20 | Bootstrap allowed; flatten-only behaviour |
| CLOSED | after 15:20 | Stop soak; no meaningful intraday data |

---

## Monday morning chronology (recommended)

Use this as a **single-session playbook**: 30-min smoke first, then 4h soak only if smoke passes.

### 08:00–08:45 — Laptop pre-flight (no market data required)

Do this from your dev machine before SSH.

1. Pull latest code and confirm tests:
   ```bash
   cd ~/swarm-ai   # or C:\Manasjit\ai\swarm-ai on Windows
   git pull
   python -m pytest tests/ -q
   ```
   **Gate:** 257+ tests green.

2. Refresh Fyers token (expires ~03:30 IST daily):
   ```bash
   python scripts/fyers_authenticate.py
   ```
   Syncs local `.env` and SSM. If EC2 runs the soak, add `--sync-ec2-ssh` or copy creds manually.

3. Confirm today is a trading day:
   ```bash
   python -c "from datetime import datetime; from src.orchestration.session_clock import IST, is_trading_day; print(is_trading_day(datetime.now(IST)))"
   ```
   **Gate:** prints `True`. If `False`, stop — holiday or weekend.

4. *(Optional)* Spot-check local Greeks against NSE website once chain is live (after 09:15):
   ```bash
   python scripts/spotcheck_greeks.py --spot <LTP> --strike 18200 --type CE --ltp ... --bid ... --ask ... --dte <DTE>
   ```

### 08:45–09:20 — EC2 / runtime prep

1. SSH to EC2 (or open terminal on the soak host):
   ```bash
   ssh ubuntu@<ec2-public-ip>
   ```

2. Sync code and venv on the host:
   ```bash
   cd ~/swarm-ai && git pull && source .venv/bin/activate
   python -m pytest tests/ -q
   ```

3. Verify SSM / env (EC2 only):
   ```bash
   aws ssm get-parameter --name /a2a/llm/FYERS_APP_ID --with-decryption --query Parameter.Name
   python -c "from src.config.secrets import get_fyers_credentials; get_fyers_credentials(); print('ok')"
   ```

4. Clear stale tick lock:
   ```bash
   rm -f /var/lock/a2a-paper-tick.lock
   ```
   Windows (local soak): delete `%TEMP%\a2a-paper-tick.lock`.

5. Create log archive dir:
   ```bash
   mkdir -p logs/paper_soak ~/soak-archive
   ```

### 09:20–09:28 — tmux setup (before market open)

1. Start named session:
   ```bash
   tmux new -s soak
   ```

2. Activate venv inside tmux:
   ```bash
   cd ~/swarm-ai && source .venv/bin/activate
   ```

3. **Do not start paper_mode yet** — wait until **09:30 IST** so `bootstrap_session` runs in INTRADAY.

### 09:30 — Phase A: 30-minute smoke (Strike 1 validation)

**Purpose:** Confirm `open_position` blocks re-entry; expect **≤1 `PAPER_APPROVE` per position**, not ~6/hour.

```bash
python -m src.orchestration.paper_mode --hours 0.5 --tick-seconds 300
```

- **Duration:** 30 minutes (~6 ticks at 5-min cadence).
- **Detach:** `Ctrl-b d` (leave tmux running).

### 09:55–10:00 — Smoke review (go / no-go)

Re-attach briefly: `tmux attach -t soak`

```bash
SESSION=$(ls -t logs/paper_soak/*.jsonl | head -1)
echo "Log: $SESSION"
grep -c PAPER_APPROVE "$SESSION"
grep -c PAPER_ORDER_ACK "$SESSION"
grep -c paper_tick_error "$SESSION"
grep -c PAPER_EXIT "$SESSION"
tail -8 "$SESSION"
```

**Smoke PASS criteria (all must hold):**

| Check | Pass |
|-------|------|
| Process alive | No Python traceback in tmux |
| `paper_tick_error` | 0 (or explainable single Fyers blip) |
| `PAPER_APPROVE` | 0 or 1 for the 30-min window |
| Re-entry blocked | If `PAPER_APPROVE` = 1, later ticks show no second approve |
| `PAPER_ORDER_ACK` | 4 per approve (iron condor legs) if approve occurred |

**If smoke FAIL:** `Ctrl-c` in tmux, capture log, fix before 4h soak. Do not proceed.

**If smoke PASS:** `Ctrl-c` to end smoke (or let it finish), clear lock if needed:
```bash
rm -f /var/lock/a2a-paper-tick.lock
```

### 10:00 — Phase B: 4-hour soak (NoOp / live Fyers quotes)

Start the full soak **by 10:00** so most ticks land in INTRADAY (ends ~14:00).

```bash
python -m src.orchestration.paper_mode --hours 4 --tick-seconds 300
```

Note `session_id` from filename:
```bash
ls -t logs/paper_soak/*.jsonl | head -1
```

Detach: `Ctrl-b d`

### 10:30, 11:00, 11:30, 12:00, 12:30, 13:00, 13:30 — Monitor (every 30 min)

```bash
SESSION=logs/paper_soak/<session_id>.jsonl
tail -5 "$SESSION"
grep -c PAPER_APPROVE "$SESSION"; grep -c paper_tick_error "$SESSION"
grep paper_tick_error "$SESSION" | tail -3
```

Confirm tmux still running: `tmux ls`

### ~14:00 — Soak completes

Re-attach; wait for `paper_soak_complete` row and printed summary.

```bash
grep paper_soak_complete "$SESSION"
```

Archive:
```bash
cp "$SESSION" ~/soak-archive/$(basename "$SESSION")
```

### 14:00–14:30 — Post-soak review (same day)

```bash
grep -c PAPER_APPROVE "$SESSION"      # expect 0–3 over 4h
grep -c PAPER_EXIT "$SESSION"
grep -c PAPER_ORDER_ACK "$SESSION"
grep -c paper_tick_error "$SESSION"
grep paper_tick "$SESSION" | tail -20
```

Record in ops notes: approve count, reject reasons, broker error rate, any `execution_halted` ticks.

### After Monday (NoOp soak complete)

- Target **20+ hours** of paper data across multiple trading days before threshold tuning.
- **Phase C — broker-exercising soak** (only after NoOp smoke + 4h pass):

```bash
# WARNING: places real Fyers orders (1-lot). Use dedicated paper account.
python -m src.orchestration.paper_mode --hours 4 --tick-seconds 300 --broker
```

`--broker` wires `FyersExecutionPort` + per-tick `get_positions()` reconcile. JSONL gains `BROKER_POSITION_SYNC` rows when broker state diverges from memory.

- **Do not go live** until broker-exercising soak is clean and a human signs off.

---

## Offline validation (no market — run anytime)

```bash
python -m pytest tests/ -q
python -m src.orchestration.paper_mode --mock
python scripts/validate_soak_log.py logs/paper_soak/mock-*.jsonl --smoke
```

---

## Quick reference — commands only

### Pre-flight checklist

1. Branch + tests: `cd ~/swarm-ai && git pull && pytest tests/ -q`
2. Fyers token: `python scripts/fyers_authenticate.py`
3. SSM (EC2): `aws ssm get-parameter --name /a2a/llm/FYERS_APP_ID --with-decryption --query Parameter.Name`
4. Trading day: `python -c "from datetime import datetime; from src.orchestration.session_clock import IST, is_trading_day; print(is_trading_day(datetime.now(IST)))"`
5. Clear lock: `rm -f /var/lock/a2a-paper-tick.lock` (Linux) or `%TEMP%\a2a-paper-tick.lock` (Windows)

### Start soak (generic)

```bash
tmux new -s soak
cd ~/swarm-ai && source .venv/bin/activate
python -m src.orchestration.paper_mode --hours 4 --tick-seconds 300
# Ctrl-b d
```

### When to abort

1. `paper_tick_error` > 5 in 30 minutes
2. Gatekeeper 100% reject for 6+ consecutive ticks
3. Python traceback in tmux — `Ctrl-c`, save log

### How to read results

| Metric | 4h expectation |
|--------|----------------|
| `PAPER_APPROVE` | 0–3 total |
| `PAPER_EXIT` | ≤ approve count (one per flatten) |
| `PAPER_ORDER_ACK` | 4 × approve count (IC legs) |
| `paper_tick_error` | < 1% of ticks |
| `paper_soak_complete` | Must be present |

### Promoting soak → live (hard gate)

1. 4h completed without crash
2. Gatekeeper reject rate < 30% (manual review)
3. Broker error rate < 1%
4. Broker-exercising soak with `--broker` completed cleanly
5. Human sign-off before `dry_run=False`

See also: [PAPER_MODE_RUNBOOK.md](PAPER_MODE_RUNBOOK.md)
