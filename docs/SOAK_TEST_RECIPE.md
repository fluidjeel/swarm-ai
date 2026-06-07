# Paper Soak Test Recipe (v4.1)

## Pre-flight checklist

1. Confirm branch and tests on EC2:
   `cd ~/swarm-ai && git pull && pytest tests/ -q`
2. Confirm Fyers token is fresh (laptop browser auth + SSM sync):
   `python scripts/fyers_authenticate.py`
3. Confirm SSM parameters exist:
   `aws ssm get-parameter --name /a2a/llm/FYERS_APP_ID --with-decryption --query Parameter.Name`
4. Confirm trading day (NSE weekday, not holiday):
   `python -c "from src.orchestration.session_clock import is_trading_day; from datetime import datetime, timezone; from src.orchestration.session_clock import IST; print(is_trading_day(datetime.now(IST)))"`
5. Confirm no stale paper tick lock:
   `rm -f /var/lock/a2a-paper-tick.lock`

## How to start the soak

1. SSH to EC2:
   `ssh ubuntu@<ec2-public-ip>`
2. Open a tmux session named `soak`:
   `tmux new -s soak`
3. Activate venv and start paper harness (4h, 5m cadence):
   `cd ~/swarm-ai && source .venv/bin/activate && python -m src.orchestration.paper_mode --hours 4 --tick-seconds 300`
4. Detach from tmux:
   `Ctrl-b d`
5. Note session id from first JSONL line or log filename:
   `ls -t logs/paper_soak/*.jsonl | head -1`

## What to monitor every 30 minutes

1. Tail latest tick rows:
   `tail -5 logs/paper_soak/<session_id>.jsonl`
2. Count approve vs reject ratio:
   `grep -c PAPER_APPROVE logs/paper_soak/<session_id>.jsonl; grep -c paper_tick_error logs/paper_soak/<session_id>.jsonl`
3. Check broker errors:
   `grep paper_tick_error logs/paper_soak/<session_id>.jsonl | tail -3`
4. Confirm process still running:
   `tmux attach -t soak` then `Ctrl-b d` to detach

## When to abort the soak

1. Broker error rate spikes (>5 errors in 30 min):
   `grep -c paper_tick_error logs/paper_soak/<session_id>.jsonl`
2. Gatekeeper 100% reject for 6+ consecutive ticks:
   `grep paper_tick logs/paper_soak/<session_id>.jsonl | tail -6`
3. Pipeline crash / Python traceback in tmux pane — stop and capture logs
4. Abort command inside tmux:
   `Ctrl-c`

## How to read the results

1. Print soak summary row:
   `grep paper_soak_complete logs/paper_soak/<session_id>.jsonl`
2. Review PAPER_APPROVE count (expect 0–3 over 4h):
   `grep -c PAPER_APPROVE logs/paper_soak/<session_id>.jsonl`
3. Review PAPER_EXIT count (one per flatten, no sticky repeats):
   `grep -c PAPER_EXIT logs/paper_soak/<session_id>.jsonl`
4. Review PAPER_ORDER_ACK rows (port contract exercised):
   `grep -c PAPER_ORDER_ACK logs/paper_soak/<session_id>.jsonl`
5. Archive log for analysis:
   `cp logs/paper_soak/<session_id>.jsonl ~/soak-archive/`

## Promoting soak → live (the exact gate)

1. Soak completed 4h without crash — `paper_soak_complete` row present
2. Gatekeeper reject rate < 30% — manual review of `paper_tick` rows
3. Broker error rate < 1% — `paper_tick_error` count / total ticks
4. `FyersExecutionPort` implemented and wired (Phase 4.2) — not NoOp
5. Human sign-off recorded in ops log before enabling `dry_run=False` with real port
