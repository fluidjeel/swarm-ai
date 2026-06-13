# Cloud Architecture: A2A v5.0

> v4.1 (5-min REST cron) spec: `../v4.1/05_cloud_architect.md`

## 1. Design Philosophy

Low-latency hybrid: **sub-second data** for premium stops and breakouts; **zero LLM** in intraday hot path.

**Shift from v4.1:** EventBridge 5-min cron → **continuous asyncio WebSocket daemon** during market hours (09:15–15:30 IST).

## 2. AWS Topology (ap-south-1)

```
VPC
 ├── EC2 t3.small (Ubuntu 24.04)
 │     └── WebSocket daemon: FeatureEngine, SessionPipeline, Gatekeeper, ExitEngine
 ├── DynamoDB (A2A_Traces — telemetry + locks)
 ├── S3 (a2a-configs: risk_config, absolute_limits, prompts)
 ├── Lambda (Agents 0, 6, 7, Telegram webhooks)
 └── EventBridge (08:30 start EC2, 16:00 stop EC2)
```

## 3. Component Spec

| Component | Role |
|-----------|------|
| **EC2** | Continuous daemon; systemd/pm2 auto-restart; 2GB RAM budget — no pandas bloat |
| **EventBridge** | Alarm clock only (boot/stop); not tick scheduler |
| **DynamoDB** | Traces + optional tick lock; Expectancy Controller input |
| **S3** | Config + prompts; Agent 7 writes throttled patches |
| **Lambda** | Agent 0 scout, Agent 6 analyzer, Agent 7 expectancy controller |

## 4. Intraday Workflow (WebSocket)

| Time (IST) | Action |
|------------|--------|
| 08:00 | Lambda Agent 0 → overnight context |
| 08:30 | EventBridge starts EC2 |
| 09:00 | Fyers auth; WebSocket connect |
| 09:15–15:30 | Stream ticks → in-memory chain → exit eval on every material update → entry eval on regime signal |
| 15:30 | Graceful socket close; flush JSONL / DDB |
| Post-market | Agent 6/7 Lambda |

## 5. Resilience

- **Broker source of truth:** GET /positions on boot; never trust DDB for live state.
- **Idempotency:** orderTag + orderbook re-query on 502/504.
- **WebSocket reconnect:** exponential backoff; fail-closed if degraded &gt; N seconds.
- **RAM watchdog:** psutil &gt; 85% → halt new entries.

## 6. Cost Estimate (~unchanged from v4.1)

| Item | ~Monthly |
|------|----------|
| EC2 t3.small + EIP | $16 |
| DynamoDB on-demand | $2 |
| S3 + Lambda + EventBridge | &lt; $1 |
| LLM (Agents 0/6/7 only) | ~$1.50 |
| **Total** | **~$20.50** |

## 7. Migration Note

Until Epic 3 ships, **production runs v4.1 REST** on the same EC2. WebSocket daemon replaces the polling loop — it does not run in parallel without explicit feature flag.
