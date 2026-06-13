# Future Enhancements & Architectural Backlog (v5.0+)

> Post-v5.0 institutional upgrades. v4.1 backlog: `../../v4.1/docs/FUTURE_ENHANCEMENTS.md`.

## Epic A: Pluggable Alpha Layer

- [ ] **Dynamic Context Truncation ("API Valve"):** Prune `AgentContext` by strategy — intraday/0-DTE gets VIX, PCR, order flow only; drop macro to save tokens and latency.
- [ ] **Multi-Broker Failover (OpenAlgo):** If Fyers HTTP 500 or WebSocket death, route to fallback broker (Dhan / AngelOne) within 3 seconds via adapter pattern.

## Epic B: Smart Order Routing & Slippage

- [ ] **Institutional tranching:** Slice fractionally-sized orders (&gt;1000 Nifty qty) into iceberg tranches.
- [ ] **Dynamic limit-chasing:** Mid-price limit, +1 tick every 2s until fill — avoid blind market crosses on wide spreads.

## Epic C: Autonomous Self-Improvement

- [ ] **Automated prompt mutation:** Agent 6 identifies failure patterns; writes qualitative rules to S3 prompts (HITL before live effect).
- [ ] **Bayesian parameter optimization:** Offline optimizer on OCI backtests → optimal PCR/VIX thresholds → clamped `risk_config.json` proposal.

## Epic D: Observability & Ops

- [ ] **Serverless trace viewer CLI:** Lambda query of `A2A_Traces` by session → human-readable decision chain.
- [ ] **S3 Object Lock audit trail:** Immutable tick archive for SEBI compliance.
- [ ] **CloudWatch + Telegram halt alerts:** Operator push on circuit breaker / execution_halted.

## Epic E: Quant Lab (OCI Free Tier)

- [ ] **Air-gapped read-only pipeline:** OCI pulls S3 Parquet traces; no inbound to live EC2.
- [ ] **Offloaded Agents 6/7 clustering:** Heavy trace analysis off Lambda onto 24GB ARM VM.
- [ ] **Local LLM macro scrape (Ollama):** Agent 0 token cost reduction.
- [ ] **Equity swing strategy miner:** VectorBT on Nifty 500; human promotion to Python matrix only.

## Epic F: Auto-Dev Pipeline (Northstar)

- [ ] **Self-healing PR flow:** EC2 traceback → GitHub Issue → agent patch + pytest → human merge → CodeDeploy.

---

When proposing features in code review, cross-check `PIVOT_DECISION.md` Tier A/B/C sequencing — do not skip gates.
